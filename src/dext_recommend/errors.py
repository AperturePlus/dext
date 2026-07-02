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
    UNSUPPORTED_FOR_RECOMMEND_CORE = "unsupported_for_recommend_core"
    NEEDS_CLARIFICATION = "needs_clarification"
    INVALID_INTENT = "invalid_intent"
    MISSING_PRIOR_RESULTS = "missing_prior_results"
    MISSING_ANCHOR = "missing_anchor"
    WEAK_EXPLANATION = "weak_explanation"
    REQUEST_TIMEOUT = "request_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    VECTOR_UNAVAILABLE = "vector_unavailable"
    HYDRATE_UNAVAILABLE = "hydrate_unavailable"
    DETAILS_UNAVAILABLE = "details_unavailable"


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


__all__ = ["ErrorSeverity", "RecommendationError", "RecommendationErrorCode"]
