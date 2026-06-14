"""Single-slot pending-decision channel (overview §9).

SP6 sets a PendingDecision when a unit's detail fetches fail N times in a row; the
human resolves it from the panel. KISS: at most one decision awaits at a time.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ResolveCallback = Callable[["PendingDecision", str], "Awaitable[None] | None"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PendingDecision:
    """Mirrors userscripts/src/types.ts PendingDecision."""

    id: str
    kind: str
    org_unit_name: str
    failure_count: int
    sample_urls: list[str]
    suggested_action: str
    status: str = "pending"
    action: str | None = None
    created_at: datetime = field(default_factory=_utcnow)
    resolved_at: datetime | None = None


class DecisionCenter:
    def __init__(self) -> None:
        self._pending: PendingDecision | None = None
        self._callback: ResolveCallback | None = None

    def on_resolve(self, callback: ResolveCallback) -> None:
        self._callback = callback

    def set_decision(self, decision: PendingDecision) -> None:
        self._pending = decision

    def current(self) -> PendingDecision | None:
        return self._pending

    async def resolve(self, decision_id: str, action: str) -> bool:
        decision = self._pending
        if decision is None or decision.id != decision_id:
            logger.info("ignoring resolve for unknown/stale decision id %s", decision_id)
            return False
        decision.action = action
        decision.status = "resolved"
        decision.resolved_at = _utcnow()
        if self._callback is not None:
            outcome = self._callback(decision, action)
            if inspect.isawaitable(outcome):
                await outcome
        self._pending = None
        return True
