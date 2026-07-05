"""Plan and LLM-personalization validation."""
from __future__ import annotations

from collections.abc import Mapping

from dext_competition.contracts.plan import PreparationPlanDraft
from dext_competition.planning.profile import PlanGenerationProfile


def validate_plan(draft: PreparationPlanDraft) -> tuple[str, ...]:
    issues: list[str] = []
    phase_by_key = {phase.key: phase for phase in draft.phases}
    if draft.time_model == "submission_deadline":
        defense = phase_by_key.get("defense_prep")
        if defense is None:
            issues.append("submission plan requires defense_prep")
        elif draft.target_date and defense.start_date <= draft.target_date:
            issues.append("defense_prep must start after submission")
    if draft.time_model == "competition_window":
        event = phase_by_key.get("event_sprint")
        if event is None:
            issues.append("competition window plan requires event_sprint")
        elif draft.target_date and event.start_date != draft.target_date:
            issues.append("event_sprint must start at the event window")
    if not draft.tasks or any(not task.is_mandatory for task in draft.tasks):
        issues.append("required task collection is invalid")
    return tuple(issues)


def validate_personalization_output(
    output: object,
    *,
    known_phase_keys: set[str],
    profile: PlanGenerationProfile,
) -> dict[str, tuple[dict[str, object], ...]] | None:
    if not isinstance(output, Mapping) or set(output) - {"phases", "global_advice"}:
        return None
    phases = output.get("phases")
    if not isinstance(phases, list):
        return None
    result: dict[str, tuple[dict[str, object], ...]] = {}
    for phase in phases:
        if not isinstance(phase, Mapping) or set(phase) - {"key", "optional_tasks", "personalized_advice"}:
            return None
        key = phase.get("key")
        tasks = phase.get("optional_tasks", [])
        if key not in known_phase_keys or not isinstance(tasks, list):
            return None
        if len(tasks) > profile.max_optional_tasks_per_phase:
            return None
        checked: list[dict[str, object]] = []
        for task in tasks:
            if not isinstance(task, Mapping) or set(task) - {"title", "estimated_hours"}:
                return None
            title, hours = task.get("title"), task.get("estimated_hours")
            if not isinstance(title, str) or not title.strip():
                return None
            if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
                return None
            checked.append({"title": title.strip(), "estimated_hours": float(hours)})
        result[str(key)] = tuple(checked)
    return result


__all__ = ["validate_personalization_output", "validate_plan"]
