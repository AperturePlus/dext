"""Owner-scoped preparation-plan application-state repositories.

The in-memory implementations are the default C7 test double. PostgreSQL
implementations can satisfy the same protocols without changing HTTP handlers.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol


class PlanRevisionConflictError(RuntimeError):
    """Raised when a persisted plan revision does not match the write intent."""


class PlanNotFoundError(LookupError):
    """Raised when a plan is absent inside the current owner scope."""


class PreparationPlanRepository(Protocol):
    async def list_plans(self, owner_id: str) -> list[dict[str, Any]]:
        ...

    async def get_plan(self, owner_id: str, plan_id: str) -> dict[str, Any] | None:
        ...

    async def create_or_replace_plan(
        self,
        owner_id: str,
        snapshot: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        ...

    async def save_new_revision(
        self,
        owner_id: str,
        plan_id: str,
        snapshot: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        ...

    async def delete_plan(self, owner_id: str, plan_id: str) -> bool:
        ...

    async def cleanup_owner(self, owner_id: str) -> int:
        ...


class PreparationAssistantHistoryRepository(Protocol):
    async def append_turn(
        self,
        owner_id: str,
        plan_id: str,
        turn: dict[str, Any],
    ) -> None:
        ...

    async def list_turns(
        self,
        owner_id: str,
        plan_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        ...

    async def delete_plan(self, owner_id: str, plan_id: str) -> int:
        ...

    async def cleanup_owner(self, owner_id: str) -> int:
        ...


def _copy(value: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(value)


def _plan_id(snapshot: dict[str, Any]) -> str:
    plan_id = str(snapshot.get("id") or "").strip()
    if not plan_id:
        raise ValueError("plan snapshot id is required")
    return plan_id


def _revision(snapshot: dict[str, Any]) -> int:
    value = snapshot.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("plan snapshot revision must be a non-negative integer")
    return value


class InMemoryPreparationPlanRepository:
    """Owner-scoped repository with optimistic revision checks."""

    def __init__(self) -> None:
        self._plans: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self._idempotency: dict[tuple[str, str], dict[str, Any]] = {}

    async def list_plans(self, owner_id: str) -> list[dict[str, Any]]:
        return [_copy(item) for item in self._plans.get(owner_id, {}).values()]

    async def get_plan(self, owner_id: str, plan_id: str) -> dict[str, Any] | None:
        value = self._plans.get(owner_id, {}).get(plan_id)
        return _copy(value) if value is not None else None

    async def create_or_replace_plan(
        self,
        owner_id: str,
        snapshot: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        idem_key = (owner_id, idempotency_key)
        if idem_key in self._idempotency:
            return _copy(self._idempotency[idem_key])
        plan_id = _plan_id(snapshot)
        incoming_revision = _revision(snapshot)
        current = self._plans[owner_id].get(plan_id)
        if current is not None and incoming_revision < _revision(current):
            raise PlanRevisionConflictError("incoming revision is older than persisted revision")
        stored = _copy(snapshot)
        self._plans[owner_id][plan_id] = stored
        self._idempotency[idem_key] = stored
        return _copy(stored)

    async def save_new_revision(
        self,
        owner_id: str,
        plan_id: str,
        snapshot: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        idem_key = (owner_id, idempotency_key)
        if idem_key in self._idempotency:
            return _copy(self._idempotency[idem_key])
        if _plan_id(snapshot) != plan_id:
            raise ValueError("path plan_id must match plan snapshot id")
        current = self._plans.get(owner_id, {}).get(plan_id)
        if current is None:
            raise PlanNotFoundError(plan_id)
        if _revision(snapshot) != _revision(current) + 1:
            raise PlanRevisionConflictError("incoming revision must be exactly current revision + 1")
        stored = _copy(snapshot)
        self._plans[owner_id][plan_id] = stored
        self._idempotency[idem_key] = stored
        return _copy(stored)

    async def delete_plan(self, owner_id: str, plan_id: str) -> bool:
        owner_plans = self._plans.get(owner_id, {})
        return owner_plans.pop(plan_id, None) is not None

    async def cleanup_owner(self, owner_id: str) -> int:
        count = len(self._plans.get(owner_id, {}))
        self._plans.pop(owner_id, None)
        for key in [key for key in self._idempotency if key[0] == owner_id]:
            del self._idempotency[key]
        return count


class InMemoryPreparationAssistantHistoryRepository:
    def __init__(self, *, max_turns_per_plan: int = 20) -> None:
        self._turns: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self._max_turns = max_turns_per_plan

    async def append_turn(
        self,
        owner_id: str,
        plan_id: str,
        turn: dict[str, Any],
    ) -> None:
        key = (owner_id, plan_id)
        self._turns[key].append(_copy(turn))
        if len(self._turns[key]) > self._max_turns:
            self._turns[key] = self._turns[key][-self._max_turns:]

    async def list_turns(
        self,
        owner_id: str,
        plan_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        turns = self._turns.get((owner_id, plan_id), ())
        return [_copy(turn) for turn in list(turns)[-limit:]]

    async def delete_plan(self, owner_id: str, plan_id: str) -> int:
        key = (owner_id, plan_id)
        count = len(self._turns.get(key, ()))
        self._turns.pop(key, None)
        return count

    async def cleanup_owner(self, owner_id: str) -> int:
        keys = [key for key in self._turns if key[0] == owner_id]
        count = sum(len(self._turns[key]) for key in keys)
        for key in keys:
            del self._turns[key]
        return count


@dataclass(frozen=True, slots=True)
class RemoteDataCleanupResult:
    owner_id: str
    preparation_plans_deleted: int
    preparation_assistant_history_deleted: int

    def to_public(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "buckets": {
                "preparation_plans": self.preparation_plans_deleted,
                "preparation_assistant_history": self.preparation_assistant_history_deleted,
            },
        }


async def cleanup_owner_remote_data(
    owner_id: str,
    *,
    plans: PreparationPlanRepository,
    history: PreparationAssistantHistoryRepository,
) -> RemoteDataCleanupResult:
    history_deleted = await history.cleanup_owner(owner_id)
    plans_deleted = await plans.cleanup_owner(owner_id)
    return RemoteDataCleanupResult(
        owner_id=owner_id,
        preparation_plans_deleted=plans_deleted,
        preparation_assistant_history_deleted=history_deleted,
    )


__all__ = [
    "InMemoryPreparationAssistantHistoryRepository",
    "InMemoryPreparationPlanRepository",
    "PlanNotFoundError",
    "PlanRevisionConflictError",
    "PreparationAssistantHistoryRepository",
    "PreparationPlanRepository",
    "RemoteDataCleanupResult",
    "cleanup_owner_remote_data",
]
