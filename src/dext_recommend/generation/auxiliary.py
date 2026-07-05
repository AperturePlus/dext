"""R6 auxiliary grounded generation services for dext_recommend."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from dext_grounded import (
    ConstrainedGenerationPipeline, ContentClass, FactBundle, FactItem,
    StudentContext, load_grounded_rules, trim,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.core.generation_support import (
    has_grounded_fact_claim, map_generation_warnings, validate_fact_index_support,
)
from dext_recommend.core.service import RecommendationCore
from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import (
    AuxiliaryGenerationResult, MatchAnalysis, OutreachDraft, ProfessorComparison,
    RecommendationWarning,
)
from dext_recommend.ports import ProfessorDetail, ViewerPermissions
from dext_recommend.readiness import ActiveBuildSnapshot


logger = logging.getLogger(__name__)

_PROVIDER_EXCEPTION_ROOT_MODULES = {"openai", "httpx", "httpcore"}
_OUTER_TIMEOUT_GRACE_SECONDS = 5.0
_MATCH_DIMENSION_SCORE_KEYS = (
    "research_fit",
    "method_match",
    "location_fit",
    "degree_goal",
    "publication_activity",
)
_PROVIDER_EXCEPTION_NAMES = {
    "APIConnectionError",
    "APIError",
    "APIResponseValidationError",
    "APIStatusError",
    "APITimeoutError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "InternalServerError",
    "NotFoundError",
    "PermissionDeniedError",
    "RateLimitError",
    "UnprocessableEntityError",
}


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            stack.append(cause)
        if context is not None and context is not cause:
            stack.append(context)


def _is_timeout_exception(exc: Exception) -> bool:
    return any(
        isinstance(item, asyncio.TimeoutError) or "Timeout" in type(item).__name__
        for item in _exception_chain(exc)
    )


def _is_provider_exception(exc: Exception) -> bool:
    for item in _exception_chain(exc):
        exc_type = type(item)
        root_module = exc_type.__module__.split(".", 1)[0]
        if (
            root_module in _PROVIDER_EXCEPTION_ROOT_MODULES
            or exc_type.__name__ in _PROVIDER_EXCEPTION_NAMES
        ):
            return True
    return False


def _elapsed_ms(started_at: float) -> float:
    return (time.monotonic() - started_at) * 1000.0


def _warn(
    code: RecommendationErrorCode | str,
    message: str,
    *,
    severity: str = "warning",
) -> RecommendationWarning:
    value = code.value if isinstance(code, RecommendationErrorCode) else code
    return RecommendationWarning(code=value, message=message, severity=severity)


def _error(
    code: RecommendationErrorCode,
    message: str,
    *,
    generation_profile_version: str | None = None,
) -> AuxiliaryGenerationResult:
    return AuxiliaryGenerationResult(
        kind="error",
        issues=(_warn(code, message, severity="error"),),
        generation_profile_version=generation_profile_version,
    )


def _error_from_issues(
    issues: tuple[RecommendationWarning, ...],
    *,
    generation_profile_version: str | None,
) -> AuxiliaryGenerationResult:
    errors = tuple(issue for issue in issues if issue.severity == "error")
    non_errors = tuple(issue for issue in issues if issue.severity != "error")
    return AuxiliaryGenerationResult(
        kind="error",
        issues=errors + non_errors,
        generation_profile_version=generation_profile_version,
    )


def _allowed_detail(detail: ProfessorDetail, vp: ViewerPermissions) -> bool:
    if detail.role_status == "excluded":
        return False
    if detail.role_status == "review" and not vp.can_view_review:
        return False
    return True


def _student_terms(student_context: StudentContext | None) -> tuple[str, ...]:
    if student_context is None:
        return ()
    terms: list[str] = []
    terms.extend(str(v) for v in (student_context.research_interests or ()) if v)
    for value in (
        student_context.education_stage,
        student_context.school,
        student_context.major,
        student_context.achievements_summary,
        student_context.competition_experience_summary,
    ):
        if value:
            terms.append(str(value))
    return tuple(terms)


def _has_student_context(student_context: StudentContext | None) -> bool:
    return bool(_student_terms(student_context)) or bool(
        student_context and (student_context.gpa_bucket or student_context.rank_bucket)
    )


def _student_context_warning(
    student_context: StudentContext | None,
) -> tuple[RecommendationWarning, ...]:
    if _has_student_context(student_context):
        return ()
    return (_warn(
        RecommendationErrorCode.INSUFFICIENT_STUDENT_CONTEXT,
        "student context is missing; output cannot personalize background",
    ),)


def _trim_bundle(bundle: FactBundle, query_terms: Sequence[str]) -> FactBundle:
    return trim(
        bundle,
        token_budget=load_grounded_rules().trim_token_budget,
        query_terms=query_terms,
    )


def _match_dimension_scores(value) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    if set(value) != set(_MATCH_DIMENSION_SCORE_KEYS):
        return None
    scores: dict[str, float] = {}
    for key in _MATCH_DIMENSION_SCORE_KEYS:
        raw = value.get(key)
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            return None
        scores[key] = float(raw)
    return scores


def _ref_identity(ref) -> tuple[str, str, str, str, str | None]:
    return (
        ref.doc_path,
        ref.heading_path,
        ref.chunk_hash,
        ref.quote_or_summary,
        ref.official_url,
    )


def _limit_bundle_facts(bundle: FactBundle, fact_limit: int | None) -> FactBundle:
    if fact_limit is None or len(bundle.facts) <= fact_limit:
        return bundle
    facts = tuple(bundle.facts[:fact_limit])
    used_refs = {
        _ref_identity(ref)
        for item in facts
        for ref in item.source_refs
    }
    refs = tuple(ref for ref in bundle.source_refs if _ref_identity(ref) in used_refs)
    return FactBundle(
        build_id=bundle.build_id,
        subject_id=bundle.subject_id,
        facts=facts,
        source_refs=refs,
    )


def _merge_compare_bundle(details: Sequence[ProfessorDetail]) -> FactBundle:
    facts: list[FactItem] = []
    refs = []
    seen_refs = set()
    for detail in details:
        for item in detail.fact_bundle.facts:
            facts.append(FactItem(
                field=f"{detail.entity_id}:{item.field}",
                value=f"[{detail.entity_id}] {item.value}",
                content_class=item.content_class,
                source_refs=item.source_refs,
            ))
        for ref in detail.fact_bundle.source_refs:
            key = _ref_identity(ref)
            if key not in seen_refs:
                seen_refs.add(key)
                refs.append(ref)
    return FactBundle(
        build_id=details[0].build_id,
        subject_id="compare:" + ",".join(detail.entity_id for detail in details),
        facts=tuple(facts),
        source_refs=tuple(refs),
    )


def _string_tuple(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _string_tuple_mapping(value) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _string_tuple(child) for key, child in value.items()}


def _log_generation_exception(
    exc: Exception,
    *,
    request_id: str | None,
    operation_id: str,
    system_prompt_id: str,
    fact_bundle: FactBundle,
    student_context: StudentContext | None,
    generation_profile_version: str,
    elapsed_ms: float,
    timeout: float,
    outer_timeout: float,
) -> None:
    logger.exception(
        "auxiliary generation failed request_id=%s operation_id=%s "
        "system_prompt_id=%s generation_profile_version=%s fact_count=%s "
        "student_context_present=%s elapsed_ms=%.1f timeout=%.1f "
        "outer_timeout=%.1f error_type=%s",
        request_id or "-",
        operation_id,
        system_prompt_id,
        generation_profile_version,
        len(fact_bundle.facts),
        _has_student_context(student_context),
        elapsed_ms,
        timeout,
        outer_timeout,
        type(exc).__name__,
    )


def _log_generation_success(
    *,
    request_id: str | None,
    operation_id: str,
    system_prompt_id: str,
    fact_bundle: FactBundle,
    student_context: StudentContext | None,
    generation_profile_version: str,
    elapsed_ms: float,
    timeout: float,
    outer_timeout: float,
    warning_count: int,
) -> None:
    logger.info(
        "auxiliary generation completed request_id=%s operation_id=%s "
        "system_prompt_id=%s generation_profile_version=%s fact_count=%s "
        "student_context_present=%s elapsed_ms=%.1f timeout=%.1f "
        "outer_timeout=%.1f warning_count=%s",
        request_id or "-",
        operation_id,
        system_prompt_id,
        generation_profile_version,
        len(fact_bundle.facts),
        _has_student_context(student_context),
        elapsed_ms,
        timeout,
        outer_timeout,
        warning_count,
    )


class AuxiliaryGenerationService:
    """Process-local R6 service; HTTP/application-state wiring lands in R7."""

    def __init__(
        self,
        *,
        core: RecommendationCore,
        pipeline: ConstrainedGenerationPipeline,
        settings: RecommendSettings,
    ) -> None:
        self._core = core
        self._pipeline = pipeline
        self._settings = settings

    async def analyze_match(
        self,
        entity_id: str,
        student_context: StudentContext | None,
        evidence_policy: str,
        *,
        viewer_permissions: ViewerPermissions | None = None,
        request_id: str | None = None,
    ) -> AuxiliaryGenerationResult:
        if not isinstance(entity_id, str) or not entity_id:
            return _error(RecommendationErrorCode.INVALID_REQUEST, "entity_id must be non-empty")
        vp = viewer_permissions or ViewerPermissions()
        loaded = await self._load_snapshot_profile()
        if isinstance(loaded, AuxiliaryGenerationResult):
            return loaded
        snapshot, gen_profile = loaded
        detail_or_error = await self._read_detail(snapshot, entity_id, vp, include_contacts=False,
                                                 generation_profile_version=gen_profile.version)
        if isinstance(detail_or_error, AuxiliaryGenerationResult):
            return detail_or_error
        detail = detail_or_error
        bundle = _trim_bundle(detail.fact_bundle, _student_terms(student_context) + (entity_id,))
        if not bundle.facts:
            return _error(
                RecommendationErrorCode.INSUFFICIENT_FACTS,
                "fact bundle is empty after trimming",
                generation_profile_version=gen_profile.version,
            )
        op = gen_profile.operations["match_analysis"]
        result_or_error = await self._generate(
            op=op,
            operation_id="match_analysis",
            user_inputs={
                "entity_id": entity_id,
                "display_name": detail.display_name,
                "evidence_policy": evidence_policy,
            },
            fact_bundle=bundle,
            student_context=student_context,
            generation_profile_version=gen_profile.version,
            request_id=request_id,
        )
        if isinstance(result_or_error, AuxiliaryGenerationResult):
            return result_or_error
        result, mapped_warnings = result_or_error
        output = result.output if isinstance(result.output, dict) else {}
        summary = output.get("summary")
        scores = _match_dimension_scores(output.get("dimension_scores"))
        if not isinstance(summary, str) or not summary.strip():
            return _error(
                RecommendationErrorCode.GENERATION_PARSE_ERROR,
                "match analysis output is not usable",
                generation_profile_version=gen_profile.version,
            )
        if scores is None:
            return _error(
                RecommendationErrorCode.GENERATION_PARSE_ERROR,
                "match analysis dimension_scores are not usable",
                generation_profile_version=gen_profile.version,
            )
        payload = MatchAnalysis(
            build_id=snapshot.build_id,
            ranking_profile_version=snapshot.ranking_profile_version,
            generation_profile_version=gen_profile.version,
            grounded_rules_manifest_hash=gen_profile.grounded_rules_manifest_hash,
            embedding_fingerprint=snapshot.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            entity_id=entity_id,
            display_name=detail.display_name,
            summary=summary,
            dimension_scores=scores,
            next_steps=_string_tuple(output.get("next_steps")),
            claims=tuple(result.claims),
            cited_refs=tuple(result.cited_refs),
            warnings=tuple(mapped_warnings) + _student_context_warning(student_context),
        )
        return AuxiliaryGenerationResult(
            kind="match_analysis",
            match_analysis=payload,
            generation_profile_version=gen_profile.version,
        )

    async def draft_outreach_email(
        self,
        entity_id: str,
        student_context: StudentContext | None,
        tone: str,
        language: str,
        *,
        include_contacts: bool = False,
        viewer_permissions: ViewerPermissions | None = None,
        request_id: str | None = None,
    ) -> AuxiliaryGenerationResult:
        if not isinstance(entity_id, str) or not entity_id:
            return _error(RecommendationErrorCode.INVALID_REQUEST, "entity_id must be non-empty")
        vp = viewer_permissions or ViewerPermissions()
        if include_contacts and not vp.include_contacts:
            return _error(
                RecommendationErrorCode.UNAUTHORIZED_CONTACT,
                "include_contacts requested without permission",
            )
        loaded = await self._load_snapshot_profile()
        if isinstance(loaded, AuxiliaryGenerationResult):
            return loaded
        snapshot, gen_profile = loaded
        detail_or_error = await self._read_detail(
            snapshot, entity_id, vp, include_contacts=include_contacts,
            generation_profile_version=gen_profile.version,
        )
        if isinstance(detail_or_error, AuxiliaryGenerationResult):
            return detail_or_error
        detail = detail_or_error
        bundle = _trim_bundle(detail.fact_bundle, _student_terms(student_context) + (entity_id,))
        if not bundle.facts:
            return _error(
                RecommendationErrorCode.INSUFFICIENT_FACTS,
                "fact bundle is empty after trimming",
                generation_profile_version=gen_profile.version,
            )
        op = gen_profile.operations["outreach_email"]
        bundle = _limit_bundle_facts(bundle, op.fact_limit)
        result_or_error = await self._generate(
            op=op,
            operation_id="outreach_email",
            user_inputs={
                "entity_id": entity_id,
                "display_name": detail.display_name,
                "tone": tone,
                "language": language,
            },
            fact_bundle=bundle,
            student_context=student_context,
            generation_profile_version=gen_profile.version,
            request_id=request_id,
        )
        if isinstance(result_or_error, AuxiliaryGenerationResult):
            return result_or_error
        result, mapped_warnings = result_or_error
        output = result.output if isinstance(result.output, dict) else {}
        subject = output.get("subject")
        body = output.get("body")
        if not isinstance(subject, str) or not subject.strip() or not isinstance(body, str) or not body.strip():
            return _error(
                RecommendationErrorCode.GENERATION_PARSE_ERROR,
                "outreach email output is not usable",
                generation_profile_version=gen_profile.version,
            )
        payload = OutreachDraft(
            build_id=snapshot.build_id,
            ranking_profile_version=snapshot.ranking_profile_version,
            generation_profile_version=gen_profile.version,
            grounded_rules_manifest_hash=gen_profile.grounded_rules_manifest_hash,
            embedding_fingerprint=snapshot.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            entity_id=entity_id,
            display_name=detail.display_name,
            subject=subject,
            body=body,
            tone=tone,
            language=language,
            authorized_contacts=dict(detail.contacts) if include_contacts and vp.include_contacts else {},
            claims=tuple(result.claims),
            cited_refs=tuple(result.cited_refs),
            warnings=tuple(mapped_warnings) + _student_context_warning(student_context),
        )
        return AuxiliaryGenerationResult(
            kind="outreach_email",
            outreach_draft=payload,
            generation_profile_version=gen_profile.version,
        )

    async def compare_professors(
        self,
        entity_ids: Sequence[str],
        student_context: StudentContext | None,
        evidence_policy: str,
        *,
        viewer_permissions: ViewerPermissions | None = None,
        request_id: str | None = None,
    ) -> AuxiliaryGenerationResult:
        ids = tuple(entity_ids or ())
        if (
            not 2 <= len(ids) <= 3
            or len(set(ids)) != len(ids)
            or any(not isinstance(eid, str) or not eid for eid in ids)
        ):
            return _error(
                RecommendationErrorCode.INVALID_REQUEST,
                "compare_professors requires 2-3 distinct entity_ids",
            )
        vp = viewer_permissions or ViewerPermissions()
        loaded = await self._load_snapshot_profile()
        if isinstance(loaded, AuxiliaryGenerationResult):
            return loaded
        snapshot, gen_profile = loaded
        details: list[ProfessorDetail] = []
        for entity_id in ids:
            detail_or_error = await self._read_detail(
                snapshot, entity_id, vp, include_contacts=False,
                generation_profile_version=gen_profile.version,
            )
            if isinstance(detail_or_error, AuxiliaryGenerationResult):
                return detail_or_error
            details.append(detail_or_error)
        bundle = _trim_bundle(
            _merge_compare_bundle(details),
            _student_terms(student_context) + ids,
        )
        if not bundle.facts:
            return _error(
                RecommendationErrorCode.INSUFFICIENT_FACTS,
                "merged fact bundle is empty after trimming",
                generation_profile_version=gen_profile.version,
            )
        op = gen_profile.operations["professor_comparison"]
        result_or_error = await self._generate(
            op=op,
            operation_id="professor_comparison",
            user_inputs={
                "entity_ids": list(ids),
                "display_names": {detail.entity_id: detail.display_name for detail in details},
                "evidence_policy": evidence_policy,
            },
            fact_bundle=bundle,
            student_context=student_context,
            generation_profile_version=gen_profile.version,
            request_id=request_id,
        )
        if isinstance(result_or_error, AuxiliaryGenerationResult):
            return result_or_error
        result, mapped_warnings = result_or_error
        output = result.output if isinstance(result.output, dict) else {}
        summary = output.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return _error(
                RecommendationErrorCode.GENERATION_PARSE_ERROR,
                "professor comparison output is not usable",
                generation_profile_version=gen_profile.version,
            )
        payload = ProfessorComparison(
            build_id=snapshot.build_id,
            ranking_profile_version=snapshot.ranking_profile_version,
            generation_profile_version=gen_profile.version,
            grounded_rules_manifest_hash=gen_profile.grounded_rules_manifest_hash,
            embedding_fingerprint=snapshot.embedding_fingerprint,
            taxonomy_version=snapshot.taxonomy_version,
            entity_ids=ids,
            display_names={detail.entity_id: detail.display_name for detail in details},
            summary=summary,
            professor_notes=_string_tuple_mapping(output.get("professor_notes")),
            evidence_gaps=_string_tuple_mapping(output.get("evidence_gaps")),
            claims=tuple(result.claims),
            cited_refs=tuple(result.cited_refs),
            warnings=tuple(mapped_warnings),
        )
        return AuxiliaryGenerationResult(
            kind="professor_comparison",
            professor_comparison=payload,
            generation_profile_version=gen_profile.version,
        )

    async def _load_snapshot_profile(self):
        try:
            snapshot = self._core.deps.snapshot_port.get_snapshot()
        except Exception:
            snapshot = None
        if snapshot is None:
            return _error(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, "no ACTIVE build")
        try:
            gen_profile = await self._core.deps.generation_profile_port.read_profile(
                self._settings.generation_profile_path
            )
        except Exception:
            return _error(
                RecommendationErrorCode.GENERATION_UNAVAILABLE,
                "generation profile unavailable",
            )
        return snapshot, gen_profile

    async def _read_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        vp: ViewerPermissions,
        *,
        include_contacts: bool,
        generation_profile_version: str,
    ):
        try:
            detail = await self._core.deps.facts_port.get_detail(
                snapshot,
                entity_id,
                include_contacts=include_contacts,
                viewer_permissions=vp,
            )
        except (KeyError, LookupError):
            return _error(
                RecommendationErrorCode.ANCHOR_NOT_IN_ACTIVE_BUILD,
                "entity is not available in the ACTIVE build",
                generation_profile_version=generation_profile_version,
            )
        if not _allowed_detail(detail, vp):
            return _error(
                RecommendationErrorCode.ANCHOR_NOT_IN_ACTIVE_BUILD,
                "entity is not available in the ACTIVE build",
                generation_profile_version=generation_profile_version,
            )
        return detail

    async def _generate(
        self,
        *,
        op,
        operation_id: str,
        user_inputs: dict,
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        generation_profile_version: str,
        request_id: str | None = None,
    ):
        timeout = float(op.timeout)
        outer_timeout = timeout + _OUTER_TIMEOUT_GRACE_SECONDS
        started_at = time.monotonic()
        try:
            result = await asyncio.wait_for(self._pipeline.generate(
                system_prompt_id=op.system_prompt_id,
                user_inputs=user_inputs,
                fact_bundle=fact_bundle,
                student_context=student_context,
                json_schema=dict(op.json_schema),
                generation_profile_version=generation_profile_version,
                safety_domain="recommend",
                include_contacts=False,
                operation_id=operation_id,
                subject_kind="mentor",
                support_validator=validate_fact_index_support,
            ), timeout=outer_timeout)
        except Exception as exc:
            elapsed_ms = _elapsed_ms(started_at)
            _log_generation_exception(
                exc,
                request_id=request_id,
                operation_id=operation_id,
                system_prompt_id=op.system_prompt_id,
                fact_bundle=fact_bundle,
                student_context=student_context,
                generation_profile_version=generation_profile_version,
                elapsed_ms=elapsed_ms,
                timeout=timeout,
                outer_timeout=outer_timeout,
            )
            if _is_timeout_exception(exc):
                return _error(
                    RecommendationErrorCode.REQUEST_TIMEOUT,
                    f"{operation_id} generation timed out",
                    generation_profile_version=generation_profile_version,
                )
            if _is_provider_exception(exc):
                return _error(
                    RecommendationErrorCode.LLM_UNAVAILABLE,
                    f"{operation_id} provider unavailable",
                    generation_profile_version=generation_profile_version,
                )
            return _error(
                RecommendationErrorCode.GENERATION_UNAVAILABLE,
                f"{operation_id} generation failed",
                generation_profile_version=generation_profile_version,
            )
        _log_generation_success(
            request_id=request_id,
            operation_id=operation_id,
            system_prompt_id=op.system_prompt_id,
            fact_bundle=fact_bundle,
            student_context=student_context,
            generation_profile_version=generation_profile_version,
            elapsed_ms=_elapsed_ms(started_at),
            timeout=timeout,
            outer_timeout=outer_timeout,
            warning_count=len(result.warnings),
        )
        mapped = map_generation_warnings(
            result.warnings,
            generation_unavailable_code=RecommendationErrorCode.GENERATION_UNAVAILABLE,
        )
        if any(w.severity == "error" for w in mapped):
            return _error_from_issues(mapped, generation_profile_version=generation_profile_version)
        if not has_grounded_fact_claim(result):
            return _error(
                RecommendationErrorCode.NO_GROUNDED_OUTPUT,
                f"{operation_id} output is not grounded",
                generation_profile_version=generation_profile_version,
            )
        return result, tuple(w for w in mapped if w.severity != "error")


__all__ = ["AuxiliaryGenerationService"]
