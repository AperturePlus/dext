"""Preparation-plan contracts shared by C5 and the C6 assistant."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from dext_competition.errors import CompetitionError, ErrorSeverity
from dext_grounded import SourceRef


_TIME_MODELS = frozenset({"submission_deadline", "competition_window"})
_TASK_KINDS = frozenset({"required", "optional", "user_added"})


@dataclass(frozen=True, slots=True)
class PlanTask:
    task_id: str
    phase_id: str
    label: str
    is_mandatory: bool = False
    estimated_weeks: float | None = None
    depends_on: tuple[str, ...] = ()
    kind: str = "optional"
    estimated_hours: float | None = None
    start_date: date | None = None
    due_date: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "depends_on", tuple(self.depends_on or ()))
        if not self.task_id or not self.phase_id or not self.label.strip():
            raise ValueError("plan task id, phase id and label must be non-empty")
        if self.kind not in _TASK_KINDS:
            raise ValueError(f"unsupported plan task kind: {self.kind}")
        if self.is_mandatory and self.kind != "required":
            object.__setattr__(self, "kind", "required")
        if self.estimated_weeks is not None and self.estimated_weeks <= 0:
            raise ValueError("estimated_weeks must be positive")
        if self.estimated_hours is not None and self.estimated_hours <= 0:
            raise ValueError("estimated_hours must be positive")
        if self.start_date and self.due_date and self.start_date > self.due_date:
            raise ValueError("task start_date must not be after due_date")


@dataclass(frozen=True, slots=True)
class PlanPhase:
    phase_id: str
    key: str
    label: str
    start_date: date
    end_date: date
    task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_ids", tuple(self.task_ids or ()))
        if not self.phase_id or not self.key or not self.label.strip():
            raise ValueError("plan phase id, key and label must be non-empty")
        if self.start_date > self.end_date:
            raise ValueError("phase start_date must not be after end_date")


@dataclass(frozen=True, slots=True)
class PreparationPlanDraft:
    plan_id: str
    competition_id: str
    time_model: str
    target_date: date | None = None
    event_end_date: date | None = None
    defense_date: date | None = None
    phases: tuple[PlanPhase, ...] = ()
    tasks: tuple[PlanTask, ...] = ()
    optional_tasks: tuple[PlanTask, ...] = ()
    milestones: tuple[str, ...] = ()
    calendar_items: tuple[str, ...] = ()
    risk_register: tuple[str, ...] = ()
    internal_source_refs: tuple[SourceRef, ...] = ()
    warnings: tuple[CompetitionError, ...] = ()
    revision: int = 0

    def __post_init__(self) -> None:
        for field_name in (
            "phases", "tasks", "optional_tasks", "milestones", "calendar_items",
            "risk_register", "internal_source_refs", "warnings",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name) or ()))
        if not self.plan_id or not self.competition_id:
            raise ValueError("plan_id and competition_id must be non-empty")
        if self.time_model not in _TIME_MODELS:
            raise ValueError(f"unsupported time_model: {self.time_model}")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if self.event_end_date and self.target_date and self.event_end_date < self.target_date:
            raise ValueError("event_end_date must not be before target_date")
        if self.defense_date and self.target_date and self.defense_date <= self.target_date:
            raise ValueError("defense_date must be after target_date")
        phase_ids = {phase.phase_id for phase in self.phases}
        if len(phase_ids) != len(self.phases):
            raise ValueError("phase ids must be unique")
        task_ids = {task.task_id for task in (*self.tasks, *self.optional_tasks)}
        if len(task_ids) != len(self.tasks) + len(self.optional_tasks):
            raise ValueError("task ids must be unique")
        if phase_ids and any(task.phase_id not in phase_ids for task in (*self.tasks, *self.optional_tasks)):
            raise ValueError("every task phase_id must reference a plan phase")


@dataclass(frozen=True, slots=True)
class PlanGenerationResult:
    draft: PreparationPlanDraft | None
    issues: tuple[CompetitionError, ...] = ()
    generation_profile_version: str = "competition.plan.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        if not self.generation_profile_version:
            raise ValueError("generation_profile_version must be non-empty")
        has_fatal = any(issue.severity == ErrorSeverity.ERROR for issue in self.issues)
        if has_fatal and self.draft is not None:
            raise ValueError("fatal plan generation issues require draft=None")
        if not has_fatal and self.draft is None:
            raise ValueError("successful plan generation requires a draft")


@dataclass(frozen=True, slots=True)
class LevelDiagnosis:
    level: str
    rationale: str
    suggestion: str

    def __post_init__(self) -> None:
        if self.level not in {"beginner", "intermediate", "experienced"}:
            raise ValueError(f"unsupported diagnosis level: {self.level}")


__all__ = [
    "LevelDiagnosis",
    "PlanGenerationResult",
    "PlanPhase",
    "PlanTask",
    "PreparationPlanDraft",
]
