"""Recommend request/response contracts (overview §3)."""
from __future__ import annotations

from dataclasses import dataclass

from dext_grounded import SourceRef


@dataclass(frozen=True, slots=True)
class CompetitionPreferences:
    categories: tuple[str, ...] = ()
    major: str | None = None
    grade: str | None = None
    experience_level: str | None = None   # beginner|intermediate|advanced
    weekly_hours: int | None = None
    target_goal: str | None = None
    team_preference: str | None = None    # solo|team|either
    time_window: str | None = None
    risk_tolerance: str = "medium"         # low|medium|high

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "categories",
            tuple(self.categories) if self.categories is not None else (),
        )


@dataclass(frozen=True, slots=True)
class CompetitionRecommendRequest:
    query_text: str
    preferences: CompetitionPreferences
    student_context: object | None = None   # dext_grounded.StudentContext
    limit: int = 6
    diagnostics_level: str = "summary"      # none|summary|debug


@dataclass(frozen=True, slots=True)
class CompetitionQueryUnderstanding:
    interests: tuple[str, ...] = ()
    major_fit: str | None = None
    grade_fit: str | None = None
    experience_level: str | None = None
    weekly_hours: int | None = None
    target_goal: str | None = None
    team_preference: str | None = None
    missing_information: tuple[str, ...] = ()
    needs_clarification: bool = False
    confidence: float = 0.0

    def __post_init__(self) -> None:
        for f in ("interests", "missing_information"):
            object.__setattr__(
                self, f,
                tuple(getattr(self, f)) if getattr(self, f) is not None else (),
            )


@dataclass(frozen=True, slots=True)
class CompetitionWarning:
    code: str
    message: str
    competition_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecommendedCompetition:
    competition_id: str
    display_name: str
    category: str
    summary: str
    fit_level: str
    score: float
    score_components: dict[str, float]
    eligibility_notes: str
    schedule_notes: str
    team_notes: str
    preparation_effort: str
    short_reasons: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    official_links: tuple[str, ...] = ()
    evidence_status: str = "grounded"   # grounded|partial|uncertain
    freshness_notice: str | None = None
    internal_source_refs: tuple[SourceRef, ...] = ()
    available_actions: tuple[str, ...] = (
        "detail", "create_plan", "ask_rules", "compare",
    )

    def __post_init__(self) -> None:
        for f in ("short_reasons", "risk_flags", "official_links",
                  "internal_source_refs", "available_actions"):
            object.__setattr__(
                self, f,
                tuple(getattr(self, f)) if getattr(self, f) is not None else (),
            )


@dataclass(frozen=True, slots=True)
class CompetitionRecommendResponse:
    knowledge_base_version: str
    query_understanding: CompetitionQueryUnderstanding | None
    competition_ranking_profile_version: str = "competition.ranking.v1"
    generation_profile_version: str = "competition.query-understanding.heuristic-v1"
    results: tuple[RecommendedCompetition, ...] = ()
    warnings: tuple[CompetitionWarning, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "results",
            tuple(self.results) if self.results is not None else ())
        object.__setattr__(
            self, "warnings",
            tuple(self.warnings) if self.warnings is not None else ())


__all__ = [
    "CompetitionPreferences",
    "CompetitionQueryUnderstanding",
    "CompetitionRecommendRequest",
    "CompetitionRecommendResponse",
    "CompetitionWarning",
    "RecommendedCompetition",
]
