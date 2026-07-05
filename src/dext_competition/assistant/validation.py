"""C6 change-card validation.

Validation produces card status only; it never mutates a plan snapshot.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from dext_competition.contracts.assistant import PlanChangeCard
from dext_competition.contracts.plan import PlanPhase, PlanTask, PreparationPlanDraft
from dext_competition.planning.validation import validate_plan


def reject_card(card: PlanChangeCard, code: str, reason: str) -> PlanChangeCard:
    return replace(
        card,
        validation_status="rejected",
        rejection_code=code,
        rejection_reason=reason,
    )


def _pass_card(card: PlanChangeCard) -> PlanChangeCard:
    return replace(card, validation_status="passed", rejection_code=None, rejection_reason=None)


def _tasks(plan: PreparationPlanDraft) -> dict[str, PlanTask]:
    return {task.task_id: task for task in (*plan.tasks, *plan.optional_tasks)}


def _phases_by_id(plan: PreparationPlanDraft) -> dict[str, PlanPhase]:
    return {phase.phase_id: phase for phase in plan.phases}


def _phases_by_key(plan: PreparationPlanDraft) -> dict[str, PlanPhase]:
    return {phase.key: phase for phase in plan.phases}


def _date_in_phase(value: date, phase: PlanPhase, calendar_today: date) -> bool:
    return value >= calendar_today and phase.start_date <= value <= phase.end_date


def _validate_source(card: PlanChangeCard) -> PlanChangeCard | None:
    if card.validation_status == "rejected":
        return card
    if card.type != "append_advice" and not card.internal_source_refs:
        return reject_card(
            card,
            "missing_required_fields",
            "non-advice change cards require at least one grounded source reference",
        )
    return None


def _validate_move(
    plan: PreparationPlanDraft,
    card: PlanChangeCard,
    *,
    calendar_today: date,
) -> PlanChangeCard:
    tasks = _tasks(plan)
    task = tasks.get(card.target_task_id or "")
    if task is None:
        return reject_card(card, "target_task_not_found", "target task does not exist")
    if card.new_date is None:
        return reject_card(card, "date_out_of_range", "move_task requires new_date")
    phases_by_key = _phases_by_key(plan)
    phases_by_id = _phases_by_id(plan)
    phase = phases_by_key.get(card.target_phase_key or "") if card.target_phase_key else phases_by_id.get(task.phase_id)
    if phase is None:
        return reject_card(card, "target_phase_not_found", "target phase does not exist")
    if not _date_in_phase(card.new_date, phase, calendar_today):
        return reject_card(card, "date_out_of_range", "new_date is outside the target phase or before today")
    return _pass_card(card)


def _validate_add(
    plan: PreparationPlanDraft,
    card: PlanChangeCard,
    *,
    calendar_today: date,
) -> PlanChangeCard:
    phase = _phases_by_key(plan).get(card.target_phase_key or "")
    if phase is None:
        return reject_card(card, "target_phase_not_found", "target phase does not exist")
    if card.new_task is None:
        return reject_card(card, "invalid_add_task_fields", "add_task requires a valid new_task")
    if not _date_in_phase(card.new_task.due_date, phase, calendar_today):
        return reject_card(card, "date_out_of_range", "new task due_date is outside the target phase or before today")
    return _pass_card(card)


def _validate_delete(plan: PreparationPlanDraft, card: PlanChangeCard) -> PlanChangeCard:
    task = _tasks(plan).get(card.target_task_id or "")
    if task is None:
        return reject_card(card, "target_task_not_found", "target task does not exist")
    if task.is_mandatory:
        return reject_card(card, "required_task_delete_forbidden", "required tasks cannot be deleted")
    return _pass_card(card)


def _validate_reschedule(
    plan: PreparationPlanDraft,
    card: PlanChangeCard,
    *,
    calendar_today: date,
) -> PlanChangeCard:
    if not card.phase_schedule:
        return reject_card(card, "phase_schedule_invalid", "reschedule_phase requires phase_schedule")
    phases = _phases_by_key(plan)
    for item in card.phase_schedule:
        if item.phase_key not in phases:
            return reject_card(card, "target_phase_not_found", "target phase does not exist")
        if item.start_date < calendar_today or item.start_date > item.end_date:
            return reject_card(card, "phase_schedule_invalid", "phase schedule dates are invalid")
        if plan.time_model == "submission_deadline" and item.phase_key == "defense_prep":
            if plan.target_date and item.start_date <= plan.target_date:
                return reject_card(card, "phase_schedule_invalid", "defense_prep must remain after submission")
        if plan.time_model == "competition_window" and item.phase_key == "event_sprint":
            if plan.target_date and item.start_date != plan.target_date:
                return reject_card(card, "phase_schedule_invalid", "event_sprint must keep the competition-window start")
    if validate_plan(plan):
        return reject_card(card, "phase_schedule_invalid", "base plan invariants are invalid")
    return _pass_card(card)


def _validate_advice(card: PlanChangeCard) -> PlanChangeCard:
    if not (card.advice_text and card.advice_text.strip()):
        return reject_card(card, "invalid_advice_fields", "append_advice requires advice_text")
    return _pass_card(card)


def validate_change_card(
    plan: PreparationPlanDraft,
    card: PlanChangeCard,
    *,
    calendar_today: date,
) -> PlanChangeCard:
    source_issue = _validate_source(card)
    if source_issue is not None:
        return source_issue
    if card.type == "move_task":
        return _validate_move(plan, card, calendar_today=calendar_today)
    if card.type == "add_task":
        return _validate_add(plan, card, calendar_today=calendar_today)
    if card.type == "delete_task":
        return _validate_delete(plan, card)
    if card.type == "reschedule_phase":
        return _validate_reschedule(plan, card, calendar_today=calendar_today)
    if card.type == "append_advice":
        return _validate_advice(card)
    return reject_card(card, "missing_required_fields", "unsupported change card type")


def validate_change_cards(
    plan: PreparationPlanDraft,
    cards: tuple[PlanChangeCard, ...],
    *,
    calendar_today: date,
) -> tuple[PlanChangeCard, ...]:
    return tuple(validate_change_card(plan, card, calendar_today=calendar_today) for card in cards)


__all__ = ["reject_card", "validate_change_card", "validate_change_cards"]
