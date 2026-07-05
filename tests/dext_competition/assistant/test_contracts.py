from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from dext_competition import (
    PlanAssistantRequest,
    PlanChangeCard,
    PlanChangeSet,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
    PreparationPlanDraft,
    collapse_change_card_status,
)
from dext_competition.contracts.assistant import AssistantHistoryTurn, CardResult


def _plan() -> PreparationPlanDraft:
    return PreparationPlanDraft("plan-1", "cmp-1", "submission_deadline", date(2026, 8, 1), revision=3)


@pytest.mark.parametrize(
    ("validation", "approval", "application", "expected"),
    [
        ("pending", "pending", "not_applied", "pending"),
        ("rejected", "pending", "not_applied", "rejected"),
        ("passed", "declined", "not_applied", "declined"),
        ("passed", "accepted", "applied", "applied"),
        ("passed", "accepted", "failed", "stale"),
    ],
)
def test_change_card_status_collapse(validation, approval, application, expected) -> None:
    assert collapse_change_card_status(
        validation_status=validation,
        approval_status=approval,
        application_status=application,
    ) == expected


def test_change_card_contract_uses_openapi_type_and_action_alias() -> None:
    card = PlanChangeCard(
        id="card-1",
        type="move_task",
        summary="移动任务",
        rationale="基于备赛流程调整。",
        target_task_id="task-1",
        new_date=date(2026, 7, 10),
        validation_status="passed",
    )
    assert card.type == "move_task"
    assert card.action == "move_task"
    assert card.status == "pending"
    with pytest.raises(dataclasses.FrozenInstanceError):
        card.summary = "mutated"  # type: ignore[misc]


def test_change_set_and_request_coerce_collections() -> None:
    history = AssistantHistoryTurn("assistant", "ok", [CardResult("card-1", "pending")])
    request = PlanAssistantRequest(
        date(2026, 7, 1),
        3,
        _plan(),
        "把准备节奏放缓",
        "req-1",
        [history],
    )
    assert request.history == (history,)
    assert PlanChangeSet("set-1", 3, []).cards == ()


def test_nested_drafts_validate_shape() -> None:
    task = PreparationNewTaskDraft("补充复盘", 2, date(2026, 7, 20), "note")
    schedule = PreparationPhaseScheduleDraft("practice", date(2026, 7, 2), date(2026, 7, 15))
    assert task.estimated_hours == 2
    assert schedule.phase_key == "practice"
    with pytest.raises(ValueError, match="estimated_hours"):
        PreparationNewTaskDraft("bad", 0, date(2026, 7, 20))
