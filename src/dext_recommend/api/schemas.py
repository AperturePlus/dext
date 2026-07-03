"""Pydantic DTOs for the public recommendation HTTP contract."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcademicScore(StrictModel):
    gpa: StrictFloat | StrictInt | None = None
    scale: StrictFloat | StrictInt | None = None
    rank_mode: Literal["none", "percent", "ordinal"] | None = None
    percent: StrictInt | None = None
    rank_position: StrictInt | None = None
    rank_total: StrictInt | None = None


class Competition(StrictModel):
    name: StrictStr
    level: StrictStr | None = None
    award: StrictStr | None = None
    year: StrictStr | None = None


class ResearchItem(StrictModel):
    type: Literal["paper", "project", "patent", "other"]
    title: StrictStr
    role: StrictStr | None = None
    venue_or_status: StrictStr | None = None
    year: StrictStr | None = None


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
    professor_id: StrictStr


class FeedbackRequest(StrictModel):
    feedback: Literal["none", "like", "dislike"]


class ChatMessageRequest(StrictModel):
    session_id: UUID
    message: StrictStr = Field(min_length=1, max_length=4096)
    professor_id: StrictStr | None = None


class RecommendationRecap(StrictModel):
    professor_id: StrictStr | None = None
    name: StrictStr | None = None
    university: StrictStr | None = None
    research_fields: list[StrictStr] = Field(default_factory=list, max_length=50)


class QuickActionsRequest(StrictModel):
    follow_up: StrictStr = Field(max_length=4096)
    last_recommendations: list[RecommendationRecap] = Field(default_factory=list, max_length=5)


class ProfessorCompareRequest(StrictModel):
    professor_ids: list[StrictStr] = Field(min_length=2, max_length=3)


class MatchAnalysisRequest(StrictModel):
    profile: UserProfile


class OutreachEmailRequest(StrictModel):
    profile: UserProfile
    locale: StrictStr | None = Field(default=None, max_length=16)


class FavoriteRequest(StrictModel):
    professor_id: StrictStr
    name: StrictStr
    university: StrictStr
    college: StrictStr
    title: StrictStr
    research_fields: list[StrictStr] = Field(default_factory=list)
    homepage_url: StrictStr | None = None
    favorited_at: StrictStr | None = None


class UserFeedbackContext(StrictModel):
    route: StrictStr | None = None
    session_id: StrictStr | None = None
    message_id: StrictStr | None = None
    professor_id: StrictStr | None = None
    competition_id: StrictStr | None = None
    prompt: StrictStr | None = None
    app_version: StrictStr | None = None
    data_source_mode: Literal["llm", "http", "mock"] | None = None


class UserFeedbackRequest(StrictModel):
    id: StrictStr
    type: Literal["recommendation", "missing_professor", "bug", "other"]
    content: StrictStr = Field(min_length=1, max_length=5000)
    contact: StrictStr | None = None
    context: UserFeedbackContext = Field(default_factory=UserFeedbackContext)
    created_at: StrictStr


class HistoryItem(StrictModel):
    type: Literal["mentor", "competition"] = "mentor"
    session_id: StrictStr
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
    "QuickActionsRequest",
    "RecommendationRecap",
    "SessionCreateRequest",
    "StrictModel",
    "TurnCreateRequest",
    "UserProfile",
    "UserFeedbackRequest",
]
