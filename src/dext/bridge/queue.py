"""In-memory FetchJob bookkeeping for the fetch bridge.

Pure data structure: no asyncio, no IO. Every access happens inside the single
backend event loop (aiohttp handlers + GraphDriver share one loop, no parallelism),
so a deque + a single assigned slot is sufficient and lock-free. The asyncio Future
that fulfils a job lives in the `future` field but is attached/awaited by the bridge.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING

from dext.types import FetchAction

if TYPE_CHECKING:
    import asyncio

    from dext.types import FetchResult


class JobStatus(str, Enum):
    pending = "pending"
    assigned = "assigned"
    completed = "completed"
    failed = "failed"
    skipped = "skipped"


@dataclass
class JobContext:
    """Mirrors userscripts/src/types.ts JobContext. Built by SP6, echoed to the script."""

    university_name: str = ""
    agent_state: str = ""
    intent: str = ""
    parent_url: str = ""
    depth: int = 0
    org_unit_name: str = ""
    hints: list[str] = field(default_factory=list)


@dataclass
class QueueStats:
    pending: int
    assigned: int
    completed: int
    failed: int
    skipped: int


@dataclass
class FetchJob:
    """One queued browser fetch. `future` (excluded from repr/eq and never serialized)
    is the asyncio.Future the bridge awaits and the resolution ops fulfil."""

    id: str
    url: str
    context: JobContext
    created_at: datetime
    timeout_seconds: int
    identity_url: str | None = None
    action: FetchAction | None = None
    status: JobStatus = JobStatus.pending
    future: "asyncio.Future[FetchResult] | None" = field(default=None, repr=False, compare=False)


def new_job_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FetchQueue:
    """FIFO pending deque + a single in-flight slot + lifetime counters."""

    def __init__(self) -> None:
        self._pending: deque[FetchJob] = deque()
        self._assigned: FetchJob | None = None
        self._completed = 0
        self._failed = 0
        self._skipped = 0

    @property
    def assigned(self) -> FetchJob | None:
        return self._assigned

    def enqueue(self, job: FetchJob) -> None:
        job.status = JobStatus.pending
        self._pending.append(job)

    def take_next(self) -> FetchJob | None:
        """Pop next pending → assigned. Returns None (HTTP 204) when a job is already
        in flight OR the queue is empty (single-in-flight invariant)."""
        if self._assigned is not None or not self._pending:
            return None
        job = self._pending.popleft()
        job.status = JobStatus.assigned
        self._assigned = job
        return job

    def find(self, job_id: str) -> FetchJob | None:
        """The in-flight job iff its id matches — basis for idempotent complete/fail/
        skip/override (a stale id after timeout simply returns None)."""
        if self._assigned is not None and self._assigned.id == job_id:
            return self._assigned
        return None

    def finish(self, job: FetchJob, status: JobStatus) -> None:
        """Clear the in-flight slot and bump the lifetime counter for a terminal status."""
        if self._assigned is job:
            self._assigned = None
        if status is JobStatus.completed:
            self._completed += 1
        elif status is JobStatus.failed:
            self._failed += 1
        elif status is JobStatus.skipped:
            self._skipped += 1
        job.status = status

    def discard(self, job: FetchJob) -> None:
        """Remove a timed-out job whether pending or assigned; counts as failed so
        take_next() never later hands out a job nobody awaits."""
        if self._assigned is job:
            self._assigned = None
        else:
            try:
                self._pending.remove(job)
            except ValueError:
                pass
        self._failed += 1
        job.status = JobStatus.failed

    def stats(self) -> QueueStats:
        return QueueStats(
            pending=len(self._pending),
            assigned=1 if self._assigned is not None else 0,
            completed=self._completed,
            failed=self._failed,
            skipped=self._skipped,
        )
