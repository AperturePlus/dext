"""Plan-assistant contracts (overview §6, C6 spec).

The assistant emits change cards only. It never mutates a preparation plan:
validation, user approval and actual application are three independent axes
which C7 exposes as one public status per docs/appside/openapi.yaml.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

from dext_competition.contracts.plan import PreparationPlanDraft
from dext_competition.errors import CompetitionError, ErrorSeverity
from dext_grounded import SourceRef

CHANGE_CARD_TYPES = frozenset({
    "move_task",
    "add_task",
    "delete_task",
    "reschedule_phase",
    "append_advice",
})
CHANGE_CARD_STATUSES = frozenset({
    "pending",
    "rejected",
    "applied",
    "declined",
    "stale",
})
CHANGE_CARD_REJECTION_CODES = frozenset({
    "missing_required_fields",
    "target_task_not_found",
    "target_phase_not_found",
    "completed_task_protected",
    "date_out_of_range",
    "invalid_add_task_fields",
    "required_task_delete_forbidden",
    "phase_schedule_invalid",
    "invalid_advice_fields",
})
VALIDATION_STATUSES = frozenset({"pending", "passed", "rejected"})
APPROVAL_STATUSES = frozenset({"pending", "accepted", "declined"})
APPLICATION_STATUSES = frozenset({"not_applied", "applied", "failed"})


def collapse_change_card_status(
    *,
    validation_status: str,
    approval_status: str,
    application_status: str,
) -> str:
    """Collapse C6's internal three-axis state to the OpenAPI public status."""

    if validation_status == "rejected":
        return "rejected"
    if validation_status == "pending":
        return "pending"
    if approval_status == "declined":
        return "declined"
    if approval_status != "accepted":
        return "pending"
    if application_status == "applied":
        return "applied"
    if application_status == "failed":
        return "stale"
    return "pending"


@dataclass(frozen=True, slots=True)
class PreparationNewTaskDraft:
    title: str
    estimated_hours: int
    due_date: date
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("new task title must be non-empty")
        object.__setattr__(self, "title", self.title.strip())
        if (
            isinstance(self.estimated_hours, bool)
            or not isinstance(self.estimated_hours, int)
            or not 1 <= self.estimated_hours <= 200
        ):
            raise ValueError("estimated_hours must be an integer in [1, 200]")
        if not isinstance(self.due_date, date):
            raise ValueError("new task due_date must be a date")
        if self.note is not None:
            object.__setattr__(self, "note", str(self.note).strip() or None)


@dataclass(frozen=True, slots=True)
class PreparationPhaseScheduleDraft:
    phase_key: str
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.phase_key, str) or not self.phase_key.strip():
            raise ValueError("phase_key must be non-empty")
        object.__setattr__(self, "phase_key", self.phase_key.strip())
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise ValueError("phase schedule dates must be dates")
        if self.start_date > self.end_date:
            raise ValueError("phase schedule start_date must not be after end_date")


@dataclass(frozen=True, slots=True)
class CardResult:
    card_id: str
    status: str

    def __post_init__(self) -> None:
        if not isinstance(self.card_id, str) or not self.card_id.strip():
            raise ValueError("card_id must be non-empty")
        if self.status not in CHANGE_CARD_STATUSES:
            raise ValueError(f"unsupported card result status: {self.status!r}")


@dataclass(frozen=True, slots=True)
class AssistantHistoryTurn:
    role: str
    content: str
    card_results: tuple[CardResult, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError(f"unsupported assistant history role: {self.role!r}")
        if not isinstance(self.content, str):
            raise ValueError("assistant history content must be a string")
        object.__setattr__(self, "card_results", tuple(self.card_results or ()))


@dataclass(frozen=True, slots=True)
class PlanChangeCard:
    id: str
    type: str
    summary: str
    rationale: str
    target_task_id: str | None = None
    target_phase_key: str | None = None
    new_date: date | None = None
    new_task: PreparationNewTaskDraft | None = None
    phase_schedule: tuple[PreparationPhaseScheduleDraft, ...] = ()
    advice_text: str | None = None
    internal_source_refs: tuple[SourceRef, ...] = ()
    validation_status: str = "pending"
    approval_status: str = "pending"
    application_status: str = "not_applied"
    rejection_code: str | None = None
    rejection_reason: str | None = None
    status: str = "pending"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("change card id must be non-empty")
        object.__setattr__(self, "id", self.id.strip())
        if self.type not in CHANGE_CARD_TYPES:
            raise ValueError(f"unsupported change card type: {self.type!r}")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("change card summary must be non-empty")
        if not isinstance(self.rationale, str):
            raise ValueError("change card rationale must be a string")
        object.__setattr__(self, "summary", self.summary.strip())
        object.__setattr__(self, "rationale", self.rationale.strip())
        object.__setattr__(self, "phase_schedule", tuple(self.phase_schedule or ()))
        object.__setattr__(
            self, "internal_source_refs",
            tuple(self.internal_source_refs) if self.internal_source_refs is not None else (),
        )
        if self.validation_status not in VALIDATION_STATUSES:
            raise ValueError(f"unsupported validation_status: {self.validation_status!r}")
        if self.approval_status not in APPROVAL_STATUSES:
            raise ValueError(f"unsupported approval_status: {self.approval_status!r}")
        if self.application_status not in APPLICATION_STATUSES:
            raise ValueError(f"unsupported application_status: {self.application_status!r}")
        if self.rejection_code is not None and self.rejection_code not in CHANGE_CARD_REJECTION_CODES:
            raise ValueError(f"unsupported rejection_code: {self.rejection_code!r}")
        collapsed = collapse_change_card_status(
            validation_status=self.validation_status,
            approval_status=self.approval_status,
            application_status=self.application_status,
        )
        object.__setattr__(self, "status", collapsed)

    @property
    def action(self) -> str:
        """Read-only compatibility alias for the pre-C6 field name."""

        return self.type


@dataclass(frozen=True, slots=True)
class PlanChangeSet:
    id: str
    base_plan_revision: int
    cards: tuple[PlanChangeCard, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("change set id must be non-empty")
        if (
            isinstance(self.base_plan_revision, bool)
            or not isinstance(self.base_plan_revision, int)
            or self.base_plan_revision < 0
        ):
            raise ValueError("base_plan_revision must be a non-negative integer")
        object.__setattr__(self, "cards", tuple(self.cards or ()))
        if len(self.cards) > 5:
            raise ValueError("change set cards must contain at most 5 cards")


@dataclass(frozen=True, slots=True)
class PlanAssistantRequest:
    calendar_today: date
    base_plan_revision: int
    plan_snapshot: PreparationPlanDraft
    user_message: str
    request_id: str
    history: tuple[AssistantHistoryTurn, ...] = ()
    student_context: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.calendar_today, date):
            raise ValueError("calendar_today must be a date")
        if (
            isinstance(self.base_plan_revision, bool)
            or not isinstance(self.base_plan_revision, int)
            or self.base_plan_revision < 0
        ):
            raise ValueError("base_plan_revision must be a non-negative integer")
        if not isinstance(self.plan_snapshot, PreparationPlanDraft):
            raise ValueError("plan_snapshot must be a PreparationPlanDraft")
        if not isinstance(self.user_message, str) or not self.user_message.strip():
            raise ValueError("user_message must be non-empty")
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be non-empty")
        object.__setattr__(self, "user_message", self.user_message.strip())
        object.__setattr__(self, "request_id", self.request_id.strip())
        object.__setattr__(self, "history", tuple(self.history or ()))


@dataclass(frozen=True, slots=True)
class PlanAssistantResult:
    reply: str
    change_set: PlanChangeSet
    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.reply, str):
            raise ValueError("reply must be a string")
        if not isinstance(self.change_set, PlanChangeSet):
            raise ValueError("change_set must be a PlanChangeSet")
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be non-empty")


@dataclass(frozen=True, slots=True)
class PlanAssistantServiceResult:
    result: PlanAssistantResult | None
    issues: tuple[CompetitionError, ...] = ()
    generation_profile_version: str = "competition.assistant.v1"
    diagnostics: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        if not self.generation_profile_version:
            raise ValueError("generation_profile_version must be non-empty")
        has_fatal = any(issue.severity == ErrorSeverity.ERROR for issue in self.issues)
        if has_fatal and self.result is not None:
            raise ValueError("fatal assistant issues require result=None")
        if not has_fatal and self.result is None:
            raise ValueError("successful assistant result requires a payload")
        diagnostics = dict(self.diagnostics or {})
        object.__setattr__(self, "diagnostics", MappingProxyType(diagnostics))


__all__ = [
    "APPLICATION_STATUSES",
    "APPROVAL_STATUSES",
    "AssistantHistoryTurn",
    "CHANGE_CARD_REJECTION_CODES",
    "CHANGE_CARD_STATUSES",
    "CHANGE_CARD_TYPES",
    "CardResult",
    "PlanAssistantRequest",
    "PlanAssistantResult",
    "PlanAssistantServiceResult",
    "PlanChangeCard",
    "PlanChangeSet",
    "PreparationNewTaskDraft",
    "PreparationPhaseScheduleDraft",
    "VALIDATION_STATUSES",
    "collapse_change_card_status",
]
