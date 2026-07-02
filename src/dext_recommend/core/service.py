# src/dext_recommend/core/service.py
"""RecommendRequest -> RecommendResponse orchestrator.

Snapshot pinned once at entry; threaded through every data port. Pipeline:
snapshot -> route -> profile -> query understanding -> embedding -> recall
loop -> filters -> detail fan-out -> rerank -> cards -> validation.
"""
from __future__ import annotations

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

from dext_recommend.core.cards import assemble_card
from dext_recommend.core.detail_fetch import fetch_details
from dext_recommend.core.explanation import build_explanation
from dext_recommend.core.filters import final_filter, payload_prefilter
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
                    embedding_fingerprint: str | None, warning: RecommendationWarning) -> RecommendResponse:
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
    )


class RecommendationCore:
    def __init__(self, deps: RecommendDeps, settings: RecommendSettings) -> None:
        self._deps = deps
        self._settings = settings

    @property
    def deps(self) -> RecommendDeps:
        return self._deps

    async def recommend(self, request: RecommendRequest) -> RecommendResponse:
        snapshot = self._deps.snapshot_port.get_snapshot()
        if snapshot is None:
            return _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                              "no ACTIVE build", severity="error"),
            )

        route = resolve_recommend_route(request)
        route_warnings = list(route.warnings)

        if route.unsupported:
            warning = _warn(RecommendationErrorCode.UNSUPPORTED_FOR_RECOMMEND_CORE,
                            route.unsupported, severity="warning")
            resp = _error_response(
                snapshot=snapshot, profile=None, embedding_fingerprint=None,
                warning=warning,
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
            )
            validate(resp)
            return resp

        try:
            profile = await self._deps.ranking_port.read_profile(self._settings.ranking_profile_path)
        except Exception:
            return _error_response(
                snapshot=snapshot, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE,
                              "ranking profile unavailable", severity="error"),
            )

        qu = await understand_query(
            request, self._deps.llm_port, snapshot, profile_version=profile.version,
        )
        if qu.needs_clarification:
            # build the warning once and reuse it (the brief had a duplicate)
            warning = _warn(RecommendationErrorCode.NEEDS_CLARIFICATION,
                            "query needs clarification; recall skipped", severity="warning")
            resp = _error_response(
                snapshot=snapshot, profile=profile,
                embedding_fingerprint=snapshot.embedding_fingerprint,
                warning=warning,
            )
            validate(resp)
            return resp

        # spec §6.2: refine_direction merges QU preferred_* into the effective
        # filters used downstream. Explicit request.filters always win.
        effective_filters = _effective_filters(request, qu, route)

        embedding = await self._deps.embedding_port.embed(snapshot, request.query_text)
        if embedding.embedding_fingerprint != snapshot.embedding_fingerprint:
            return _error_response(
                snapshot=snapshot, profile=profile,
                embedding_fingerprint=embedding.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
                              "embedding fingerprint != snapshot", severity="error"),
            )

        coverage_flags = self._deps.coverage_flags_by_build_id.get(snapshot.build_id, {})
        org_unit_degraded = coverage_flags.get("org_unit_ids") is not True
        hits_pool, steps_used = await recall_loop(
            snapshot, self._deps.vector_port, list(embedding.vector),
            effective_filters, profile,
            oversample_max=self._settings.oversample_max,
            request_oversample=request.oversample, limit=request.limit,
        )
        # final filter is applied per-step in service (hydrated facts needed)
        # Simpler: do one final filter on the largest pool (the last step's hits)
        prefiltered = payload_prefilter(hits_pool, effective_filters, org_unit_degraded=org_unit_degraded)
        fact_map = await self._deps.facts_port.hydrate(
            snapshot, [h.entity_id for h in prefiltered],
        )
        review_policy = request.review_policy
        survivors, filter_diag = final_filter(
            prefiltered, fact_map, effective_filters, route, coverage_flags,
            review_policy=review_policy,
        )
        recall_count = len(hits_pool)

        if not survivors:
            resp = _error_response(
                snapshot=snapshot, profile=profile,
                embedding_fingerprint=embedding.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.NO_CANDIDATES_AFTER_FILTERS,
                              "no candidates after filters", severity="warning"),
            )
            # attach diagnostics
            diag = QueryDiagnostics(
                query_length=len(request.query_text),
                language_summary=_language_summary(request.query_text),
                filter_summary=_filter_summary(effective_filters),
                recall_count=recall_count, post_filter_count=0, returned_count=0,
            )
            object.__setattr__(resp, "query", diag)
            validate(resp)
            return resp

        semantic_scores = normalize_rrf(survivors)
        query_terms = tuple(qu.research_interests)
        rerank_window = survivors[: profile.detail_rerank_window]
        detail_map = await fetch_details(
            snapshot, self._deps.facts_port, [h.entity_id for h in rerank_window],
            include_contacts=request.include_contacts,
            viewer_permissions=ViewerPermissions(
                include_contacts=request.include_contacts,
                diagnostics=request.diagnostics_level == "debug",
            ),
            concurrency=profile.detail_fetch_concurrency,
        )
        ranked = rerank(
            rerank_window, fact_map, detail_map, semantic_scores,
            request.student_context, profile, route, query_terms=query_terms,
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
                entry, fact, detail, expl, qu, include_contacts=request.include_contacts,
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

        suggested_followups = _suggested_followups(qu, route)
        diag = QueryDiagnostics(
            query_length=len(request.query_text),
            language_summary=_language_summary(request.query_text),
            filter_summary=_filter_summary(effective_filters),
            recall_count=recall_count, post_filter_count=len(survivors),
            returned_count=len(results),
        )
        resp = RecommendResponse(
            build_id=snapshot.build_id, ranking_profile_version=profile.version,
            embedding_fingerprint=embedding.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            query_understanding=qu, query=diag, results=tuple(results),
            suggested_followups=tuple(suggested_followups), warnings=tuple(warnings),
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
