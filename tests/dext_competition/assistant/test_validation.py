from __future__ import annotations

import hashlib
from datetime import date

import pytest

from dext_competition import (
    PlanChangeCard,
    PlanPhase,
    PlanTask,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
    PreparationPlanDraft,
    SourceRef,
)
from dext_competition.assistant import validate_change_card


def _ref() -> SourceRef:
    return SourceRef("备赛流程.md", "备赛流程 > 通用流程", hashlib.sha256(b"flow").hexdigest(), "通用流程")


def _submission_plan() -> PreparationPlanDraft:
    foundation = PlanPhase("phase:foundation", "foundation", "基础", date(2026, 7, 1), date(2026, 7, 14), ("task:foundation:req", "task:foundation:opt"))
    practice = PlanPhase("phase:practice", "practice", "训练", date(2026, 7, 15), date(2026, 8, 1), ())
    defense = PlanPhase("phase:defense_prep", "defense_prep", "答辩", date(2026, 8, 2), date(2026, 8, 15), ())
    return PreparationPlanDraft(
        "plan-1",
        "cmp-1",
        "submission_deadline",
        date(2026, 8, 1),
        defense_date=date(2026, 8, 15),
        phases=(foundation, practice, defense),
        tasks=(PlanTask("task:foundation:req", foundation.phase_id, "必做任务", True, kind="required", due_date=date(2026, 7, 10)),),
        optional_tasks=(PlanTask("task:foundation:opt", foundation.phase_id, "可选任务", False, kind="optional", due_date=date(2026, 7, 11)),),
        internal_source_refs=(_ref(),),
    )


def _card(**overrides) -> PlanChangeCard:
    values = dict(
        id="card-1",
        type="move_task",
        summary="调整任务",
        rationale="依据备赛流程。",
        internal_source_refs=(_ref(),),
    )
    values.update(overrides)
    return PlanChangeCard(**values)


@pytest.mark.parametrize(
    "card",
    [
        _card(type="move_task", target_task_id="task:foundation:opt", new_date=date(2026, 7, 12)),
        _card(type="add_task", target_phase_key="practice", new_task=PreparationNewTaskDraft("补充模拟", 3, date(2026, 7, 20))),
        _card(type="delete_task", target_task_id="task:foundation:opt"),
        _card(type="reschedule_phase", phase_schedule=(PreparationPhaseScheduleDraft("practice", date(2026, 7, 16), date(2026, 8, 1)),)),
        _card(type="append_advice", advice_text="保留每周复盘。", internal_source_refs=()),
    ],
)
def test_five_card_types_pass(card: PlanChangeCard) -> None:
    validated = validate_change_card(_submission_plan(), card, calendar_today=date(2026, 7, 1))
    assert validated.status == "pending"
    assert validated.validation_status == "passed"


@pytest.mark.parametrize(
    ("card", "code"),
    [
        (_card(type="move_task", target_task_id="missing", new_date=date(2026, 7, 12)), "target_task_not_found"),
        (_card(type="add_task", target_phase_key="missing", new_task=PreparationNewTaskDraft("补充", 1, date(2026, 7, 20))), "target_phase_not_found"),
        (_card(type="move_task", target_task_id="task:foundation:opt", new_date=date(2026, 8, 20)), "date_out_of_range"),
        (_card(type="add_task", target_phase_key="practice"), "invalid_add_task_fields"),
        (_card(type="delete_task", target_task_id="task:foundation:req"), "required_task_delete_forbidden"),
        (_card(type="reschedule_phase", phase_schedule=(PreparationPhaseScheduleDraft("defense_prep", date(2026, 8, 1), date(2026, 8, 15)),)), "phase_schedule_invalid"),
        (_card(type="append_advice", advice_text=""), "invalid_advice_fields"),
        (_card(type="move_task", target_task_id="task:foundation:opt", new_date=date(2026, 7, 12), internal_source_refs=()), "missing_required_fields"),
    ],
)
def test_rejection_codes(card: PlanChangeCard, code: str) -> None:
    validated = validate_change_card(_submission_plan(), card, calendar_today=date(2026, 7, 1))
    assert validated.status == "rejected"
    assert validated.rejection_code == code
