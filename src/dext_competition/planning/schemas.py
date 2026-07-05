"""Internal C5 input and template schemas."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class PlanConstraints:
    exam_dates: tuple[date, ...] = ()
    unavailable_dates: tuple[date, ...] = ()
    team_status: str | None = None
    school_deadline: date | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "exam_dates", tuple(self.exam_dates or ()))
        object.__setattr__(self, "unavailable_dates", tuple(self.unavailable_dates or ()))


@dataclass(frozen=True, slots=True)
class PlanGenerationRequest:
    competition_id: str
    calendar_today: date
    target_date: date
    weekly_hours: int
    experience_level: str
    time_model: str
    event_end_date: date | None = None
    defense_date: date | None = None
    student_context: object | None = None
    constraints: PlanConstraints = PlanConstraints()


@dataclass(frozen=True, slots=True)
class TemplateTask:
    key: str
    label: str
    estimated_hours: float
    required: bool
    min_experience: str = "beginner"


@dataclass(frozen=True, slots=True)
class PhaseTemplate:
    key: str
    label: str
    weight: float
    tasks: tuple[TemplateTask, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tasks", tuple(self.tasks or ()))
        if self.weight <= 0:
            raise ValueError("phase template weight must be positive")


__all__ = [
    "PhaseTemplate",
    "PlanConstraints",
    "PlanGenerationRequest",
    "TemplateTask",
]
