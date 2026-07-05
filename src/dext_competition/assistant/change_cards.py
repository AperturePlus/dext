"""Parsing helpers for assistant generation output."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from dext_competition.contracts.assistant import (
    CHANGE_CARD_TYPES,
    PlanChangeCard,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
)
from dext_grounded import SourceRef


def _parse_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _new_task(value: object) -> PreparationNewTaskDraft | None:
    if not isinstance(value, Mapping):
        return None
    due = _parse_date(value.get("due_date"))
    if due is None:
        return None
    hours = value.get("estimated_hours")
    if isinstance(hours, bool) or not isinstance(hours, int):
        return None
    try:
        return PreparationNewTaskDraft(
            title=_string(value.get("title")),
            estimated_hours=hours,
            due_date=due,
            note=_string(value.get("note")) or None,
        )
    except ValueError:
        return None


def _phase_schedule(value: object) -> tuple[PreparationPhaseScheduleDraft, ...]:
    if not isinstance(value, list):
        return ()
    out: list[PreparationPhaseScheduleDraft] = []
    for item in value:
        if not isinstance(item, Mapping):
            return ()
        start = _parse_date(item.get("start_date"))
        end = _parse_date(item.get("end_date"))
        if start is None or end is None:
            return ()
        try:
            out.append(PreparationPhaseScheduleDraft(
                phase_key=_string(item.get("phase_key")),
                start_date=start,
                end_date=end,
            ))
        except ValueError:
            return ()
    return tuple(out)


def _invalid_card(index: int, reason: str) -> PlanChangeCard:
    return PlanChangeCard(
        id=f"card-{index + 1}",
        type="append_advice",
        summary="无法使用的改动建议",
        rationale="",
        advice_text="",
        validation_status="rejected",
        rejection_code="missing_required_fields",
        rejection_reason=reason,
    )


def card_from_payload(
    payload: object,
    *,
    index: int,
    source_refs: tuple[SourceRef, ...],
) -> PlanChangeCard:
    """Map one generated JSON card into the immutable C6 contract."""

    if not isinstance(payload, Mapping):
        return _invalid_card(index, "change card must be an object")
    card_type = _string(payload.get("type"))
    if card_type not in CHANGE_CARD_TYPES:
        return _invalid_card(index, "change card type is missing or unsupported")
    card_id = _string(payload.get("id")) or f"card-{index + 1}"
    summary = _string(payload.get("summary"))
    rationale = _string(payload.get("rationale"))
    if not summary:
        return _invalid_card(index, "change card summary is required")
    try:
        return PlanChangeCard(
            id=card_id,
            type=card_type,
            target_task_id=_string(payload.get("target_task_id")) or None,
            target_phase_key=_string(payload.get("target_phase_key")) or None,
            new_date=_parse_date(payload.get("new_date")),
            new_task=_new_task(payload.get("new_task")),
            phase_schedule=_phase_schedule(payload.get("phase_schedule")),
            advice_text=_string(payload.get("advice_text")) or None,
            summary=summary,
            rationale=rationale,
            internal_source_refs=source_refs,
        )
    except ValueError as exc:
        return _invalid_card(index, str(exc))


def cards_from_generation_output(
    output: dict | str,
    *,
    source_refs: tuple[SourceRef, ...],
    max_cards: int = 5,
) -> tuple[PlanChangeCard, ...]:
    """Extract change cards from the assistant JSON output."""

    if not isinstance(output, Mapping):
        return ()
    cards: object = output.get("cards")
    if cards is None:
        change_set = output.get("change_set")
        if isinstance(change_set, Mapping):
            cards = change_set.get("cards")
    if not isinstance(cards, list):
        return ()
    return tuple(
        card_from_payload(payload, index=index, source_refs=source_refs)
        for index, payload in enumerate(cards[:max_cards])
    )


def reply_from_generation_output(output: dict | str) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, Mapping):
        value = output.get("reply") or output.get("message")
        if isinstance(value, str):
            return value
    return ""


def compact_plan_snapshot(plan) -> dict[str, Any]:
    """Serialize the C5 plan draft into the minimal prompt-safe shape."""

    tasks = {
        task.task_id: {
            "id": task.task_id,
            "phase_id": task.phase_id,
            "title": task.label,
            "kind": task.kind,
            "mandatory": task.is_mandatory,
            "estimated_hours": task.estimated_hours,
            "due_date": task.due_date.isoformat() if task.due_date else None,
        }
        for task in (*plan.tasks, *plan.optional_tasks)
    }
    return {
        "id": plan.plan_id,
        "competition_id": plan.competition_id,
        "time_model": plan.time_model,
        "target_date": plan.target_date.isoformat() if plan.target_date else None,
        "event_end_date": plan.event_end_date.isoformat() if plan.event_end_date else None,
        "defense_date": plan.defense_date.isoformat() if plan.defense_date else None,
        "revision": plan.revision,
        "phases": [
            {
                "phase_id": phase.phase_id,
                "key": phase.key,
                "label": phase.label,
                "start_date": phase.start_date.isoformat(),
                "end_date": phase.end_date.isoformat(),
                "task_ids": list(phase.task_ids),
            }
            for phase in plan.phases
        ],
        "tasks": list(tasks.values()),
        "milestones": list(plan.milestones),
        "risk_register": list(plan.risk_register),
    }


__all__ = [
    "card_from_payload",
    "cards_from_generation_output",
    "compact_plan_snapshot",
    "reply_from_generation_output",
]
