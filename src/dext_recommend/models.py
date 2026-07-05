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


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    session_id: str
    through_turn_id: str | None
    text: str
    created_at: str            # UTC ISO-8601


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
    conversation_model_context: Mapping[str, object] | None = None
    limit: int = 10
    oversample: int = 200
    ranking_mode: str = "explainable_precision"
    review_policy: str = "exclude"          # exclude|include_downranked
    include_contacts: bool = False
    diagnostics_level: str = "summary"      # none|summary|debug

    def __post_init__(self) -> None:
        if self.conversation_model_context is not None:
            object.__setattr__(
                self,
                "conversation_model_context",
                freeze_mapping(self.conversation_model_context),
            )


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
    generation_profile_version: str | None = None
    phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        # accept list/tuple/generator input; store as tuple (spec §3 deep immutability)
        for _f in ("results", "suggested_followups", "warnings", "phase_diagnostics"):
            object.__setattr__(
                self, _f,
                tuple(getattr(self, _f)) if getattr(self, _f) is not None else ()
            )


@dataclass(frozen=True, slots=True)
class DetailFollowupResponse:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str
    grounded_rules_manifest_hash: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    anchor_entity_id: str
    anchor_display_name: str
    answer: str
    claims: tuple
    cited_refs: tuple
    warnings: tuple[RecommendationWarning, ...]
    phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        for _f in ("claims", "cited_refs", "warnings", "phase_diagnostics"):
            object.__setattr__(
                self, _f,
                tuple(getattr(self, _f)) if getattr(self, _f) is not None else ()
            )


@dataclass(frozen=True, slots=True)
class ConversationDispatchResult:
    kind: str   # recommendation|detail_followup|clarification|error
    context: "ConversationContext | None"
    recommendation: "RecommendResponse | None"
    detail_followup: "DetailFollowupResponse | None"
    issues: tuple[RecommendationWarning, ...]
    generation_profile_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        kind = self.kind
        has_rec = self.recommendation is not None
        has_det = self.detail_followup is not None
        if kind == "recommendation":
            if not has_rec or has_det:
                raise ValueError("kind=recommendation requires only recommendation payload")
        elif kind == "detail_followup":
            if not has_det or has_rec:
                raise ValueError("kind=detail_followup requires only detail_followup payload")
        elif kind in ("clarification", "error"):
            if has_rec or has_det:
                raise ValueError(f"kind={kind} must carry no payload")
        else:
            raise ValueError(f"unknown kind: {kind!r}")
        if kind == "clarification":
            if not any(w.code == "needs_clarification" for w in self.issues):
                raise ValueError("kind=clarification requires a needs_clarification issue")
        if kind == "error":
            if not any(w.severity == "error" for w in self.issues):
                raise ValueError("kind=error requires at least one severity=error issue")


@dataclass(frozen=True, slots=True)
class MatchAnalysis:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str
    grounded_rules_manifest_hash: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    entity_id: str
    display_name: str
    summary: str
    dimension_scores: Mapping[str, float]
    next_steps: tuple[str, ...]
    claims: tuple
    cited_refs: tuple
    warnings: tuple[RecommendationWarning, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "dimension_scores", freeze_mapping(self.dimension_scores))
        for _f in ("next_steps", "claims", "cited_refs", "warnings"):
            object.__setattr__(self, _f, tuple(getattr(self, _f) or ()))


@dataclass(frozen=True, slots=True)
class OutreachDraft:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str
    grounded_rules_manifest_hash: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    entity_id: str
    display_name: str
    subject: str
    body: str
    tone: str
    language: str
    authorized_contacts: Mapping[str, str] = field(default_factory=dict)
    claims: tuple = ()
    cited_refs: tuple = ()
    warnings: tuple[RecommendationWarning, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorized_contacts", freeze_mapping(self.authorized_contacts))
        for _f in ("claims", "cited_refs", "warnings"):
            object.__setattr__(self, _f, tuple(getattr(self, _f) or ()))


@dataclass(frozen=True, slots=True)
class ProfessorComparison:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str
    grounded_rules_manifest_hash: str
    embedding_fingerprint: str
    taxonomy_version: str | None
    entity_ids: tuple[str, ...]
    display_names: Mapping[str, str]
    summary: str
    professor_notes: Mapping[str, tuple[str, ...]]
    evidence_gaps: Mapping[str, tuple[str, ...]]
    claims: tuple
    cited_refs: tuple
    warnings: tuple[RecommendationWarning, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "entity_ids", tuple(self.entity_ids or ()))
        object.__setattr__(self, "display_names", freeze_mapping(self.display_names))
        object.__setattr__(self, "professor_notes", freeze_mapping(self.professor_notes))
        object.__setattr__(self, "evidence_gaps", freeze_mapping(self.evidence_gaps))
        for _f in ("claims", "cited_refs", "warnings"):
            object.__setattr__(self, _f, tuple(getattr(self, _f) or ()))


@dataclass(frozen=True, slots=True)
class AuxiliaryGenerationResult:
    kind: str  # match_analysis|outreach_email|professor_comparison|error
    match_analysis: MatchAnalysis | None = None
    outreach_draft: OutreachDraft | None = None
    professor_comparison: ProfessorComparison | None = None
    issues: tuple[RecommendationWarning, ...] = ()
    generation_profile_version: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        payloads = {
            "match_analysis": self.match_analysis,
            "outreach_email": self.outreach_draft,
            "professor_comparison": self.professor_comparison,
        }
        if self.kind in payloads:
            if payloads[self.kind] is None:
                raise ValueError(f"kind={self.kind} requires matching payload")
            if any(value is not None for key, value in payloads.items() if key != self.kind):
                raise ValueError(f"kind={self.kind} carries mixed payloads")
        elif self.kind == "error":
            if any(value is not None for value in payloads.values()):
                raise ValueError("kind=error must carry no payload")
            if not any(w.severity == "error" for w in self.issues):
                raise ValueError("kind=error requires at least one severity=error issue")
        else:
            raise ValueError(f"unknown kind: {self.kind!r}")


__all__ = [
    "AuxiliaryGenerationResult",
    "ConversationContext",
    "ConversationDispatchResult",
    "ConversationSummary",
    "DetailFollowupResponse",
    "MatchAnalysis",
    "OutreachDraft",
    "ProfessorComparison",
    "QueryDiagnostics",
    "QueryUnderstanding",
    "PhaseDiagnostic",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
]
