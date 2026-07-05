from __future__ import annotations

import dataclasses
import json
from datetime import date

import pytest

from dext_competition import (
    CompetitionError,
    CompetitionErrorCode,
    ErrorSeverity,
    PlanGenerationResult,
    PlanPhase,
    PlanTask,
    PreparationPlanDraft,
)


def _phase() -> PlanPhase:
    return PlanPhase("phase-foundation", "foundation", "基础准备", date(2026, 7, 1), date(2026, 7, 14), ["task-1"])


def _task() -> PlanTask:
    return PlanTask(
        "task-1", "phase-foundation", "完成基础训练", True,
        estimated_hours=12, start_date=date(2026, 7, 1), due_date=date(2026, 7, 14),
    )


def test_plan_contracts_are_frozen_and_coerce_collections() -> None:
    phase = _phase()
    task = _task()
    draft = PreparationPlanDraft(
        "plan-1", "cmp-1", "submission_deadline", date(2026, 8, 1),
        defense_date=date(2026, 8, 15), phases=[phase], tasks=[task], revision=0,
    )
    assert phase.task_ids == ("task-1",)
    assert task.kind == "required"
    assert draft.phases == (phase,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        draft.revision = 1  # type: ignore[misc]


@pytest.mark.parametrize("revision", [-1, True, "1"])
def test_revision_must_be_non_negative_integer(revision) -> None:
    with pytest.raises(ValueError, match="revision"):
        PreparationPlanDraft("p", "c", "submission_deadline", revision=revision)


def test_date_ranges_and_task_phase_references_are_validated() -> None:
    with pytest.raises(ValueError, match="event_end_date"):
        PreparationPlanDraft(
            "p", "c", "competition_window", date(2026, 8, 2),
            event_end_date=date(2026, 8, 1),
        )
    with pytest.raises(ValueError, match="phase_id"):
        PreparationPlanDraft(
            "p", "c", "submission_deadline", phases=[_phase()],
            tasks=[PlanTask("orphan", "other", "孤立任务")],
        )


def test_fatal_result_carries_no_draft_and_success_carries_draft() -> None:
    issue = CompetitionError(
        CompetitionErrorCode.PLAN_INVALID,
        ErrorSeverity.ERROR,
        "mandatory task cannot be scheduled",
    )
    result = PlanGenerationResult(None, [issue])
    assert result.draft is None
    assert result.issues == (issue,)
    with pytest.raises(ValueError, match="fatal"):
        PlanGenerationResult(PreparationPlanDraft("p", "c", "submission_deadline"), [issue])


def test_contract_dates_have_stable_iso_serialization_shape() -> None:
    phase = _phase()
    task = _task()
    payload = dataclasses.asdict(
        PreparationPlanDraft(
            "plan-1", "cmp-1", "submission_deadline", date(2026, 8, 1),
            defense_date=date(2026, 8, 15), phases=[phase], tasks=[task], revision=2,
        )
    )
    encoded = json.dumps(payload, default=lambda value: value.isoformat(), sort_keys=True)
    assert '"revision": 2' in encoded
    assert '"target_date": "2026-08-01"' in encoded
