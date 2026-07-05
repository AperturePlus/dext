"""Pydantic DTOs for C7 HTTP request validation."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompetitionSnapshot(StrictModel):
    id: StrictStr
    name: StrictStr
    category: StrictStr
    rules_summary: dict


class CompetitionRecommendationRequest(StrictModel):
    prompt: StrictStr = Field(min_length=1)
    session_id: StrictStr | None = None
    profile: dict | None = None


class PreparationPlanGenerateRequest(StrictModel):
    competition: CompetitionSnapshot
    calendar_today: date
    target_date: date
    timeline_type: Literal["eventWindow", "submission"]
    event_end_date: date | None = None
    defense_date: date | None = None
    weekly_commitment: Literal["hours3to5", "hours6to10", "hours11to15", "hours16plus"]
    experience_level: Literal["beginner", "intermediate", "experienced"]
    phase_keys: list[StrictStr] = Field(min_length=1)
    user_profile: dict | None = None


class PreparationDiagnoseAnswer(StrictModel):
    question_key: StrictStr
    answer: StrictStr


class PreparationDiagnoseRequest(StrictModel):
    competition: CompetitionSnapshot
    profile: dict | None = None
    answers: list[PreparationDiagnoseAnswer] = Field(default_factory=list)


class PreparationTaskSnapshot(StrictModel):
    id: StrictStr
    title: StrictStr
    kind: Literal["required", "optional", "userAdded"]
    estimated_hours: float
    due_date: date
    completed_at: datetime | None = None


class PreparationPhaseSnapshot(StrictModel):
    key: StrictStr
    start_date: date
    end_date: date
    tasks: list[PreparationTaskSnapshot] = Field(default_factory=list)


class CardResultRequest(StrictModel):
    card_id: StrictStr
    status: Literal["pending", "rejected", "applied", "declined", "stale"]


class PreparationAssistantHistoryTurnRequest(StrictModel):
    role: Literal["user", "assistant"]
    content: StrictStr
    card_results: list[CardResultRequest] = Field(default_factory=list)


class PreparationAssistantPlanSnapshot(StrictModel):
    id: StrictStr
    competition: CompetitionSnapshot
    target_date: date
    timeline_type: Literal["eventWindow", "submission"]
    event_end_date: date | None = None
    defense_date: date | None = None
    revision: int = Field(ge=0)
    weekly_commitment: Literal["hours3to5", "hours6to10", "hours11to15", "hours16plus"]
    experience_level: Literal["beginner", "intermediate", "experienced"]
    status: Literal["draft", "active", "completed", "archived"]
    phases: list[PreparationPhaseSnapshot] = Field(default_factory=list)
    personalized_summary: StrictStr | None = None
    created_at: datetime
    updated_at: datetime
    tight_schedule: bool = False
    overload: bool = False


class PreparationAssistantRequest(StrictModel):
    calendar_today: date
    base_plan_revision: int = Field(ge=0)
    plan_snapshot: PreparationAssistantPlanSnapshot
    user_message: StrictStr = Field(min_length=1)
    request_id: StrictStr = Field(min_length=1)
    history: list[PreparationAssistantHistoryTurnRequest] = Field(default_factory=list)


__all__ = [
    "CompetitionRecommendationRequest",
    "CompetitionSnapshot",
    "PreparationAssistantPlanSnapshot",
    "PreparationAssistantRequest",
    "PreparationDiagnoseRequest",
    "PreparationPlanGenerateRequest",
]
