"""Competition structured errors + warnings (overview §7).

Mirrors dext_recommend.errors: codes registered up-front, every drop/skip/
retry carries a reason code. Errors never crash the process.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


class CompetitionErrorCode(str, Enum):
    KNOWLEDGE_BASE_UNAVAILABLE = "knowledge_base_unavailable"
    KNOWLEDGE_BASE_STALE = "knowledge_base_stale"
    CATALOG_UNAVAILABLE = "catalog_unavailable"
    NO_CANDIDATES_AFTER_FILTERS = "no_candidates_after_filters"
    NEEDS_CLARIFICATION = "needs_clarification"
    INVALID_REQUEST = "invalid_request"
    STALE_FACT = "stale_fact"
    UNSAFE_ADVICE = "unsafe_advice"
    UNAUTHORIZED_CONTACT = "unauthorized_contact"
    GENERATION_UNAVAILABLE = "generation_unavailable"
    GENERATION_FALLBACK = "generation_fallback"
    PLAN_INVALID = "plan_invalid"
    CHANGE_CARD_REJECTED = "change_card_rejected"
    PLAN_REVISION_STALE = "plan_revision_stale"
    LLM_UNAVAILABLE = "llm_unavailable"


@dataclass(frozen=True, slots=True)
class CompetitionError:
    code: CompetitionErrorCode
    severity: ErrorSeverity
    message: str
    retryable: bool = False
    operator_action: str | None = None
    user_action: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "retryable": self.retryable,
            "operator_action": self.operator_action,
            "user_action": self.user_action,
        }


__all__ = ["CompetitionErrorCode", "CompetitionError", "ErrorSeverity"]
