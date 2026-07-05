"""Structured errors + warnings (overview §17, foundations §4).

Every drop/skip/retry carries a reason code + count. Errors never crash the
process; they are returned as structured values. Codes are registered here
up-front; later phases wire the trigger logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class RecommendationErrorCode(str, Enum):
    ACTIVE_BUILD_UNAVAILABLE = "active_build_unavailable"
    ACTIVE_BUILD_INCONSISTENT = "active_build_inconsistent"
    EMBEDDING_FINGERPRINT_MISMATCH = "embedding_fingerprint_mismatch"
    NO_CANDIDATES_AFTER_FILTERS = "no_candidates_after_filters"
    PAYLOAD_PREFILTER_DEGRADED = "payload_prefilter_degraded"
    INSUFFICIENT_FACTS = "insufficient_facts"
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED_CONTACT = "unauthorized_contact"
    UNAUTHORIZED_REVIEW = "unauthorized_review"
    GENERATION_UNAVAILABLE = "generation_unavailable"
    ORG_UNIT_IDS_COVERAGE_INSUFFICIENT = "org_unit_ids_coverage_insufficient"
    PROFILE_HASH_COVERAGE_INSUFFICIENT = "profile_hash_coverage_insufficient"
    ROLE_STATUS_COVERAGE_INSUFFICIENT = "role_status_coverage_insufficient"
    ELIGIBILITY_COVERAGE_INSUFFICIENT = "eligibility_coverage_insufficient"
    ORG_UNIT_FILTER_UNAVAILABLE = "org_unit_filter_unavailable"
    RANKING_PROFILE_UNAVAILABLE = "ranking_profile_unavailable"
    NEEDS_CLARIFICATION = "needs_clarification"
    INVALID_INTENT = "invalid_intent"
    WEAK_EXPLANATION = "weak_explanation"
    REQUEST_TIMEOUT = "request_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    VECTOR_UNAVAILABLE = "vector_unavailable"
    HYDRATE_UNAVAILABLE = "hydrate_unavailable"
    DETAILS_UNAVAILABLE = "details_unavailable"
    PREFERENCE_RELAXED = "preference_relaxed"
    INVALID_CONVERSATION_STATE = "invalid_conversation_state"
    MORE_MENTORS_REQUIRES_PRIOR = "more_mentors_requires_prior"
    SAME_FIELD_REQUIRES_ANCHOR = "same_field_requires_anchor"
    DETAIL_FOLLOWUP_REQUIRES_ANCHOR = "detail_followup_requires_anchor"
    ANCHOR_NOT_IN_ACTIVE_BUILD = "anchor_not_in_active_build"
    INTENT_CLASSIFICATION_UNAVAILABLE = "intent_classification_unavailable"
    FOLLOWUP_GENERATION_UNAVAILABLE = "followup_generation_unavailable"
    GENERATION_PARSE_ERROR = "generation_parse_error"
    NO_GROUNDED_OUTPUT = "no_grounded_output"
    INSUFFICIENT_STUDENT_CONTEXT = "insufficient_student_context"
    CONTENT_POLICY_REFUSAL = "content_policy_refusal"
    POLITICAL_SENSITIVE = "political_sensitive"
    PERSONAL_ATTACK = "personal_attack"
    SEXUAL_CONTENT = "sexual_content"
    VIOLENT_CONTENT = "violent_content"
    MENTOR_ATTACK = "mentor_attack"


@dataclass(frozen=True, slots=True)
class RecommendationError:
    code: RecommendationErrorCode
    severity: ErrorSeverity
    message: str
    build_id: str | None = None
    retryable: bool = False
    operator_action: str | None = None
    user_action: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "build_id": self.build_id,
            "retryable": self.retryable,
            "operator_action": self.operator_action,
            "user_action": self.user_action,
        }


@dataclass(frozen=True, slots=True)
class RecommendationRuntimeError(RuntimeError):
    """Secret-free operational failure from the live composition root."""

    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.message)


__all__ = [
    "ErrorSeverity",
    "RecommendationError",
    "RecommendationErrorCode",
    "RecommendationRuntimeError",
]
