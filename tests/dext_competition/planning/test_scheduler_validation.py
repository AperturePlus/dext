from __future__ import annotations

from datetime import date, timedelta

import pytest

from dext_competition import CompetitionCard, PreparationPlanDraft
from dext_competition.planning import (
    PlanConstraints,
    PlanGenerationRequest,
    PlanSchedulingError,
    build_phase_templates,
    schedule_plan,
    validate_plan,
)


def _request(time_model: str, **overrides) -> PlanGenerationRequest:
    values = dict(
        competition_id="cmp-1",
        calendar_today=date(2026, 7, 1),
        target_date=date(2026, 8, 20),
        weekly_hours=8,
        experience_level="beginner",
        time_model=time_model,
    )
    values.update(overrides)
    return PlanGenerationRequest(**values)


def test_submission_schedule_places_defense_after_target_and_is_stable() -> None:
    request = _request("submission_deadline", defense_date=date(2026, 9, 1))
    templates = build_phase_templates(CompetitionCard("cmp-1", "测试", "计算机"), time_model=request.time_model, experience_level=request.experience_level)
    first = schedule_plan(request, templates)
    second = schedule_plan(request, templates)
    assert first == second
    defense = next(phase for phase in first.phases if phase.key == "defense_prep")
    assert defense.start_date == request.target_date + timedelta(days=1)
    assert defense.end_date == request.defense_date


def test_window_schedule_anchors_sprint_and_recovery() -> None:
    request = _request("competition_window", event_end_date=date(2026, 8, 23))
    templates = build_phase_templates(CompetitionCard("cmp-1", "测试", "机器人"), time_model=request.time_model, experience_level="beginner")
    scheduled = schedule_plan(request, templates)
    sprint = next(phase for phase in scheduled.phases if phase.key == "event_sprint")
    recovery = next(phase for phase in scheduled.phases if phase.key == "recovery")
    assert (sprint.start_date, sprint.end_date) == (request.target_date, request.event_end_date)
    assert recovery.start_date == request.event_end_date + timedelta(days=1)


def test_required_task_with_no_available_date_is_fatal() -> None:
    blocked = tuple(date(2026, 7, 1) + timedelta(days=offset) for offset in range(80))
    request = _request("submission_deadline", constraints=PlanConstraints(unavailable_dates=blocked))
    templates = build_phase_templates(CompetitionCard("cmp-1", "测试", "计算机"), time_model=request.time_model, experience_level="beginner")
    with pytest.raises(PlanSchedulingError, match="mandatory task"):
        schedule_plan(request, templates)


def test_validator_rejects_submission_without_defense() -> None:
    draft = PreparationPlanDraft("p", "c", "submission_deadline", date(2026, 8, 1))
    assert "submission plan requires defense_prep" in validate_plan(draft)
