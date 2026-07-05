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
                    item.venue_or_status,
                    item.role,
                    item.year,
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
        _gpa_bucket(profile.score),
        _rank_bucket(profile.score),
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
        gpa_bucket=_gpa_bucket(profile.score),
        rank_bucket=_rank_bucket(profile.score),
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


_MATCH_DIMENSION_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("方向契合", ("方向契合", "research_alignment", "research_fit")),
    ("方法匹配", ("方法匹配", "method_match", "preparation")),
    ("地域", ("地域", "location_fit", "geography")),
    ("学历目标", ("学历目标", "academic_background", "degree_goal")),
    ("产出活跃", ("产出活跃", "supervision_capacity", "publication_activity", "output_activity")),
)

_MATCH_DIMENSION_COMMENTS: dict[str, str] = {
    "方向契合": "当前资料显示研究方向有一定重合，建议继续对照导师近三年论文确认细分主题。",
    "方法匹配": "现有背景能支撑初步沟通，但还需要补充具体方法、工具或实验经历。",
    "地域": "地域匹配度只能作为参考，建议补充目标城市、学校层级或就读偏好。",
    "学历目标": "目标阶段与导师招生类型需要进一步核对，尤其要确认硕博名额和培养方向。",
    "产出活跃": "导师近期产出可作为准备材料的依据，建议补充你自己的论文、项目或可展示成果。",
}

_MATCH_DIMENSION_MISSING_COMMENTS: dict[str, str] = {
    "方向契合": "当前资料不足以判断研究方向契合度，建议补充具体研究兴趣、项目主题或论文阅读记录。",
    "方法匹配": "当前资料不足以判断方法匹配，建议补充常用技术栈、实验方法或数据分析经历。",
    "地域": "当前资料未体现地域偏好，建议补充目标城市、学校层级或是否接受异地。",
    "学历目标": "当前资料不足以判断学历目标匹配，建议补充申请硕士、博士或联培等具体目标。",
    "产出活跃": "当前资料不足以判断产出活跃匹配，建议补充论文、项目、专利或竞赛成果。",
}


def _match_score_to_public(value: float) -> int:
    score = float(value)
    if score <= 1.0:
        score *= 100
    return int(round(max(0.0, min(100.0, score))))


def _match_dimensions_to_public(scores: dict[str, float]) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    for label, aliases in _MATCH_DIMENSION_ALIASES:
        matched_score = None
        for alias in aliases:
            if alias in scores:
                matched_score = scores[alias]
                break
        if matched_score is None:
            dimensions.append({
                "label": label,
                "score": 50,
                "comment": _MATCH_DIMENSION_MISSING_COMMENTS[label],
            })
        else:
            dimensions.append({
                "label": label,
                "score": _match_score_to_public(matched_score),
                "comment": _MATCH_DIMENSION_COMMENTS[label],
            })
    return dimensions


def match_analysis_to_public(value: MatchAnalysis) -> dict[str, Any]:
    return {
        "professor_id": value.entity_id,
        "summary": value.summary,
        "strengths": value.next_steps[:2],
        "gaps": [],
        "suggestions": list(value.next_steps),
        "dimensions": _match_dimensions_to_public(dict(value.dimension_scores)),
    }


def outreach_to_public(value: OutreachDraft) -> dict[str, str]:
    return {"subject": value.subject, "body": value.body}


def comparison_to_public(value: ProfessorComparison) -> dict[str, Any]:
    professors = [
        {
            "professor_id": entity_id,
            "name": value.display_names.get(entity_id, entity_id),
            "university": "",
            "college": "",
            "title": "",
            "research_fields": [],
        }
        for entity_id in value.entity_ids
    ]
    return {
        "professor_ids": list(value.entity_ids),
        "professors": professors,
        "rows": [
            {
                "dimension": "综合对比",
                "cells": {
                    entity_id: "；".join(value.professor_notes.get(entity_id, ()))
                    for entity_id in value.entity_ids
                },
            }
        ],
        "summary": value.summary,
        "suggestion": value.summary,
    }


def conversation_dispatch_to_answer(result: ConversationDispatchResult) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if result.kind == "recommendation" and result.recommendation is not None:
        recommendations = [
            recommended_professor_to_public(item) for item in result.recommendation.results
        ]
        warnings = [warning_to_public(w) for w in result.recommendation.warnings]
        if recommendations:
            answer = "已根据你的问题推荐了合适的导师。"
        else:
            errors = [w for w in warnings if w["severity"] == "error"]
            if errors:
                answer = errors[0]["message"] or "无法完成本次导师推荐。"
            elif any(w["code"] == "no_candidates_after_filters" for w in warnings):
                answer = "这次没有找到足够匹配的导师，可以放宽学校或研究方向后再试。"
            else:
                answer = "这次没有生成可展示的导师推荐，可以调整条件后再试。"
        snapshot = {
            "result_entity_ids": [item["professor_id"] for item in recommendations],
            "build_id": result.recommendation.build_id,
            "ranking_profile_version": result.recommendation.ranking_profile_version,
            "generation_profile_version": result.recommendation.generation_profile_version,
            "warnings": warnings,
            "query_diagnostics": query_diagnostics_to_public(result.recommendation.query),
            "phase_diagnostics": [
                phase_diagnostic_to_public(item)
                for item in result.recommendation.phase_diagnostics
            ],
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


def query_diagnostics_to_public(value) -> dict[str, Any]:
    return {
        "query_length": int(value.query_length),
        "language_summary": value.language_summary,
        "filter_summary": value.filter_summary,
        "recall_count": int(getattr(value, "recall_count", 0)),
        "post_filter_count": int(getattr(value, "post_filter_count", 0)),
        "returned_count": int(getattr(value, "returned_count", 0)),
        "steps_used": int(getattr(value, "steps_used", 0)),
    }


def phase_diagnostic_to_public(value) -> dict[str, Any]:
    return {
        "phase": str(value.phase),
        "attempt": value.attempt,
        "elapsed_ms": float(value.elapsed_ms),
        "error_code": value.error_code,
    }


def _match_level(value: str) -> str:
    return {
        "excellent": "高",
        "strong": "中",
        "possible": "低",
        "weak": "低",
    }.get(value, value)


def _gpa_bucket(score) -> str | None:
    if score is None or score.gpa is None:
        return None
    scale = float(score.scale or 4.0)
    if scale <= 0:
        return "unknown"
    ratio = float(score.gpa) / scale
    if ratio >= 0.9:
        return "top10"
    if ratio >= 0.85:
        return "top25"
    if ratio >= 0.8:
        return "high"
    if ratio >= 0.7:
        return "medium"
    return "unknown"


def _rank_bucket(score) -> str | None:
    if score is None:
        return None
    if score.rank_mode == "percent" and score.percent is not None:
        percent = int(score.percent)
        if percent <= 5:
            return "top5"
        if percent <= 10:
            return "top10"
        if percent <= 25:
            return "top25"
        return "medium"
    if score.rank_mode == "ordinal" and score.rank_position and score.rank_total:
        if score.rank_total <= 0:
            return "unknown"
        percent = 100.0 * int(score.rank_position) / int(score.rank_total)
        if percent <= 5:
            return "top5"
        if percent <= 10:
            return "top10"
        if percent <= 25:
            return "top25"
        return "medium"
    return None


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
