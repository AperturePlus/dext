# src/dext_recommend/core/query_understanding.py
"""Map LLMGenerationPort output to internal QueryUnderstanding.

Parse failure or blocking GenerationWarning codes degrade to
needs_clarification=True — never trigger wide recall (spec §10).
"""
from __future__ import annotations

from typing import Any

from dext_grounded import FactBundle, LLMGenerationPort

from dext_recommend.models import QueryUnderstanding, RecommendRequest
from dext_recommend.readiness import ActiveBuildSnapshot

from dext_recommend.core._schemas import QUERY_UNDERSTANDING_SCHEMA

_SYSTEM_PROMPT_ID = "query_understanding_v1"
_BLOCKING_WARNING_CODES = {"generation_unavailable", "schema_validation_failed", "json_parse_failed"}


def _safe_log_student_context(ctx: Any) -> dict | None:
    if ctx is None:
        return None
    return {"education_stage": getattr(ctx, "education_stage", None),
            "major": getattr(ctx, "major", None)}


def _filter_summary(filters: Any) -> dict:
    return {
        "university_ids": list(getattr(filters, "university_ids", ())),
        "city_names": list(getattr(filters, "city_names", ())),
        "org_unit_ids": list(getattr(filters, "org_unit_ids", ())),
        "master_eligibility": getattr(filters, "master_eligibility", "any"),
        "phd_eligibility": getattr(filters, "phd_eligibility", "any"),
    }


def _fallback(query_text: str) -> QueryUnderstanding:
    tokens = tuple(t for t in query_text.split() if t) or (query_text,)
    return QueryUnderstanding(
        research_interests=tokens,
        preferred_universities=(), preferred_cities=(), preferred_org_units=(),
        degree_goal=None, mentor_eligibility_requirement=None,
        missing_information=(), needs_clarification=True, confidence=0.0,
    )


def _coerce(output: Any) -> QueryUnderstanding | None:
    if not isinstance(output, dict):
        return None
    # Missing the discriminating required fields => treat as parse failure
    # (spec §10: schema-violating output degrades to needs_clarification=True).
    if "needs_clarification" not in output or "confidence" not in output:
        return None
    try:
        return QueryUnderstanding(
            research_interests=tuple(output.get("research_interests") or ()),
            preferred_universities=tuple(output.get("preferred_universities") or ()),
            preferred_cities=tuple(output.get("preferred_cities") or ()),
            preferred_org_units=tuple(output.get("preferred_org_units") or ()),
            degree_goal=output.get("degree_goal"),
            mentor_eligibility_requirement=output.get("mentor_eligibility_requirement"),
            missing_information=tuple(output.get("missing_information") or ()),
            needs_clarification=bool(output.get("needs_clarification")),
            confidence=float(output.get("confidence") or 0.0),
        )
    except (TypeError, ValueError):
        return None


async def understand_query(
    request: RecommendRequest,
    llm_port: LLMGenerationPort,
    snapshot: ActiveBuildSnapshot,
    *,
    profile_version: str,
) -> QueryUnderstanding:
    fact_bundle = FactBundle(
        build_id=snapshot.build_id,
        subject_id="query_understanding",
        facts=(),
        source_refs=(),
    )
    user_inputs = {
        "query_text": request.query_text,
        "filters": _filter_summary(request.filters),
        "student_context_summary": _safe_log_student_context(request.student_context),
        "conversation_intent": (
            request.conversation_context.intent
            if request.conversation_context is not None else None
        ),
    }
    result = await llm_port.generate(
        system_prompt_id=_SYSTEM_PROMPT_ID,
        user_inputs=user_inputs,
        fact_bundle=fact_bundle,
        student_context=request.student_context,
        json_schema=QUERY_UNDERSTANDING_SCHEMA,
        generation_profile_version=profile_version,
    )
    blocking = any(w.code in _BLOCKING_WARNING_CODES for w in result.warnings)
    if blocking:
        return _fallback(request.query_text)
    qu = _coerce(result.output)
    if qu is None:
        return _fallback(request.query_text)
    return qu


__all__ = ["understand_query"]
