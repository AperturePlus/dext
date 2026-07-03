"""Pydantic DTOs for the public recommendation HTTP contract."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcademicScore(StrictModel):
    gpa_bucket: StrictStr | None = None
    rank_bucket: StrictStr | None = None


class Competition(StrictModel):
    name: StrictStr | None = None
    level: StrictStr | None = None
    award: StrictStr | None = None


class ResearchItem(StrictModel):
    title: StrictStr | None = None
    summary: StrictStr | None = None
    role: StrictStr | None = None


class UserProfile(StrictModel):
    name: StrictStr | None = None
    gender: Literal["male", "female", "other", "undisclosed"] | None = None
    degree_stage: StrictStr | None = None
    target_degree: StrictStr | None = None
    school: StrictStr | None = None
    major: StrictStr | None = None
    research_interests: list[StrictStr] = Field(default_factory=list, max_length=50)
    highlights: StrictStr | None = Field(default=None, max_length=2000)
    score: AcademicScore | None = None
    competitions: list[Competition] = Field(default_factory=list, max_length=20)
    research: list[ResearchItem] = Field(default_factory=list, max_length=20)


class MentorRecommendationRequest(StrictModel):
    prompt: StrictStr = Field(min_length=1, max_length=4096)
    session_id: UUID | None = None
    profile: UserProfile | None = None
    limit: int = Field(default=10, ge=1, le=50)


class SessionCreateRequest(StrictModel):
    kind: Literal["general", "professor"] = "general"
    professor_id: StrictStr | None = None


class TurnCreateRequest(StrictModel):
    text: StrictStr = Field(min_length=1, max_length=4096)
    request_id: UUID
    expected_revision: int = Field(ge=0)


class AttemptCreateRequest(StrictModel):
    session_id: UUID
    request_id: UUID
    expected_revision: int = Field(ge=0)


class ForkCreateRequest(StrictModel):
    source_turn_id: UUID
    professor_id: StrictStr | None = None


class FeedbackRequest(StrictModel):
    feedback: Literal["none", "like", "dislike"]


class ChatMessageRequest(StrictModel):
    session_id: UUID
    message: StrictStr = Field(min_length=1, max_length=4096)
    professor_id: StrictStr | None = None


class ProfessorCompareRequest(StrictModel):
    professor_ids: list[StrictStr] = Field(min_length=2, max_length=3)


class MatchAnalysisRequest(StrictModel):
    profile: UserProfile


class OutreachEmailRequest(StrictModel):
    profile: UserProfile
    locale: StrictStr | None = Field(default=None, max_length=16)


class FavoriteRequest(StrictModel):
    professor_id: StrictStr | None = None
    snapshot: dict | None = None


class HistoryItem(StrictModel):
    type: Literal["mentor", "competition"] = "mentor"
    session_id: UUID
    prompt: StrictStr = Field(max_length=4096)
    created_at: StrictStr
    summary: StrictStr = Field(max_length=1000)
    research_interests: list[StrictStr] = Field(default_factory=list, max_length=50)
    preferred_locations: list[StrictStr] = Field(default_factory=list, max_length=50)
    recommendation_count: int = Field(ge=0)


__all__ = [
    "AcademicScore",
    "AttemptCreateRequest",
    "ChatMessageRequest",
    "FavoriteRequest",
    "FeedbackRequest",
    "ForkCreateRequest",
    "HistoryItem",
    "MatchAnalysisRequest",
    "MentorRecommendationRequest",
    "OutreachEmailRequest",
    "ProfessorCompareRequest",
    "SessionCreateRequest",
    "StrictModel",
    "TurnCreateRequest",
    "UserProfile",
]
