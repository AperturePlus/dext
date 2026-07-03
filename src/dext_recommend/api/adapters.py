"""Explicit DTO adapters between public HTTP shapes and internal models."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from dext_grounded import StudentContext
from dext_recommend.api.schemas import UserProfile
from dext_recommend.models import (
    AuxiliaryGenerationResult,
    ConversationContext,
    ConversationDispatchResult,
    DetailFollowupResponse,
    MatchAnalysis,
    OutreachDraft,
    ProfessorComparison,
    QueryUnderstanding,
    RecommendRequest,
    RecommendResponse,
    RecommendedProfessor,
    RecommendationFilters,
)
from dext_recommend.ports import ProfessorDetail


def student_context_from_profile(profile: UserProfile | None) -> StudentContext | None:
    if profile is None:
        return None
    achievements = _bounded_join(
        [
            profile.highlights,
            *(
                _compact_item(
                    item.title,
                    item.summary,
                    item.role,
                )
                for item in profile.research
            ),
        ],
        limit=1200,
    )
    competitions = _bounded_join(
        (_compact_item(item.name, item.level, item.award) for item in profile.competitions),
        limit=800,
    )
    completeness_fields = [
        profile.degree_stage,
        profile.school,
        profile.major,
        profile.score.gpa_bucket if profile.score else None,
        profile.score.rank_bucket if profile.score else None,
        achievements,
        competitions,
        *profile.research_interests,
    ]
    populated = sum(1 for value in completeness_fields if value)
    completeness = min(1.0, populated / 8.0)
    return StudentContext(
        education_stage=profile.degree_stage or profile.target_degree,
        school=profile.school,
        major=profile.major,
        gpa_bucket=profile.score.gpa_bucket if profile.score else None,
        rank_bucket=profile.score.rank_bucket if profile.score else None,
        research_interests=list(profile.research_interests[:20]),
        achievements_summary=achievements or None,
        competition_experience_summary=competitions or None,
        profile_completeness=completeness,
    )


def recommend_request_from_public(
    *,
    prompt: str,
    profile: UserProfile | None,
    session_id: str | None = None,
    turn_id: str | None = None,
    professor_id: str | None = None,
    limit: int = 10,
) -> RecommendRequest:
    context = None
    if session_id and turn_id:
        context = ConversationContext(
            session_id=session_id,
            turn_id=turn_id,
            anchor_entity_id=professor_id,
            intent_source="implicit",
        )
    return RecommendRequest(
        query_text=prompt,
        student_context=student_context_from_profile(profile),
        filters=RecommendationFilters(),
        conversation_context=context,
        limit=limit,
        include_contacts=False,
        diagnostics_level="none",
    )


def recommendation_response_to_public(
    response: RecommendResponse,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    return {
        "session_id": session_id or "",
        "query_understanding": query_understanding_to_public(response.query_understanding),
        "recommendations": [
            recommended_professor_to_public(item) for item in response.results
        ],
        "follow_up_questions": list(response.suggested_followups),
        "build_id": response.build_id,
        "ranking_profile_version": response.ranking_profile_version,
        "generation_profile_version": response.generation_profile_version,
        "warnings": [warning_to_public(w) for w in response.warnings],
    }


def query_understanding_to_public(value: QueryUnderstanding) -> dict[str, Any]:
    return {
        "research_interests": list(value.research_interests),
        "preferred_locations": list(value.preferred_cities),
        "preferred_universities": list(value.preferred_universities),
        "degree_stage": value.degree_goal,
        "uncertainties": list(value.missing_information),
    }


def recommended_professor_to_public(value: RecommendedProfessor) -> dict[str, Any]:
    reason = "；".join(value.short_reasons) if value.short_reasons else value.research_summary or ""
    return {
        "professor_id": value.entity_id,
        "name": value.display_name,
        "university": value.university,
        "college": " / ".join(value.org_units),
        "title": value.title,
        "research_fields": list(value.matched_topics or value.matched_statements),
        "homepage_url": value.profile_url,
        "match_level": _match_level(value.match_level),
        "match_score": max(0.0, min(1.0, float(value.score))),
        "reason": reason,
        "limitations": list(value.risk_flags),
    }


def professor_detail_to_public(value: ProfessorDetail) -> dict[str, Any]:
    return {
        "professor_id": value.entity_id,
        "name": value.display_name,
        "university": value.university,
        "college": " / ".join(value.org_units),
        "title": value.title,
        "research_fields": list(value.approved_topics or value.research_statements),
        "bio": "\n".join(value.bio_snippets),
        "homepage_url": value.profile_url,
        "source_url": value.source_urls[0] if value.source_urls else value.profile_url,
        "updated_at": None,
        "data_quality_score": 1.0 if not value.quality_findings else 0.8,
    }


def auxiliary_result_to_public(result: AuxiliaryGenerationResult) -> dict[str, Any]:
    if result.kind == "match_analysis" and result.match_analysis is not None:
        return match_analysis_to_public(result.match_analysis)
    if result.kind == "outreach_email" and result.outreach_draft is not None:
        return outreach_to_public(result.outreach_draft)
    if result.kind == "professor_comparison" and result.professor_comparison is not None:
        return comparison_to_public(result.professor_comparison)
    return {
        "error": True,
        "issues": [warning_to_public(w) for w in result.issues],
    }


def match_analysis_to_public(value: MatchAnalysis) -> dict[str, Any]:
    return {
        "professor_id": value.entity_id,
        "summary": value.summary,
        "strengths": value.next_steps[:2],
        "gaps": [],
        "suggestions": list(value.next_steps),
        "dimensions": [
            {
                "label": key,
                "score": int(max(0, min(100, float(score) * 100))),
                "comment": key,
            }
            for key, score in value.dimension_scores.items()
        ],
    }


def outreach_to_public(value: OutreachDraft) -> dict[str, str]:
    return {"subject": value.subject, "body": value.body}


def comparison_to_public(value: ProfessorComparison) -> dict[str, Any]:
    return {
        "summary": value.summary,
        "professors": [
            {
                "professor_id": entity_id,
                "name": value.display_names.get(entity_id, entity_id),
                "notes": list(value.professor_notes.get(entity_id, ())),
                "evidence_gaps": list(value.evidence_gaps.get(entity_id, ())),
            }
            for entity_id in value.entity_ids
        ],
    }


def conversation_dispatch_to_answer(result: ConversationDispatchResult) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if result.kind == "recommendation" and result.recommendation is not None:
        recommendations = [
            recommended_professor_to_public(item) for item in result.recommendation.results
        ]
        answer = "已根据你的问题推荐了合适的导师。"
        snapshot = {
            "result_entity_ids": [item["professor_id"] for item in recommendations],
            "build_id": result.recommendation.build_id,
            "ranking_profile_version": result.recommendation.ranking_profile_version,
            "generation_profile_version": result.recommendation.generation_profile_version,
        }
        return answer, recommendations, snapshot
    if result.kind == "detail_followup" and result.detail_followup is not None:
        detail = result.detail_followup
        return detail.answer, [], {
            "result_entity_ids": [detail.anchor_entity_id],
            "build_id": detail.build_id,
            "ranking_profile_version": detail.ranking_profile_version,
            "generation_profile_version": detail.generation_profile_version,
        }
    issues = [warning_to_public(item) for item in result.issues]
    answer = issues[0]["message"] if issues else "无法完成本次对话。"
    return answer, [], {"issues": issues}


def detail_followup_to_public(value: DetailFollowupResponse) -> dict[str, Any]:
    return {
        "answer": value.answer,
        "anchor_entity_id": value.anchor_entity_id,
        "anchor_display_name": value.anchor_display_name,
        "warnings": [warning_to_public(w) for w in value.warnings],
    }


def warning_to_public(value) -> dict[str, Any]:
    return {
        "code": str(value.code),
        "message": str(value.message),
        "severity": str(value.severity),
    }


def _match_level(value: str) -> str:
    return {
        "excellent": "Ип",
        "strong": "жа",
        "possible": "ЕЭ",
        "weak": "ЕЭ",
    }.get(value, value)


def _compact_item(*parts: str | None) -> str | None:
    kept = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    return " / ".join(kept) if kept else None


def _bounded_join(items, *, limit: int) -> str:
    out: list[str] = []
    used = 0
    for item in items:
        if not item:
            continue
        text = str(item).strip()
        if not text:
            continue
        remaining = limit - used
        if remaining <= 0:
            break
        clipped = text[:remaining]
        out.append(clipped)
        used += len(clipped)
    return "\n".join(out)[:limit]


__all__ = [
    "auxiliary_result_to_public",
    "conversation_dispatch_to_answer",
    "detail_followup_to_public",
    "professor_detail_to_public",
    "recommend_request_from_public",
    "recommendation_response_to_public",
    "student_context_from_profile",
]
