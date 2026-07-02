# src/dext_recommend/models.py
"""Internal request/response models (overview §8, §10).

Field names match the overview spec strictly. StudentContext and SourceRef
are imported from the shared dext_grounded contract — never redefined here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from dext_grounded import SourceRef, StudentContext
from dext_recommend._immutable import freeze_mapping


# ---- Filters & conversation (overview §8) ----

@dataclass(frozen=True, slots=True)
class RecommendationFilters:
    university_ids: tuple[str, ...] = ()
    city_names: tuple[str, ...] = ()
    org_unit_ids: tuple[str, ...] = ()
    title_families: tuple[str, ...] = ()
    master_eligibility: str = "any"          # any|confirmed
    phd_eligibility: str = "any"              # any|confirmed
    topic_ids: tuple[str, ...] = ()
    topic_filter_mode: str = "soft"           # soft|hard

    def __post_init__(self) -> None:
        # accept list/tuple/generator input; store as tuple (spec §3 deep immutability)
        for _f in ("university_ids", "city_names", "org_unit_ids",
                   "title_families", "topic_ids"):
            object.__setattr__(
                self, _f,
                tuple(getattr(self, _f)) if getattr(self, _f) is not None else (),
            )


@dataclass(frozen=True, slots=True)
class ConversationContext:
    session_id: str | None = None
    turn_id: str | None = None
    main_session_id: str | None = None        # fork relationship
    source_turn_id: str | None = None
    anchor_entity_id: str | None = None
    intent: str | None = None
    # new_search|more_mentors|same_field|refine_direction|detail_followup
    intent_source: str | None = None          # explicit|implicit
    intent_confidence: float | None = None
    prior_result_entity_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "prior_result_entity_ids",
            tuple(self.prior_result_entity_ids) if self.prior_result_entity_ids is not None else ()
        )


# ---- Query understanding (overview §10) ----

@dataclass(frozen=True, slots=True)
class QueryUnderstanding:
    research_interests: tuple[str, ...]
    preferred_universities: tuple[str, ...]
    preferred_cities: tuple[str, ...]
    preferred_org_units: tuple[str, ...]
    degree_goal: str | None
    mentor_eligibility_requirement: str | None
    missing_information: tuple[str, ...]
    needs_clarification: bool
    confidence: float

    def __post_init__(self) -> None:
        for name in (
            "research_interests",
            "preferred_universities",
            "preferred_cities",
            "preferred_org_units",
            "missing_information",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))


@dataclass(frozen=True, slots=True)
class QueryDiagnostics:
    query_length: int
    language_summary: str | None
    filter_summary: str | None
    recall_count: int = 0
    post_filter_count: int = 0
    returned_count: int = 0
    steps_used: int = 0


@dataclass(frozen=True, slots=True)
class RecommendationWarning:
    code: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class PhaseDiagnostic:
    phase: str
    attempt: int | None
    elapsed_ms: float
    error_code: str | None


# ---- Result & response (overview §8, §5) ----

@dataclass(frozen=True, slots=True)
class RecommendedProfessor:
    entity_id: str
    display_name: str
    university: str
    org_units: tuple[str, ...]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_summary: str | None
    match_level: str                       # excellent|strong|possible|weak
    short_reasons: tuple[str, ...]
    score: float
    score_components: Mapping[str, float]
    matched_topics: tuple[str, ...]
    matched_statements: tuple[str, ...]
    matched_publications: tuple[str, ...]
    evidence_refs: tuple[SourceRef, ...]
    risk_flags: tuple[str, ...]
    available_actions: tuple[str, ...]            # detail|match|email|compare|favorite|follow_up

    def __post_init__(self) -> None:
        # accept list/tuple/generator input; store as tuple (spec §3 deep immutability)
        for _f in ("org_units", "short_reasons", "matched_topics",
                   "matched_statements", "matched_publications",
                   "evidence_refs", "risk_flags", "available_actions"):
            object.__setattr__(
                self, _f,
                tuple(getattr(self, _f)) if getattr(self, _f) is not None else ()
            )
        object.__setattr__(self, "score_components", freeze_mapping(self.score_components))


@dataclass(frozen=True, slots=True)
class RecommendRequest:
    query_text: str
    student_context: StudentContext | None = None
    filters: RecommendationFilters = field(default_factory=RecommendationFilters)
    conversation_context: ConversationContext | None = None
    limit: int = 10
    oversample: int = 200
    ranking_mode: str = "explainable_precision"
    review_policy: str = "exclude"          # exclude|include_downranked
    include_contacts: bool = False
    diagnostics_level: str = "summary"      # none|summary|debug


@dataclass(frozen=True, slots=True)
class RecommendResponse:
    build_id: str
    ranking_profile_version: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    query_understanding: QueryUnderstanding
    query: QueryDiagnostics
    results: tuple[RecommendedProfessor, ...]
    suggested_followups: tuple[str, ...]
    warnings: tuple[RecommendationWarning, ...]
    phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        # accept list/tuple/generator input; store as tuple (spec §3 deep immutability)
        for _f in ("results", "suggested_followups", "warnings", "phase_diagnostics"):
            object.__setattr__(
                self, _f,
                tuple(getattr(self, _f)) if getattr(self, _f) is not None else ()
            )


__all__ = [
    "ConversationContext",
    "QueryDiagnostics",
    "QueryUnderstanding",
    "PhaseDiagnostic",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
]
