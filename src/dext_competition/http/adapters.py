"""C7 public HTTP field mapping for competition contracts."""
from __future__ import annotations

from datetime import date
from typing import Any

from dext_competition.contracts.assistant import (
    AssistantHistoryTurn,
    CardResult,
    PlanAssistantRequest,
    PlanAssistantResult,
    PlanChangeCard,
)
from dext_competition.contracts.catalog import CompetitionCard
from dext_competition.contracts.plan import PlanPhase, PlanTask, PreparationPlanDraft
from dext_competition.contracts.qa import CompetitionDetail
from dext_competition.contracts.recommend import (
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
    CompetitionRecommendRequest,
    CompetitionRecommendResponse,
    RecommendedCompetition,
)
from dext_competition.http.schemas import (
    CompetitionRecommendationRequest as PublicCompetitionRecommendationRequest,
    PreparationAssistantPlanSnapshot,
    PreparationAssistantRequest as PublicPreparationAssistantRequest,
    PreparationPlanGenerateRequest,
)
from dext_competition.planning.schemas import PlanGenerationRequest
from dext_grounded import StudentContext

_WEEKLY_HOURS = {
    "hours3to5": 4,
    "hours6to10": 8,
    "hours11to15": 13,
    "hours16plus": 18,
}
_TIME_MODEL = {
    "eventWindow": "competition_window",
    "submission": "submission_deadline",
}
_TIMELINE_TYPE = {value: key for key, value in _TIME_MODEL.items()}


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _first(values: tuple[str, ...] | list[str], default: str = "") -> str:
    return str(values[0]) if values else default


def _public_recommended_base(
    *,
    competition_id: str,
    display_name: str,
    category: str,
    tags: tuple[str, ...] = (),
    schedule: str = "",
    team_policy: str = "",
    reason: str = "",
    preparation_tips: tuple[str, ...] = (),
    limitations: tuple[str, ...] = (),
    official_links: tuple[str, ...] = (),
    match_score: float = 0.0,
) -> dict[str, Any]:
    return {
        "id": competition_id,
        "name": display_name,
        "category": category,
        "level": "",
        "tags": list(tags),
        "team_size": team_policy,
        "signup_time": schedule,
        "contest_time": schedule,
        "format": "",
        "organizer": "",
        "official_url": _first(tuple(official_links)),
        "reason": reason,
        "preparation_tips": list(preparation_tips),
        "limitations": list(limitations),
        "match_score": _clamp_score(match_score),
    }


def catalog_card_to_public(card: CompetitionCard) -> dict[str, Any]:
    return _public_recommended_base(
        competition_id=card.competition_id,
        display_name=card.display_name,
        category=card.category,
        tags=card.tags,
        schedule=card.schedule,
        team_policy=card.team_policy,
        reason=card.summary,
        preparation_tips=card.preparation_focus,
        limitations=card.risk_flags,
        official_links=card.official_links,
    )


def competition_detail_to_public(detail: CompetitionDetail) -> dict[str, Any]:
    return _public_recommended_base(
        competition_id=detail.competition_id,
        display_name=detail.display_name,
        category=detail.category,
        schedule=detail.schedule,
        team_policy=detail.team_policy,
        reason=detail.summary,
        preparation_tips=detail.preparation_focus,
        limitations=detail.risk_flags,
        official_links=detail.official_links,
    )


def recommended_competition_to_public(value: RecommendedCompetition) -> dict[str, Any]:
    return _public_recommended_base(
        competition_id=value.competition_id,
        display_name=value.display_name,
        category=value.category,
        schedule=value.schedule_notes,
        team_policy=value.team_notes,
        reason="; ".join(value.short_reasons) or value.summary,
        preparation_tips=tuple(item for item in (value.preparation_effort, value.eligibility_notes) if item),
        limitations=value.risk_flags,
        official_links=value.official_links,
        match_score=value.score,
    )


def profile_to_student_context(profile: dict | None) -> StudentContext | None:
    if not profile:
        return None
    score = profile.get("score") if isinstance(profile.get("score"), dict) else {}
    return StudentContext(
        education_stage=profile.get("degree_stage") or profile.get("target_degree"),
        school=profile.get("school"),
        major=profile.get("major"),
        gpa_bucket=score.get("gpa_bucket"),
        rank_bucket=score.get("rank_bucket"),
        research_interests=list(profile.get("research_interests") or ()),
        achievements_summary=profile.get("highlights"),
        competition_experience_summary="; ".join(
            str(item.get("name") or "")
            for item in profile.get("competitions", ())
            if isinstance(item, dict) and item.get("name")
        ) or None,
    )


def recommendation_request_from_public(
    dto: PublicCompetitionRecommendationRequest,
) -> CompetitionRecommendRequest:
    profile = dto.profile or {}
    preferences = CompetitionPreferences(
        major=profile.get("major"),
        grade=profile.get("degree_stage"),
    )
    return CompetitionRecommendRequest(
        query_text=dto.prompt,
        preferences=preferences,
        student_context=profile_to_student_context(dto.profile),
        limit=6,
        diagnostics_level="summary",
    )


def query_understanding_to_public(
    value: CompetitionQueryUnderstanding | None,
) -> dict[str, Any]:
    if value is None:
        return {
            "directions": [],
            "categories": [],
            "timing_preferences": [],
            "team_preferences": [],
            "uncertainties": [],
        }
    return {
        "directions": list(value.interests),
        "categories": [value.major_fit] if value.major_fit else [],
        "timing_preferences": [value.target_goal] if value.target_goal else [],
        "team_preferences": [value.team_preference] if value.team_preference else [],
        "uncertainties": list(value.missing_information),
    }


def recommendation_response_to_public(
    response: CompetitionRecommendResponse,
    *,
    session_id: str,
) -> dict[str, Any]:
    followups = []
    if response.query_understanding is not None:
        followups.extend(response.query_understanding.missing_information)
    return {
        "session_id": session_id,
        "understanding": query_understanding_to_public(response.query_understanding),
        "recommendations": [
            recommended_competition_to_public(item) for item in response.results
        ],
        "follow_up_questions": list(dict.fromkeys(followups)),
    }


def public_timeline_to_time_model(value: str) -> str:
    return _TIME_MODEL[value]


def time_model_to_public_timeline(value: str) -> str:
    return _TIMELINE_TYPE[value]


def plan_generation_request_from_public(
    dto: PreparationPlanGenerateRequest,
) -> PlanGenerationRequest:
    return PlanGenerationRequest(
        competition_id=dto.competition.id,
        calendar_today=dto.calendar_today,
        target_date=dto.target_date,
        weekly_hours=_WEEKLY_HOURS[dto.weekly_commitment],
        experience_level=dto.experience_level,
        time_model=public_timeline_to_time_model(dto.timeline_type),
        event_end_date=dto.event_end_date,
        defense_date=dto.defense_date,
        student_context=profile_to_student_context(dto.user_profile),
    )


def plan_generation_result_to_public(draft: PreparationPlanDraft) -> dict[str, Any]:
    optional_by_phase: dict[str, list[PlanTask]] = {}
    for task in draft.optional_tasks:
        optional_by_phase.setdefault(task.phase_id, []).append(task)
    phases = []
    for phase in draft.phases:
        tasks = optional_by_phase.get(phase.phase_id, ())[:3]
        phases.append({
            "key": phase.key,
            "optional_tasks": [
                {
                    "template_key": task.task_id,
                    "title": task.label,
                    "estimated_hours": task.estimated_hours or task.estimated_weeks or 0,
                }
                for task in tasks
            ],
            "personalized_advice": "",
        })
    return {"phases": phases, "global_advice": "; ".join(draft.risk_register)}


def template_phase_to_public(phase) -> dict[str, Any]:
    return {
        "key": phase.key,
        "title": phase.label,
        "weight": phase.weight,
        "required_tasks": [
            {
                "template_key": task.key,
                "title": task.label,
                "estimated_hours": task.estimated_hours,
            }
            for task in phase.tasks
            if task.required
        ],
        "optional_tasks": [
            {
                "template_key": task.key,
                "title": task.label,
                "estimated_hours": task.estimated_hours,
            }
            for task in phase.tasks
            if not task.required
        ],
    }


def _task_from_public(task, phase_id: str) -> PlanTask:
    kind = "user_added" if task.kind == "userAdded" else task.kind
    mandatory = kind == "required"
    return PlanTask(
        task_id=task.id,
        phase_id=phase_id,
        label=task.title,
        is_mandatory=mandatory,
        kind=kind,
        estimated_hours=task.estimated_hours,
        due_date=task.due_date,
    )


def plan_snapshot_to_internal(
    snapshot: PreparationAssistantPlanSnapshot,
) -> PreparationPlanDraft:
    phases: list[PlanPhase] = []
    required: list[PlanTask] = []
    optional: list[PlanTask] = []
    for public_phase in snapshot.phases:
        phase_id = f"phase:{public_phase.key}"
        task_ids = tuple(task.id for task in public_phase.tasks)
        phases.append(PlanPhase(
            phase_id,
            public_phase.key,
            public_phase.key,
            public_phase.start_date,
            public_phase.end_date,
            task_ids,
        ))
        for public_task in public_phase.tasks:
            task = _task_from_public(public_task, phase_id)
            if task.is_mandatory:
                required.append(task)
            else:
                optional.append(task)
    return PreparationPlanDraft(
        plan_id=snapshot.id,
        competition_id=snapshot.competition.id,
        time_model=public_timeline_to_time_model(snapshot.timeline_type),
        target_date=snapshot.target_date,
        event_end_date=snapshot.event_end_date,
        defense_date=snapshot.defense_date,
        phases=tuple(phases),
        tasks=tuple(required),
        optional_tasks=tuple(optional),
        revision=snapshot.revision,
    )


def plan_snapshot_to_public_dict(snapshot: PreparationAssistantPlanSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(mode="json")


def card_result_to_public(card: PlanChangeCard) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": card.id,
        "type": card.type,
        "summary": card.summary,
        "rationale": card.rationale,
        "status": card.status,
    }
    for key in ("target_task_id", "target_phase_key", "advice_text"):
        value = getattr(card, key)
        if value is not None:
            payload[key] = value
    if card.new_date is not None:
        payload["new_date"] = card.new_date.isoformat()
    if card.new_task is not None:
        payload["new_task"] = {
            "title": card.new_task.title,
            "estimated_hours": card.new_task.estimated_hours,
            "due_date": card.new_task.due_date.isoformat(),
            "note": card.new_task.note,
        }
    if card.phase_schedule:
        payload["phase_schedule"] = [
            {
                "phase_key": item.phase_key,
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
            }
            for item in card.phase_schedule
        ]
    if card.rejection_code is not None:
        payload["rejection_code"] = card.rejection_code
    if card.rejection_reason is not None:
        payload["rejection_reason"] = card.rejection_reason
    return payload


def assistant_result_to_public(result: PlanAssistantResult) -> dict[str, Any]:
    return {
        "reply": result.reply,
        "change_set": {
            "id": result.change_set.id,
            "base_plan_revision": result.change_set.base_plan_revision,
            "cards": [card_result_to_public(card) for card in result.change_set.cards],
        },
        "request_id": result.request_id,
    }


def assistant_request_from_public(
    dto: PublicPreparationAssistantRequest,
) -> PlanAssistantRequest:
    history = tuple(
        AssistantHistoryTurn(
            role=turn.role,
            content=turn.content,
            card_results=tuple(
                CardResult(card_id=item.card_id, status=item.status)
                for item in turn.card_results
            ),
        )
        for turn in dto.history
    )
    return PlanAssistantRequest(
        calendar_today=dto.calendar_today,
        base_plan_revision=dto.base_plan_revision,
        plan_snapshot=plan_snapshot_to_internal(dto.plan_snapshot),
        user_message=dto.user_message,
        request_id=dto.request_id,
        history=history,
    )


def assistant_history_turn_to_public(
    *,
    role: str,
    content: str,
    cards: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "role": role,
        "content": content,
        "card_results": [
            {"card_id": card["id"], "status": card["status"]}
            for card in (cards or ())
        ],
    }


__all__ = [
    "assistant_history_turn_to_public",
    "assistant_request_from_public",
    "assistant_result_to_public",
    "catalog_card_to_public",
    "competition_detail_to_public",
    "plan_generation_request_from_public",
    "plan_generation_result_to_public",
    "plan_snapshot_to_internal",
    "plan_snapshot_to_public_dict",
    "recommendation_request_from_public",
    "recommendation_response_to_public",
    "template_phase_to_public",
]
