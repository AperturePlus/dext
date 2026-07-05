"""Competition query understanding (C3 spec §3)."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from dext_competition.contracts.recommend import (
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
    CompetitionRecommendRequest,
)
from dext_competition.recommend.generation_profile import QueryUnderstandingProfile
from dext_grounded import ConstrainedGenerationPipeline, FactBundle


QUERY_UNDERSTANDING_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "interests": {"type": "array", "items": {"type": "string"}},
        "major_fit": {"type": ["string", "null"]},
        "grade_fit": {"type": ["string", "null"]},
        "experience_level": {"type": ["string", "null"]},
        "weekly_hours": {"type": ["integer", "null"]},
        "target_goal": {"type": ["string", "null"]},
        "team_preference": {"type": ["string", "null"]},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["needs_clarification", "confidence"],
}
DEFAULT_GENERATION_PROFILE_VERSION = "competition.query-understanding.v1"
PIPELINE_GENERATION_PROFILE_VERSION = DEFAULT_GENERATION_PROFILE_VERSION
_BLOCKING_WARNING_CODES = {
    "generation_unavailable",
    "generation_parse_error",
    "schema_validation_failed",
    "json_parse_failed",
    "unsafe_advice",
}
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")


@dataclass(frozen=True, slots=True)
class QueryUnderstandingResult:
    understanding: CompetitionQueryUnderstanding
    generation_profile_version: str
    warning_code: str | None = None


def _clean_tokens(value: str) -> tuple[str, ...]:
    stopwords = {
        "我想", "推荐", "适合", "比赛", "竞赛", "一个", "一些", "有没有",
        "大学生", "参加", "准备", "希望",
    }
    tokens = []
    for token in _TOKEN_RE.findall(value):
        if token in stopwords:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tuple(dict.fromkeys(tokens))


def _preference_missing(preferences: CompetitionPreferences) -> tuple[str, ...]:
    missing: list[str] = []
    if not preferences.categories:
        missing.append("categories")
    if not preferences.major:
        missing.append("major")
    if preferences.weekly_hours is None:
        missing.append("weekly_hours")
    return tuple(missing)


def heuristic_understanding(request: CompetitionRecommendRequest) -> CompetitionQueryUnderstanding:
    query = (request.query_text or "").strip()
    prefs = request.preferences
    if not query and not prefs.categories:
        return CompetitionQueryUnderstanding(
            interests=(),
            major_fit=prefs.major,
            grade_fit=prefs.grade,
            experience_level=prefs.experience_level,
            weekly_hours=prefs.weekly_hours,
            target_goal=prefs.target_goal,
            team_preference=prefs.team_preference,
            missing_information=("query_text", "categories"),
            needs_clarification=True,
            confidence=0.0,
        )
    interests = tuple(dict.fromkeys((*prefs.categories, *_clean_tokens(query))))
    return CompetitionQueryUnderstanding(
        interests=interests,
        major_fit=prefs.major,
        grade_fit=prefs.grade,
        experience_level=prefs.experience_level,
        weekly_hours=prefs.weekly_hours,
        target_goal=prefs.target_goal,
        team_preference=prefs.team_preference,
        missing_information=_preference_missing(prefs),
        needs_clarification=False,
        confidence=0.55 if query else 0.35,
    )


def _coerce_output(output: Any, request: CompetitionRecommendRequest) -> CompetitionQueryUnderstanding | None:
    if not isinstance(output, dict):
        return None
    if "needs_clarification" not in output or "confidence" not in output:
        return None
    prefs = request.preferences
    try:
        return CompetitionQueryUnderstanding(
            interests=tuple(output.get("interests") or ()),
            major_fit=output.get("major_fit") or prefs.major,
            grade_fit=output.get("grade_fit") or prefs.grade,
            experience_level=output.get("experience_level") or prefs.experience_level,
            weekly_hours=(
                int(output["weekly_hours"])
                if output.get("weekly_hours") is not None else prefs.weekly_hours
            ),
            target_goal=output.get("target_goal") or prefs.target_goal,
            team_preference=output.get("team_preference") or prefs.team_preference,
            missing_information=tuple(output.get("missing_information") or ()),
            needs_clarification=bool(output.get("needs_clarification")),
            confidence=float(output.get("confidence") or 0.0),
        )
    except (TypeError, ValueError):
        return None


async def understand_query(
    request: CompetitionRecommendRequest,
    *,
    knowledge_base_version: str,
    generation_pipeline: ConstrainedGenerationPipeline | None,
    profile: QueryUnderstandingProfile,
) -> QueryUnderstandingResult:
    if generation_pipeline is None:
        return QueryUnderstandingResult(
            CompetitionQueryUnderstanding(
                interests=(), missing_information=("query_text",),
                needs_clarification=True, confidence=0.0,
            ),
            profile.version,
            "generation_unavailable",
        )

    fact_bundle = FactBundle(
        build_id=knowledge_base_version,
        subject_id="competition_query_understanding",
        facts=(),
        source_refs=(),
    )
    try:
        result = await asyncio.wait_for(
            generation_pipeline.generate(
                system_prompt_id=profile.system_prompt_id,
                user_inputs={
                    "query_text": request.query_text,
                    "preferences": {
                        "categories": list(request.preferences.categories),
                        "major": request.preferences.major,
                        "grade": request.preferences.grade,
                        "experience_level": request.preferences.experience_level,
                        "weekly_hours": request.preferences.weekly_hours,
                        "target_goal": request.preferences.target_goal,
                        "team_preference": request.preferences.team_preference,
                        "time_window": request.preferences.time_window,
                        "risk_tolerance": request.preferences.risk_tolerance,
                    },
                },
                fact_bundle=fact_bundle,
                student_context=request.student_context,
                json_schema=dict(profile.json_schema),
                generation_profile_version=profile.version,
                safety_domain=profile.safety_domain,
                operation_id=profile.operation_id,
            ),
            timeout=profile.timeout_seconds,
        )
    except (TimeoutError, OSError, RuntimeError):
        return QueryUnderstandingResult(
            CompetitionQueryUnderstanding(
                interests=(), missing_information=("query_text",),
                needs_clarification=True, confidence=0.0,
            ),
            profile.version,
            "generation_unavailable",
        )
    blocking = next((w.code for w in result.warnings if w.code in _BLOCKING_WARNING_CODES), None)
    if blocking:
        return QueryUnderstandingResult(
            CompetitionQueryUnderstanding(
                interests=(),
                missing_information=("query_text",),
                needs_clarification=True,
                confidence=0.0,
            ),
            profile.version,
            blocking,
        )
    understood = _coerce_output(result.output, request)
    if understood is None:
        return QueryUnderstandingResult(
            CompetitionQueryUnderstanding(
                interests=(), missing_information=("query_text",),
                needs_clarification=True, confidence=0.0,
            ),
            profile.version,
            "generation_parse_error",
        )
    return QueryUnderstandingResult(understood, profile.version)


__all__ = [
    "DEFAULT_GENERATION_PROFILE_VERSION",
    "PIPELINE_GENERATION_PROFILE_VERSION",
    "QUERY_UNDERSTANDING_SCHEMA",
    "QueryUnderstandingResult",
    "heuristic_understanding",
    "understand_query",
]
