# src/dext_recommend/models.py
"""Internal request/response models (overview §8, §10).

Field names match the overview spec strictly. StudentContext and SourceRef
are imported from the shared dext_grounded contract — never redefined here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dext_grounded import SourceRef, StudentContext


# ---- Filters & conversation (overview §8) ----

@dataclass(frozen=True, slots=True)
class RecommendationFilters:
    university_ids: list[str] = field(default_factory=list)
    city_names: list[str] = field(default_factory=list)
    org_unit_ids: list[str] = field(default_factory=list)
    title_families: list[str] = field(default_factory=list)
    master_eligibility: str = "any"          # any|confirmed
    phd_eligibility: str = "any"              # any|confirmed
    topic_ids: list[str] = field(default_factory=list)
    topic_filter_mode: str = "soft"           # soft|hard


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
    prior_result_entity_ids: list[str] = field(default_factory=list)


# ---- Query understanding (overview §10) ----

@dataclass(frozen=True, slots=True)
class QueryUnderstanding:
    research_interests: list[str]
    preferred_universities: list[str]
    preferred_cities: list[str]
    preferred_org_units: list[str]
    degree_goal: str | None
    mentor_eligibility_requirement: str | None
    missing_information: list[str]
    needs_clarification: bool
    confidence: float


@dataclass(frozen=True, slots=True)
class QueryDiagnostics:
    query_length: int
    language_summary: str | None
    filter_summary: str | None
    recall_count: int = 0
    post_filter_count: int = 0
    returned_count: int = 0


@dataclass(frozen=True, slots=True)
class RecommendationWarning:
    code: str
    message: str
    severity: str = "warning"


# ---- Result & response (overview §8, §5) ----

@dataclass(frozen=True, slots=True)
class RecommendedProfessor:
    entity_id: str
    display_name: str
    university: str
    org_units: list[str]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_summary: str | None
    match_level: str                       # excellent|strong|possible|weak
    short_reasons: list[str]
    score: float
    score_components: dict[str, float]
    matched_topics: list[str]
    matched_statements: list[str]
    matched_publications: list[str]
    evidence_refs: list[SourceRef]
    risk_flags: list[str]
    available_actions: list[str]            # detail|match|email|compare|favorite|follow_up


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
    results: list[RecommendedProfessor]
    suggested_followups: list[str]
    warnings: list[RecommendationWarning]


__all__ = [
    "ConversationContext",
    "QueryDiagnostics",
    "QueryUnderstanding",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
]
