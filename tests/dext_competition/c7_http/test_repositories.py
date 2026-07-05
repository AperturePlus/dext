from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dext_competition.repositories import (
    InMemoryPreparationAssistantHistoryRepository,
    InMemoryPreparationPlanRepository,
    PlanRevisionConflictError,
    cleanup_owner_remote_data,
)


def _snapshot(plan_id: str, revision: int) -> dict:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat()
    return {
        "id": plan_id,
        "competition": {"id": "cmp-1", "name": "测试竞赛", "category": "计算机", "rules_summary": {}},
        "target_date": "2026-08-20",
        "timeline_type": "submission",
        "revision": revision,
        "weekly_commitment": "hours6to10",
        "experience_level": "beginner",
        "status": "draft",
        "phases": [],
        "created_at": now,
        "updated_at": now,
        "tight_schedule": False,
        "overload": False,
    }


async def test_plan_repository_is_owner_scoped() -> None:
    repo = InMemoryPreparationPlanRepository()

    await repo.create_or_replace_plan("owner-a", _snapshot("plan-1", 0), idempotency_key="a")

    assert await repo.get_plan("owner-a", "plan-1") is not None
    assert await repo.get_plan("owner-b", "plan-1") is None


async def test_plan_repository_enforces_revision_increment() -> None:
    repo = InMemoryPreparationPlanRepository()
    await repo.create_or_replace_plan("owner", _snapshot("plan-1", 0), idempotency_key="create")

    with pytest.raises(PlanRevisionConflictError):
        await repo.save_new_revision("owner", "plan-1", _snapshot("plan-1", 0), idempotency_key="put-bad")

    saved = await repo.save_new_revision("owner", "plan-1", _snapshot("plan-1", 1), idempotency_key="put-ok")
    assert saved["revision"] == 1


async def test_cleanup_participant_deletes_plans_and_assistant_history() -> None:
    plans = InMemoryPreparationPlanRepository()
    history = InMemoryPreparationAssistantHistoryRepository()
    await plans.create_or_replace_plan("owner", _snapshot("plan-1", 0), idempotency_key="create")
    await history.append_turn("owner", "plan-1", {"role": "user", "content": "hi"})
    await history.append_turn("other", "plan-1", {"role": "user", "content": "keep"})

    result = await cleanup_owner_remote_data("owner", plans=plans, history=history)

    assert result.preparation_plans_deleted == 1
    assert result.preparation_assistant_history_deleted == 1
    assert await plans.list_plans("owner") == []
    assert len(await history.list_turns("other", "plan-1")) == 1
