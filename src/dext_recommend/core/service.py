# src/dext_recommend/core/service.py
"""RecommendRequest -> RecommendResponse orchestrator.

Snapshot pinned once at entry; threaded through every data port. Pipeline:
snapshot -> route -> profile -> query understanding -> embedding -> recall
loop -> filters -> detail fan-out -> rerank -> cards -> validation.
"""
from __future__ import annotations

import dataclasses
import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from dext_recommend.config import RecommendSettings
from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import (
    QueryDiagnostics, QueryUnderstanding, RecommendRequest, RecommendResponse,
    RecommendationFilters, RecommendationWarning,
)
from dext_recommend.ports import (
    ActiveSnapshotProvider, LLMGenerationPort, ProfessorFactPort,
    QueryEmbeddingPort, RankingProfilePort, VectorSearchPort, ViewerPermissions,
)
from dext_recommend.readiness import ActiveBuildSnapshot

from dext_recommend.core._resilience import (
    ClassifiedRecommendError, RecommendExecutionContext,
    _guarded_async, _guarded_sync,
)
from dext_recommend.core.cards import assemble_card
from dext_recommend.core.detail_fetch import fetch_details
from dext_recommend.core.explanation import build_explanation
from dext_recommend.core.intent import resolve_recommend_route
from dext_recommend.core.query_understanding import understand_query
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.recall import normalize_rrf, recall_loop
from dext_recommend.core.rerank import rerank
from dext_recommend.core.validation import make_error_response, validate


@dataclass(frozen=True, slots=True)
class RecommendDeps:
    snapshot_port: ActiveSnapshotProvider
    embedding_port: QueryEmbeddingPort
    vector_port: VectorSearchPort
    facts_port: ProfessorFactPort
    llm_port: LLMGenerationPort
    ranking_port: RankingProfilePort
    coverage_flags_by_build_id: Mapping[str, Mapping[str, bool]] = field(default_factory=dict)


def _warn(code: RecommendationErrorCode, message: str, *, severity: str = "warning") -> RecommendationWarning:
    return RecommendationWarning(code=code.value, message=message, severity=severity)


def _error_response(*, snapshot: ActiveBuildSnapshot | None, profile: RankingProfile | None,
                    embedding_fingerprint: str | None, warning: RecommendationWarning,
                    phase_diagnostics: tuple = (),
                    prior_warnings: tuple[RecommendationWarning, ...] = ()) -> RecommendResponse:
    return make_error_response(
        build_id=snapshot.build_id if snapshot else "unavailable",
        ranking_profile_version=profile.version if profile else (
            snapshot.ranking_profile_version if snapshot else "unavailable"
        ),
        embedding_fingerprint=embedding_fingerprint or (
            snapshot.embedding_fingerprint if snapshot else "unavailable"
        ),
        taxonomy_version=snapshot.taxonomy_version if snapshot else None,
        warning=warning,
        phase_diagnostics=phase_diagnostics,
        prior_warnings=prior_warnings,
    )


def validate_request(request: RecommendRequest, settings: RecommendSettings) -> str | None:
    """Return an error message string if invalid, else None.

    This is the admission boundary for RecommendRequest; it must reject
    malformed input (wrong types included) with a clear message rather than
    letting downstream comparisons crash with TypeError/AttributeError.
    """
    if not isinstance(request.query_text, str) or not request.query_text.strip():
        return "query_text must be a non-empty string"
    if not isinstance(request.limit, int) or isinstance(request.limit, bool):
        return "limit must be an int"
    if not isinstance(request.oversample, int) or isinstance(request.oversample, bool):
        return "oversample must be an int"
    if len(request.query_text) > settings.query_max_chars:
        return f"query_text exceeds {settings.query_max_chars} chars"
    if not (1 <= request.limit <= settings.limit_max):
        return f"limit must be in [1, {settings.limit_max}]"
    if not (1 <= request.oversample <= settings.oversample_max):
        return f"oversample must be in [1, {settings.oversample_max}]"
    if not isinstance(request.ranking_mode, str):
        return "ranking_mode must be a string"
    if request.ranking_mode != "explainable_precision":
        return f"ranking_mode {request.ranking_mode!r} not supported"
    if not isinstance(request.review_policy, str):
        return "review_policy must be a string"
    if request.review_policy not in ("exclude", "include_downranked"):
        return f"invalid review_policy: {request.review_policy!r}"
    if not isinstance(request.diagnostics_level, str):
        return "diagnostics_level must be a string"
    if request.diagnostics_level not in ("none", "summary", "debug"):
        return f"invalid diagnostics_level: {request.diagnostics_level!r}"
    filters = request.filters
    if filters is None or not isinstance(filters, RecommendationFilters):
        return "filters must be a RecommendationFilters instance"
    if filters.master_eligibility not in ("any", "confirmed"):
        return "invalid master_eligibility"
    if filters.phd_eligibility not in ("any", "confirmed"):
        return "invalid phd_eligibility"
    if filters.topic_filter_mode not in ("soft", "hard"):
        return "invalid topic_filter_mode"
    # spec §2.2: topic_filter_mode="hard" with empty topic_ids is not a valid
    # request — R3 defaults to NOT allowing topic hard-filter degraded.
    if filters.topic_filter_mode == "hard" and not filters.topic_ids:
        return "topic_filter_mode=hard requires non-empty topic_ids"
    for fld in ("university_ids", "city_names", "org_unit_ids", "title_families", "topic_ids"):
        for v in getattr(filters, fld):
            if not isinstance(v, str) or not v:
                return f"filters.{fld} contains empty/non-string value"
    return None


class RecommendationCore:
    def __init__(self, deps: RecommendDeps, settings: RecommendSettings) -> None:
        self._deps = deps
        self._settings = settings

    @property
    def deps(self) -> RecommendDeps:
        return self._deps

    async def recommend(
        self,
        request: RecommendRequest,
        *,
        viewer_permissions: ViewerPermissions | None = None,
    ) -> RecommendResponse:
        vp = viewer_permissions or ViewerPermissions()

        err = validate_request(request, self._settings)
        if err is not None:
            resp = _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.INVALID_REQUEST, err, severity="error"),
                phase_diagnostics=(),
            )
            validate(resp)
            return resp

        if request.include_contacts and not vp.include_contacts:
            resp = _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.UNAUTHORIZED_CONTACT,
                              "include_contacts requested without permission", severity="error"),
                phase_diagnostics=(),
            )
            validate(resp)
            return resp
        if request.review_policy == "include_downranked" and not vp.can_view_review:
            resp = _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.UNAUTHORIZED_REVIEW,
                              "include_downranked requested without permission", severity="error"),
                phase_diagnostics=(),
            )
            validate(resp)
            return resp

        ctx = RecommendExecutionContext()
        try:
            return await asyncio.wait_for(
                self._recommend_inner(request, vp, ctx),
                timeout=self._settings.total_timeout,
            )
        except asyncio.TimeoutError:
            return _error_response(
                snapshot=ctx.snapshot, profile=ctx.profile,
                embedding_fingerprint=ctx.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.REQUEST_TIMEOUT,
                              f"recommend exceeded {self._settings.total_timeout}s",
                              severity="error"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
                prior_warnings=ctx.snapshot_warnings(),
            )
        except ClassifiedRecommendError as exc:
            return _error_response(
                snapshot=ctx.snapshot, profile=ctx.profile,
                embedding_fingerprint=ctx.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode(exc.code),
                              f"{exc.phase} failed", severity="error"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
                prior_warnings=ctx.snapshot_warnings(),
            )

    async def _recommend_inner(
        self,
        request: RecommendRequest,
        vp: ViewerPermissions,
        ctx: RecommendExecutionContext,
    ) -> RecommendResponse:
        effective_include_contacts = request.include_contacts and vp.include_contacts

        snapshot = _guarded_sync(
            ctx, "snapshot",
            RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE.value,
            lambda: self._deps.snapshot_port.get_snapshot(),
        )
        if snapshot is None:
            return _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                              "no ACTIVE build", severity="error"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
            )
        ctx.snapshot = snapshot

        route = resolve_recommend_route(request)
        # ctx.warnings is the request-local warning accumulator (spec 3d §2.3);
        # route_warnings aliases it so every emission is visible to the
        # timeout/classified terminal branches that read ctx.snapshot_warnings().
        route_warnings = ctx.warnings
        route_warnings.extend(route.warnings)

        if route.unsupported:
            warning = _warn(RecommendationErrorCode.UNSUPPORTED_FOR_RECOMMEND_CORE,
                            route.unsupported, severity="warning")
            resp = _error_response(
                snapshot=snapshot, profile=None, embedding_fingerprint=None,
                warning=warning,
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
            )
            # merge route warnings ahead of the unsupported warning, then rebuild
            # the frozen+slots dataclass via object.__setattr__ (avoids fragile
            # __dict__ spread on slots).
            all_warnings = tuple(route_warnings) + tuple(resp.warnings)
            resp = RecommendResponse(
                build_id=resp.build_id, ranking_profile_version=resp.ranking_profile_version,
                embedding_fingerprint=resp.embedding_fingerprint, taxonomy_version=resp.taxonomy_version,
                query_understanding=resp.query_understanding, query=resp.query,
                results=resp.results, suggested_followups=resp.suggested_followups,
                warnings=all_warnings,
                phase_diagnostics=resp.phase_diagnostics,
            )
            validate(resp)
            return resp

        profile = await _guarded_async(
            ctx, "ranking_profile",
            RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE.value,
            lambda: self._deps.ranking_port.read_profile(self._settings.ranking_profile_path),
        )
        ctx.profile = profile

        qu = await _guarded_async(
            ctx, "query_understanding",
            RecommendationErrorCode.LLM_UNAVAILABLE.value,
            lambda: understand_query(
                request, self._deps.llm_port, snapshot, profile_version=profile.version,
            ),
        )
        if qu.needs_clarification:
            # build the warning once and reuse it (the brief had a duplicate)
            warning = _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                            "query needs clarification; recall skipped", severity="warning")
            resp = _error_response(
                snapshot=snapshot, profile=profile,
                embedding_fingerprint=snapshot.embedding_fingerprint,
                warning=warning,
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
                prior_warnings=tuple(route_warnings),
            )
            validate(resp)
            return resp

        # spec §6.2: refine_direction merges QU preferred_* into the effective
        # filters used downstream. Explicit request.filters always win.
        effective_filters = _effective_filters(request, qu, route)

        anchor_topics: tuple[str, ...] = ()
        if route.intent == "same_field" and route.anchor_entity_id:
            anchor_map = await _guarded_async(
                ctx, "anchor_hydrate",
                RecommendationErrorCode.HYDRATE_UNAVAILABLE.value,
                lambda: self._deps.facts_port.hydrate(
                    snapshot, [route.anchor_entity_id],
                ),
            )
            anchor_fact = anchor_map.get(route.anchor_entity_id)
            anchor_unavailable = (
                anchor_fact is None
                or anchor_fact.role_status == "excluded"
                or (anchor_fact.role_status == "review"
                    and request.review_policy != "include_downranked")
                or not anchor_fact.topic_ids
            )
            if anchor_unavailable:
                route_warnings.append(_warn(
                    RecommendationErrorCode.MISSING_ANCHOR,
                    "anchor unavailable or lacks approved topics; falling back to new_search",
                ))
                route = dataclasses.replace(
                    route, intent="new_search", anchor_entity_id=None,
                )
            else:
                anchor_topics = tuple(anchor_fact.topic_ids)
                route = dataclasses.replace(
                    route,
                    exclude_entity_ids=tuple(dict.fromkeys(
                        route.exclude_entity_ids + (route.anchor_entity_id,)
                    )),
                )

        embedding = await _guarded_async(
            ctx, "embedding",
            RecommendationErrorCode.EMBEDDING_UNAVAILABLE.value,
            lambda: self._deps.embedding_port.embed(snapshot, request.query_text),
        )
        ctx.embedding_fingerprint = embedding.embedding_fingerprint
        if embedding.embedding_fingerprint != snapshot.embedding_fingerprint:
            return _error_response(
                snapshot=snapshot, profile=profile,
                embedding_fingerprint=embedding.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
                              "embedding fingerprint != snapshot", severity="error"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
                prior_warnings=tuple(route_warnings),
            )

        coverage_flags = self._deps.coverage_flags_by_build_id.get(snapshot.build_id, {})
        recall = await recall_loop(
            snapshot, self._deps.vector_port, list(embedding.vector),
            effective_filters, profile,
            facts_port=self._deps.facts_port,
            route=route, coverage_flags=coverage_flags,
            review_policy=request.review_policy,
            embedding_sparse_vector=embedding.sparse_vector,
            oversample_max=self._settings.oversample_max,
            request_oversample=request.oversample, limit=request.limit,
            ctx=ctx,
        )
        survivors = list(recall.survivors)
        fact_map = dict(recall.fact_map)
        filter_diag = recall.filter_diagnostics
        recall_count = recall.step_diags[-1].raw_hits if recall.step_diags else 0
        steps_used = recall.steps_used

        if not survivors:
            resp = _error_response(
                snapshot=snapshot, profile=profile,
                embedding_fingerprint=embedding.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.NO_CANDIDATES_AFTER_FILTERS,
                              "no candidates after filters", severity="warning"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
                prior_warnings=tuple(route_warnings),
            )
            # attach diagnostics
            diag = QueryDiagnostics(
                query_length=len(request.query_text),
                language_summary=_language_summary(request.query_text),
                filter_summary=_filter_summary(effective_filters),
                recall_count=recall_count, post_filter_count=0, returned_count=0,
                steps_used=steps_used,
            )
            object.__setattr__(resp, "query", diag)
            validate(resp)
            return resp

        semantic_scores = normalize_rrf(survivors)
        query_terms = tuple(qu.research_interests)
        rerank_window = survivors[: profile.detail_rerank_window]
        rerank_window_ids = [h.entity_id for h in rerank_window]
        detail_map, failed = await fetch_details(
            snapshot, self._deps.facts_port, rerank_window_ids,
            include_contacts=effective_include_contacts,
            viewer_permissions=vp,
            concurrency=profile.detail_fetch_concurrency,
            ctx=ctx,
        )
        ranked = rerank(
            rerank_window, fact_map, detail_map, semantic_scores,
            request.student_context, profile, route, query_terms=query_terms,
            anchor_topics=anchor_topics,
        )
        top = ranked[: request.limit]
        results = []
        weak_explanation = False
        for entry in top:
            fact = fact_map.get(entry.entity_id)
            detail = detail_map.get(entry.entity_id)
            expl = build_explanation(entry, fact, detail, query_terms=query_terms)
            if expl.weak_explanation:
                weak_explanation = True
            card = assemble_card(
                entry, fact, detail, expl, qu, include_contacts=effective_include_contacts,
            )
            results.append(card)

        warnings = list(route_warnings)
        if filter_diag.org_unit_degraded and effective_filters.org_unit_ids:
            warnings.append(_warn(RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE,
                                  "org_unit hard filter degraded (coverage unavailable)"))
        if weak_explanation:
            warnings.append(_warn(RecommendationErrorCode.WEAK_EXPLANATION,
                                  "one or more results lack traceable evidence"))
        if len(results) < request.limit:
            warnings.append(_warn(RecommendationErrorCode.NO_CANDIDATES_AFTER_FILTERS,
                                  f"returned {len(results)} < limit {request.limit}"))
        # partial detail degradation: a detail in the rerank window failed for
        # operational reasons (not just missing) -> warn but keep the request.
        detail_failures = [eid for eid in rerank_window_ids if eid in failed]
        if detail_failures:
            warnings.append(_warn(RecommendationErrorCode.DETAILS_UNAVAILABLE,
                                  f"{len(detail_failures)} detail(s) unavailable; degraded"))

        suggested_followups = _suggested_followups(qu, route)
        diag = QueryDiagnostics(
            query_length=len(request.query_text),
            language_summary=_language_summary(request.query_text),
            filter_summary=_filter_summary(effective_filters),
            recall_count=recall_count, post_filter_count=len(survivors),
            returned_count=len(results),
            steps_used=steps_used,
        )
        resp = RecommendResponse(
            build_id=snapshot.build_id, ranking_profile_version=profile.version,
            embedding_fingerprint=embedding.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            query_understanding=qu, query=diag, results=tuple(results),
            suggested_followups=tuple(suggested_followups), warnings=tuple(warnings),
            phase_diagnostics=ctx.snapshot_phase_diagnostics(),
        )
        validate(resp)
        return resp


def _language_summary(text: str) -> str:
    has_cjk = any("一" <= ch <= "鿿" for ch in text)
    has_latin = any(ch.isascii() and ch.isalpha() for ch in text)
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    return "en"


def _filter_summary(filters) -> str:
    parts = []
    if filters.university_ids:
        parts.append(f"university_ids={list(filters.university_ids)}")
    if filters.org_unit_ids:
        parts.append(f"org_unit_ids={list(filters.org_unit_ids)}")
    if filters.city_names:
        parts.append(f"city_names={list(filters.city_names)}")
    if filters.master_eligibility == "confirmed":
        parts.append("master=confirmed")
    if filters.phd_eligibility == "confirmed":
        parts.append("phd=confirmed")
    return ", ".join(parts) or "none"


def _effective_filters(
    request: RecommendRequest, qu: QueryUnderstanding, route,
) -> RecommendationFilters:
    """spec §6.2: refine_direction merges QU preferred_* into the effective
    filters used by recall_loop / payload_prefilter / final_filter. Explicit
    request.filters always win — preferred_* only fill empty slots.
    """
    f = request.filters
    if not getattr(route, "refine_merge", False):
        return f

    university_ids = f.university_ids
    if not university_ids and qu.preferred_universities:
        university_ids = tuple(qu.preferred_universities)

    city_names = f.city_names
    if not city_names and qu.preferred_cities:
        city_names = tuple(qu.preferred_cities)

    org_unit_ids = f.org_unit_ids
    if not org_unit_ids and qu.preferred_org_units:
        org_unit_ids = tuple(qu.preferred_org_units)

    master_eligibility = f.master_eligibility
    phd_eligibility = f.phd_eligibility
    if qu.mentor_eligibility_requirement == "confirmed":
        if master_eligibility == "any":
            master_eligibility = "confirmed"
        if phd_eligibility == "any":
            phd_eligibility = "confirmed"

    return RecommendationFilters(
        university_ids=university_ids,
        city_names=city_names,
        org_unit_ids=org_unit_ids,
        title_families=f.title_families,
        master_eligibility=master_eligibility,
        phd_eligibility=phd_eligibility,
        topic_ids=f.topic_ids,
        topic_filter_mode=f.topic_filter_mode,
    )



def _suggested_followups(qu, route) -> list[str]:
    out = []
    if qu.missing_information:
        out.append(f"补充信息: {', '.join(qu.missing_information[:2])}")
    if not qu.preferred_universities:
        out.append("补充学校偏好")
    if not qu.degree_goal:
        out.append("明确升学阶段")
    return out[:3]


__all__ = ["RecommendDeps", "RecommendationCore"]
