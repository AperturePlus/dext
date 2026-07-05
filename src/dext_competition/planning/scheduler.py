"""Deterministic phase/task scheduler for both C5 time models."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from dext_competition.contracts.plan import PlanPhase, PlanTask
from dext_competition.planning.schemas import PhaseTemplate, PlanGenerationRequest, TemplateTask


class PlanSchedulingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScheduledPlan:
    phases: tuple[PlanPhase, ...]
    tasks: tuple[PlanTask, ...]
    optional_tasks: tuple[PlanTask, ...]
    dropped_optional_labels: tuple[str, ...] = ()


def _allocate_ranges(start: date, end: date, templates: tuple[PhaseTemplate, ...]) -> list[tuple[date, date]]:
    if not templates:
        return []
    total_days = (end - start).days + 1
    if total_days < len(templates):
        raise PlanSchedulingError("not enough calendar days for mandatory phases")
    total_weight = sum(template.weight for template in templates)
    remaining_days = total_days
    cursor = start
    ranges: list[tuple[date, date]] = []
    for index, template in enumerate(templates):
        phases_left = len(templates) - index - 1
        if index == len(templates) - 1:
            days = remaining_days
        else:
            days = max(1, round(total_days * template.weight / total_weight))
            days = min(days, remaining_days - phases_left)
        phase_end = cursor + timedelta(days=days - 1)
        ranges.append((cursor, phase_end))
        cursor = phase_end + timedelta(days=1)
        remaining_days -= days
    return ranges


def _available_due_date(start: date, end: date, blocked: set[date]) -> date | None:
    candidate = end
    while candidate >= start:
        if candidate not in blocked:
            return candidate
        candidate -= timedelta(days=1)
    return None


def _eligible(task: TemplateTask, experience_level: str) -> bool:
    order = {"beginner": 0, "intermediate": 1, "experienced": 2}
    return order.get(experience_level, 0) >= order.get(task.min_experience, 0)


def schedule_plan(
    request: PlanGenerationRequest,
    templates: tuple[PhaseTemplate, ...],
) -> ScheduledPlan:
    blocked = set(request.constraints.exam_dates) | set(request.constraints.unavailable_dates)
    if request.constraints.school_deadline and request.target_date > request.constraints.school_deadline:
        raise PlanSchedulingError("target date is after the school deadline")

    post_keys = {"defense_prep"} if request.time_model == "submission_deadline" else {"event_sprint", "recovery"}
    pre = tuple(template for template in templates if template.key not in post_keys)
    pre_ranges = _allocate_ranges(request.calendar_today, request.target_date, pre)
    ranges: dict[str, tuple[date, date]] = {
        template.key: value for template, value in zip(pre, pre_ranges, strict=True)
    }
    if request.time_model == "submission_deadline":
        submission = next((template for template in templates if template.key == "submission"), None)
        if submission is not None:
            ranges["submission"] = pre_ranges[-1]
        defense_end = request.defense_date or request.target_date + timedelta(days=14)
        ranges["defense_prep"] = (request.target_date + timedelta(days=1), defense_end)
    else:
        event_end = request.event_end_date or request.target_date
        if event_end < request.target_date:
            raise PlanSchedulingError("event window ends before it starts")
        ranges["event_sprint"] = (request.target_date, event_end)
        ranges["recovery"] = (event_end + timedelta(days=1), event_end + timedelta(days=7))

    phase_results: list[PlanPhase] = []
    required_tasks: list[PlanTask] = []
    optional_tasks: list[PlanTask] = []
    dropped: list[str] = []
    total_weeks = max(1.0, (request.target_date - request.calendar_today).days / 7)
    optional_budget = max(0.0, request.weekly_hours * total_weeks)
    optional_used = 0.0

    for template in templates:
        start, end = ranges[template.key]
        phase_id = f"phase:{template.key}"
        phase_task_ids: list[str] = []
        for item in template.tasks:
            if not _eligible(item, request.experience_level):
                continue
            due = _available_due_date(start, end, blocked)
            if due is None:
                if item.required:
                    raise PlanSchedulingError(f"mandatory task cannot be scheduled: {item.key}")
                dropped.append(item.label)
                continue
            if not item.required and optional_used + item.estimated_hours > optional_budget:
                dropped.append(item.label)
                continue
            task_id = f"task:{template.key}:{item.key}"
            task = PlanTask(
                task_id=task_id,
                phase_id=phase_id,
                label=item.label,
                is_mandatory=item.required,
                kind="required" if item.required else "optional",
                estimated_hours=item.estimated_hours,
                start_date=start,
                due_date=due,
            )
            phase_task_ids.append(task_id)
            if item.required:
                required_tasks.append(task)
            else:
                optional_used += item.estimated_hours
                optional_tasks.append(task)
        phase_results.append(PlanPhase(phase_id, template.key, template.label, start, end, phase_task_ids))

    return ScheduledPlan(tuple(phase_results), tuple(required_tasks), tuple(optional_tasks), tuple(dropped))


__all__ = ["PlanSchedulingError", "ScheduledPlan", "schedule_plan"]
