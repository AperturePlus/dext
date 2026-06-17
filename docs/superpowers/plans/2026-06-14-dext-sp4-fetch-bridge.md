# SP4 — Fetch bridge (aiohttp server + in-memory job queue + HumanFetcherBridge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build dext's fetch bridge — an `aiohttp` server implementing the **fixed** userscript HTTP contract, backed by an in-memory single-in-flight `FetchJob` queue and a `HumanFetcherBridge.fetch()` that turns "let a human grab this URL" into an `await`, plus mojibake repair, a best-effort redirect guard, and the pending-decision channel.

**Architecture:** A new `src/dext/bridge/` package of six focused modules. `queue.py` is a pure (no-asyncio) in-memory structure; `fetcher.py` owns the asyncio futures/timeouts and all job-resolution logic (thin-handler/fat-bridge); `server.py` is a pure HTTP↔domain adapter (UTF-8 decode + byte-faithful serialization to the TS interfaces). `decision.py`, `mojibake.py`, `redirect.py` are independent helpers. The one shared output DTO (`FetchResult`) is added to `dext.types` (overview §5). No DB, no LLM, no HTML parsing.

**Tech Stack:** Python 3.11, `aiohttp` (already a declared dep — server + the redirect probe client), stdlib `asyncio`/`json`/`uuid`/`datetime`/`collections.deque`/`urllib.parse`/`time`, pytest + pytest-asyncio (`asyncio_mode="auto"`). Tests use `aiohttp.test_utils.TestServer`/`TestClient` directly (no `pytest-aiohttp` needed).

**Spec:** `docs/superpowers/specs/2026-06-13-dext-04-fetch-bridge-design.md` (authoritative, incl. §13 implementation decisions). **Contract to match byte-for-byte:** `userscripts/src/api.ts` + `userscripts/src/types.ts`.

---

## Pre-verified facts (don't re-derive)

- **Branch:** work happens on `sp4-fetch-bridge` (already created off `sp3-page-processing`). SP4 depends only on SP1 (config) + `dext.types` (FetchAction/PaginationState from SP3). Stay on this branch.
- **`aiohttp>=3.9.0` is already declared** in `pyproject.toml` (resolved 3.12.15). **No `pyproject.toml` change is needed.** `httpx` is installed only transitively — do NOT use it; the redirect probe uses `aiohttp` (spec §13.1).
- `dext.types` already exists; it re-exports the enums from `dext.storage.models` and defines `ProfessorPayload`, `FetchAction`, `PaginationState`. SP4 appends `FetchResult` and extends `__all__`.
- `dext.config.Settings` already has `bridge_host="127.0.0.1"`, `bridge_port=21520`, `fetch_timeout_seconds=60`. The bridge reads only `settings.fetch_timeout_seconds`.
- Pytest config: `testpaths=["tests"]`, `pythonpath=["src"]`, `asyncio_mode="auto"`. Async tests need no `@pytest.mark.asyncio` decorator.
- **Test command (repo root `D:\pyprj\dext`):** `uv run pytest <path> -v`.
- Existing test style: plain `pytest`, inline UTF-8 Chinese literals, direct `from dext.X import ...`. Bridge tests build mojibake fixtures via `"中文".encode("utf-8").decode("latin-1")`.
- **The fixed contract** (from `api.ts`): base `http://127.0.0.1:21520/api`; `GET /jobs/next`→FetchJob|204; `POST /jobs/{id}/complete` `{html,url,title,pagination_states}`→`{status,next_job?}`; `POST /jobs/{id}/fail` `{message}`; `POST /jobs/{id}/skip`; `POST /jobs/{id}/override` `{new_url}`→FetchJob; `GET /status`→StatusResponse; `GET /decision`→PendingDecision|204; `POST /decision/{id}/resolve` `{action}`. All UTF-8 JSON, `ensure_ascii=False`.

## Bug-risk guards (from project memory — each has a dedicated test)

- **Late `/complete` after job timeout** → idempotent: `_resolvable()` returns `None` when the id is unknown/stale or the future is already done; handlers return a benign `{status:"ignored"}` (200) and never crash. The 60 s job timeout is distinct from the script's 10 s per-request HTTP timeout.
- **Single in-flight** → `take_next()` returns `None` (→204) whenever a job is already assigned.
- **synthetic-URL identity** → `FetchResult.identity_url = job.identity_url or job.url` (the form-pagination synthetic URL is the cache key, not the real final URL).
- **StatusResponse counters accumulate** across ephemeral jobs (`completed/failed/skipped` are lifetime totals).
- **`/override`** keeps the same job id + same pending future + assigned status, only swaps `url`.
- **Timed-out pending job** is removed via `queue.discard()` so `take_next()` never hands out a job nobody awaits.

## File Structure

```
dext/
├─ src/dext/
│  ├─ types.py                 # MODIFY (Task 2) — append FetchResult, extend __all__
│  └─ bridge/
│     ├─ __init__.py           # CREATE (Task 1 marker; Task 9 re-exports public interface)
│     ├─ queue.py              # CREATE (Task 3) — JobStatus, JobContext, QueueStats, FetchJob, FetchQueue, new_job_id, utcnow
│     ├─ mojibake.py           # CREATE (Task 4) — repair_mojibake_text
│     ├─ fetcher.py            # CREATE (Task 5) — HumanFetcherBridge
│     ├─ decision.py           # CREATE (Task 6) — PendingDecision, DecisionCenter
│     ├─ redirect.py           # CREATE (Task 7) — RedirectVerdict, classify_redirect, RedirectGuard
│     └─ server.py             # CREATE (Task 8) — serializers, parsers, handlers, create_app, run_server
└─ tests/
   ├─ test_bridge_import.py        # Task 1 (marker) + Task 9 (full public interface)
   ├─ test_types_fetch_result.py   # Task 2
   ├─ test_bridge_queue.py         # Task 3
   ├─ test_bridge_mojibake.py      # Task 4
   ├─ test_bridge_fetcher.py       # Task 5
   ├─ test_bridge_decision.py      # Task 6
   ├─ test_bridge_redirect.py      # Task 7
   └─ test_bridge_server.py        # Task 8
```

Module DAG (no cycles): `queue`→`dext.types`; `mojibake`→stdlib; `fetcher`→`queue`+`mojibake`+`dext.types`(+`dext.config` under TYPE_CHECKING); `decision`→stdlib; `redirect`→stdlib(+aiohttp lazy); `server`→aiohttp+`queue`+`dext.types`(+`fetcher`/`decision` under TYPE_CHECKING).

---

### Task 1: Scaffold — `bridge` package + import smoke test

**Files:**
- Create: `src/dext/bridge/__init__.py`
- Test: `tests/test_bridge_import.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bridge_import.py
def test_bridge_package_imports():
    import dext.bridge  # noqa: F401
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/test_bridge_import.py -v` → FAIL (ModuleNotFoundError).

- [ ] **Step 3: Create the package** — `src/dext/bridge/__init__.py`:

```python
"""dext fetch bridge — aiohttp server + in-memory job queue + HumanFetcherBridge.

Implements the fixed userscript HTTP contract (overview §4). No DB, no LLM, no HTML
parsing. Public interface (spec §10) re-exported in Task 9.
"""
```

- [ ] **Step 4: Run test to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "chore(sp4): scaffold bridge package"`

---

### Task 2: `FetchResult` shared DTO

**Files:**
- Modify: `src/dext/types.py`
- Test: `tests/test_types_fetch_result.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_types_fetch_result.py
from dext.types import FetchResult, PaginationState


def test_fetch_result_defaults_block_reason_none():
    r = FetchResult(
        identity_url="https://x/list", requested_url="https://x/list",
        final_url="https://x/list?p=2", status_code=None,
        html="<html>中文</html>", title="教师", pagination_states=[],
    )
    assert r.identity_url == "https://x/list"
    assert r.block_reason is None
    assert r.pagination_states == []


def test_fetch_result_carries_states_and_block_reason():
    ps = PaginationState(kind="form_submit", state_id="form:f:p:2", label="f 第 2 页",
                         page_index=2, form_name="f", fields={"p": "2"}, submit=True,
                         synthetic_url="https://x/list?__ycl_page=2", url="https://x/list")
    r = FetchResult(identity_url="i", requested_url="r", final_url="f", status_code=200,
                    html="", title="", pagination_states=[ps], block_reason="timeout")
    assert r.pagination_states[0].page_index == 2
    assert r.block_reason == "timeout"
```

- [ ] **Step 2: Run** — FAIL (ImportError: FetchResult).

- [ ] **Step 3: Implement** — append to `src/dext/types.py` (after `PaginationState`), and add `"FetchResult"` to `__all__`:

```python
@dataclass
class FetchResult:
    """One browser fetch outcome (SP4 produces → SP6 consumes; overview §5).
    `identity_url` is the cache/node key (= job.identity_url or job.url). `html`/`title`
    are already UTF-8 / mojibake-repaired. `block_reason` is set only on failure
    (waf/timeout/human_failed/human_skip/...)."""

    identity_url: str
    requested_url: str
    final_url: str
    status_code: int | None
    html: str
    title: str
    pagination_states: list[PaginationState]
    block_reason: str | None = None
```

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add FetchResult shared DTO to dext.types"`

---

### Task 3: `queue.py` — pure in-memory job bookkeeping

**Files:**
- Create: `src/dext/bridge/queue.py`
- Test: `tests/test_bridge_queue.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_queue.py
from datetime import datetime, timezone

from dext.bridge.queue import (
    FetchJob, FetchQueue, JobContext, JobStatus, new_job_id, utcnow,
)


def _job(url="https://x/1", identity_url=None):
    return FetchJob(id=new_job_id(), url=url, context=JobContext(),
                    created_at=utcnow(), timeout_seconds=60, identity_url=identity_url)


def test_take_next_is_fifo_and_marks_assigned():
    q = FetchQueue()
    a, b = _job("u1"), _job("u2")
    q.enqueue(a); q.enqueue(b)
    first = q.take_next()
    assert first is a and first.status is JobStatus.assigned
    assert q.assigned is a


def test_single_in_flight_blocks_second_take():
    q = FetchQueue()
    q.enqueue(_job("u1")); q.enqueue(_job("u2"))
    assert q.take_next() is not None
    assert q.take_next() is None  # one already assigned


def test_take_next_empty_returns_none():
    assert FetchQueue().take_next() is None


def test_find_matches_assigned_only():
    q = FetchQueue()
    j = _job(); q.enqueue(j); q.take_next()
    assert q.find(j.id) is j
    assert q.find("nope") is None


def test_finish_clears_slot_and_counts():
    q = FetchQueue()
    j = _job(); q.enqueue(j); q.take_next()
    q.finish(j, JobStatus.completed)
    assert q.assigned is None
    s = q.stats()
    assert (s.completed, s.assigned, s.pending) == (1, 0, 0)


def test_counters_accumulate_across_jobs():
    q = FetchQueue()
    for status in (JobStatus.completed, JobStatus.failed, JobStatus.skipped, JobStatus.completed):
        j = _job(); q.enqueue(j); q.take_next(); q.finish(j, status)
    s = q.stats()
    assert (s.completed, s.failed, s.skipped) == (2, 1, 1)


def test_discard_removes_pending_job_and_counts_failed():
    q = FetchQueue()
    a, b = _job("u1"), _job("u2")
    q.enqueue(a); q.enqueue(b)
    q.discard(b)              # b never assigned
    assert q.stats().failed == 1
    assert q.take_next() is a
    assert q.take_next() is None   # b is gone, a in flight
    # nothing else pending after a:
    q.finish(a, JobStatus.completed)
    assert q.take_next() is None


def test_discard_clears_assigned_job():
    q = FetchQueue()
    j = _job(); q.enqueue(j); q.take_next()
    q.discard(j)
    assert q.assigned is None
    assert q.stats().failed == 1


def test_helpers_produce_unique_ids_and_aware_utc():
    assert new_job_id() != new_job_id()
    assert utcnow().tzinfo is timezone.utc
```

- [ ] **Step 2: Run** — FAIL (no module).

- [ ] **Step 3: Implement** — `src/dext/bridge/queue.py`:

```python
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
```

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add FetchQueue + job DTOs (single in-flight, lifetime counters, discard)"`

---

### Task 4: `mojibake.py` — UTF-8-as-Latin-1 repair

**Files:**
- Create: `src/dext/bridge/mojibake.py`
- Test: `tests/test_bridge_mojibake.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_mojibake.py
from dext.bridge.mojibake import repair_mojibake_text


def test_repairs_utf8_decoded_as_latin1():
    original = "中文教师"
    mojibake = original.encode("utf-8").decode("latin-1")
    assert mojibake != original
    assert repair_mojibake_text(mojibake) == original


def test_repairs_within_html():
    html = "<h1>计算机学院</h1>"
    assert repair_mojibake_text(html.encode("utf-8").decode("latin-1")) == html


def test_leaves_correct_utf8_untouched():
    for s in ("中文教师", "Hello world", "", "教授 Zhang San"):
        assert repair_mojibake_text(s) == s


def test_leaves_legit_latin1_text_untouched():
    # 'café' encodes to latin-1 but b'caf\xe9' is invalid utf-8 → returned unchanged.
    assert repair_mojibake_text("café") == "café"
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** — `src/dext/bridge/mojibake.py`:

```python
"""Best-effort repair of UTF-8 bytes that were mis-decoded as Latin-1.

Safe by construction (see repair_mojibake_text). Forward UTF-8 reading and
`ensure_ascii=False` JSON output handle the rest of the UTF-8 invariant (overview §6).
"""

from __future__ import annotations


def _suspicion(text: str) -> int:
    """Count Latin-1 high bytes (U+0080–U+00FF): abundant in UTF-8-as-Latin-1
    mojibake, absent from correctly-decoded CJK."""
    return sum(1 for ch in text if 0x80 <= ord(ch) <= 0xFF)


def repair_mojibake_text(text: str) -> str:
    """Repair e.g. 'ä¸­æ–‡' → '中文'.

    Safe: text containing real CJK can't .encode('latin-1') (raises → returned
    untouched, so correct UTF-8 is never harmed); non-mojibake Latin-1 fails the
    utf-8 round-trip decode (raises → returned untouched, so 'café' survives).
    The repair is accepted only when it does not increase the high-byte suspicion
    count, guarding against rare double-repair / false positives.
    """
    if not text:
        return text
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return text
    try:
        repaired = raw.decode("utf-8")
    except UnicodeDecodeError:
        return text
    if repaired == text or _suspicion(repaired) > _suspicion(text):
        return text
    return repaired
```

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add repair_mojibake_text (safe latin-1→utf-8 round trip)"`

---

### Task 5: `fetcher.py` — `HumanFetcherBridge`

**Files:**
- Create: `src/dext/bridge/fetcher.py`
- Test: `tests/test_bridge_fetcher.py`

- [ ] **Step 1: Write the failing tests** (async; `asyncio_mode="auto"`)

```python
# tests/test_bridge_fetcher.py
import asyncio
from types import SimpleNamespace

from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.queue import JobContext
from dext.types import FetchResult


def _bridge(timeout=60):
    return HumanFetcherBridge(SimpleNamespace(fetch_timeout_seconds=timeout))


def _ctx():
    return JobContext(university_name="清华大学", intent="org_listing")


async def _await_job(bridge):
    for _ in range(200):
        job = bridge.next_job()
        if job is not None:
            return job
        await asyncio.sleep(0)
    raise AssertionError("job was never queued")


async def test_fetch_resolves_on_complete_with_repaired_html():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x/list", context=_ctx()))
    job = await _await_job(b)
    assert job.url == "https://x/list"
    mojibake = "计算机学院".encode("utf-8").decode("latin-1")
    assert b.complete(job.id, html=f"<h1>{mojibake}</h1>", final_url="https://x/list?p=1", title="教师") is True
    result = await task
    assert isinstance(result, FetchResult)
    assert result.identity_url == "https://x/list"        # defaults to url
    assert result.final_url == "https://x/list?p=1"
    assert "计算机学院" in result.html                      # mojibake repaired
    assert result.block_reason is None
    assert b.stats().completed == 1


async def test_identity_url_preserved_as_cache_key():
    b = _bridge()
    task = asyncio.ensure_future(
        b.fetch(url="https://x/list?__ycl_page=2", identity_url="https://x/list#syn2", context=_ctx()))
    job = await _await_job(b)
    b.complete(job.id, html="x", final_url="https://x/list?p=2", title="t")
    assert (await task).identity_url == "https://x/list#syn2"


async def test_single_in_flight_second_next_is_none():
    b = _bridge()
    t1 = asyncio.ensure_future(b.fetch(url="u1", context=_ctx()))
    t2 = asyncio.ensure_future(b.fetch(url="u2", context=_ctx()))
    job = await _await_job(b)
    assert b.next_job() is None
    b.complete(job.id, html="", final_url="u1", title=""); await t1
    job2 = await _await_job(b)
    b.complete(job2.id, html="", final_url="u2", title=""); await t2


async def test_fail_sets_block_reason_from_message():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    b.fail(job.id, "WAF 拦截")
    assert (await task).block_reason == "WAF 拦截"
    assert b.stats().failed == 1


async def test_fail_empty_message_defaults_human_failed():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    b.fail(job.id, "   ")
    assert (await task).block_reason == "human_failed"


async def test_skip_is_terminal_human_skip():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    b.skip(job.id)
    assert (await task).block_reason == "human_skip"
    assert b.stats().skipped == 1


async def test_late_complete_after_resolution_ignored():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="u", context=_ctx()))
    job = await _await_job(b)
    assert b.complete(job.id, html="", final_url="u", title="") is True
    await task
    assert b.complete(job.id, html="", final_url="u", title="") is False   # stale
    assert b.stats().completed == 1


async def test_unknown_job_id_ignored_everywhere():
    b = _bridge()
    assert b.complete("nope", html="", final_url="u", title="") is False
    assert b.fail("nope", "x") is False
    assert b.skip("nope") is False
    assert b.override("nope", "u2") is None


async def test_timeout_returns_block_reason_timeout_and_counts_failed():
    b = _bridge(timeout=0.02)
    result = await b.fetch(url="https://x/list", context=_ctx())
    assert result.block_reason == "timeout"
    assert result.final_url == "https://x/list"
    assert b.stats().failed == 1
    assert b.current_job() is None


async def test_override_swaps_url_keeps_job_and_future():
    b = _bridge()
    task = asyncio.ensure_future(b.fetch(url="https://x/wrong", context=_ctx()))
    job = await _await_job(b)
    updated = b.override(job.id, "https://x/right")
    assert updated is not None and updated.id == job.id and updated.url == "https://x/right"
    assert b.current_job().url == "https://x/right"
    b.complete(job.id, html="", final_url="https://x/right", title="")
    result = await task
    assert result.requested_url == "https://x/right"
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** — `src/dext/bridge/fetcher.py`:

```python
"""HumanFetcherBridge — the GraphDriver's single fetch entry point.

`fetch()` enqueues a job and awaits the matching /complete (or /fail /skip /timeout).
All job-resolution methods are idempotent against stale (timed-out) job ids: they log
and no-op rather than raise, so a late /complete after a 60 s timeout can't crash the
server. Thin-handler/fat-bridge: server.py holds no job logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from dext.bridge.mojibake import repair_mojibake_text
from dext.bridge.queue import (
    FetchJob, FetchQueue, JobContext, JobStatus, QueueStats, new_job_id, utcnow,
)
from dext.types import FetchAction, FetchResult, PaginationState

if TYPE_CHECKING:
    from dext.config import Settings

logger = logging.getLogger(__name__)


class HumanFetcherBridge:
    def __init__(self, settings: "Settings") -> None:
        self._timeout = settings.fetch_timeout_seconds
        self._queue = FetchQueue()

    async def fetch(self, *, url: str, context: JobContext,
                    identity_url: str | None = None,
                    action: FetchAction | None = None) -> FetchResult:
        loop = asyncio.get_running_loop()
        job = FetchJob(
            id=new_job_id(), url=url, context=context, created_at=utcnow(),
            timeout_seconds=self._timeout, identity_url=identity_url, action=action,
            future=loop.create_future(),
        )
        self._queue.enqueue(job)
        try:
            return await asyncio.wait_for(job.future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._queue.discard(job)
            logger.warning("fetch job %s timed out after %ss (url=%s)", job.id, self._timeout, url)
            return FetchResult(
                identity_url=identity_url or url, requested_url=url, final_url=url,
                status_code=None, html="", title="", pagination_states=[], block_reason="timeout",
            )

    # ---- job-resolution ops (called by server handlers) ----

    def next_job(self) -> FetchJob | None:
        return self._queue.take_next()

    def complete(self, job_id: str, *, html: str, final_url: str, title: str,
                 pagination_states: list[PaginationState] | None = None) -> bool:
        job = self._resolvable(job_id)
        if job is None:
            return False
        result = FetchResult(
            identity_url=job.identity_url or job.url, requested_url=job.url, final_url=final_url,
            status_code=None, html=repair_mojibake_text(html), title=repair_mojibake_text(title),
            pagination_states=pagination_states or [], block_reason=None,
        )
        job.future.set_result(result)
        self._queue.finish(job, JobStatus.completed)
        return True

    def fail(self, job_id: str, message: str) -> bool:
        job = self._resolvable(job_id)
        if job is None:
            return False
        job.future.set_result(self._failed_result(job, block_reason=message.strip() or "human_failed"))
        self._queue.finish(job, JobStatus.failed)
        return True

    def skip(self, job_id: str) -> bool:
        job = self._resolvable(job_id)
        if job is None:
            return False
        job.future.set_result(self._failed_result(job, block_reason="human_skip"))
        self._queue.finish(job, JobStatus.skipped)
        return True

    def override(self, job_id: str, new_url: str) -> FetchJob | None:
        """Swap the in-flight job's URL, keeping the same id/future/assigned status."""
        job = self._queue.find(job_id)
        if job is None:
            return None
        job.url = new_url
        return job

    def stats(self) -> QueueStats:
        return self._queue.stats()

    def current_job(self) -> FetchJob | None:
        return self._queue.assigned

    # ---- internals ----

    def _resolvable(self, job_id: str) -> FetchJob | None:
        job = self._queue.find(job_id)
        if job is None:
            logger.info("ignoring call for unknown/stale job id %s", job_id)
            return None
        if job.future is None or job.future.done():
            logger.info("ignoring late call for already-resolved job %s", job_id)
            return None
        return job

    def _failed_result(self, job: FetchJob, *, block_reason: str) -> FetchResult:
        return FetchResult(
            identity_url=job.identity_url or job.url, requested_url=job.url, final_url=job.url,
            status_code=None, html="", title="", pagination_states=[], block_reason=block_reason,
        )
```

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add HumanFetcherBridge (await fetch, idempotent resolution, timeout)"`

---

### Task 6: `decision.py` — pending-decision channel

**Files:**
- Create: `src/dext/bridge/decision.py`
- Test: `tests/test_bridge_decision.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_decision.py
from dext.bridge.decision import DecisionCenter, PendingDecision


def _decision(did="d1"):
    return PendingDecision(id=did, kind="detail_failures", org_unit_name="计算机学院",
                           failure_count=3, sample_urls=["https://x/1", "https://x/2"],
                           suggested_action="switch_failed_to_human")


async def test_set_and_get_decision():
    dc = DecisionCenter()
    assert dc.current() is None
    d = _decision(); dc.set_decision(d)
    assert dc.current() is d
    assert d.status == "pending"


async def test_resolve_fires_async_callback_and_clears_slot():
    dc = DecisionCenter()
    seen = []
    async def cb(decision, action):
        seen.append((decision.id, action))
    dc.on_resolve(cb)
    d = _decision(); dc.set_decision(d)
    assert await dc.resolve("d1", "switch_failed_to_human") is True
    assert seen == [("d1", "switch_failed_to_human")]
    assert dc.current() is None
    assert d.status == "resolved" and d.action == "switch_failed_to_human"
    assert d.resolved_at is not None


async def test_resolve_unknown_id_ignored():
    dc = DecisionCenter(); dc.set_decision(_decision())
    assert await dc.resolve("other", "x") is False
    assert dc.current() is not None


async def test_sync_callback_supported():
    dc = DecisionCenter(); seen = []
    dc.on_resolve(lambda d, a: seen.append(a))
    dc.set_decision(_decision())
    await dc.resolve("d1", "act")
    assert seen == ["act"]
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** — `src/dext/bridge/decision.py`:

```python
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
```

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add DecisionCenter single-slot pending-decision channel"`

---

### Task 7: `redirect.py` — best-effort redirect/wechat guard

**Files:**
- Create: `src/dext/bridge/redirect.py`
- Test: `tests/test_bridge_redirect.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bridge_redirect.py
from dext.bridge.redirect import (
    BLOCKED, OFFSITE_OK, OK, PROBE_FAILED, RedirectGuard, classify_redirect,
)


def test_classify_blocks_wechat():
    v = classify_redirect("https://teacher.uni.edu.cn/p", "https://mp.weixin.qq.com/s/abc")
    assert v.verdict == BLOCKED and v.reason == "wechat_redirect"
    assert v.final_host == "mp.weixin.qq.com"


def test_classify_offsite_personal_homepage_ok():
    v = classify_redirect("https://cs.uni.edu.cn/faculty/zhang", "https://zhang.github.io/")
    assert v.verdict == OFFSITE_OK and v.final_host == "zhang.github.io"


def test_classify_same_host_ok():
    v = classify_redirect("https://cs.uni.edu.cn/a", "https://cs.uni.edu.cn/a/index.html")
    assert v.verdict == OK


async def test_probe_blocks_wechat_via_resolver():
    async def resolver(url):
        return "https://mp.weixin.qq.com/s/x"
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://t.uni.edu.cn/p")).verdict == BLOCKED


async def test_probe_offsite_ok_via_resolver():
    async def resolver(url):
        return "https://zhang.github.io/"
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://cs.uni.edu.cn/f/zhang")).verdict == OFFSITE_OK


async def test_probe_failure_returns_probe_failed():
    async def resolver(url):
        raise RuntimeError("WAF")
    assert (await RedirectGuard(resolver=resolver).probe_redirect("https://x/p")).verdict == PROBE_FAILED
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** — `src/dext/bridge/redirect.py`:

```python
"""Best-effort redirect / wechat-公众号 guard (overview §7).

SP6 may optionally call probe_redirect() before enqueueing a detail candidate to
drop obvious wechat traps or redirect probes that fail. The HTTP IO is an
injectable resolver so the (pure) host classification is fully unit-testable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# wechat / QQ public-account article hosts that signal a noise-page redirect trap.
DEFAULT_BLACKLIST: frozenset[str] = frozenset({"mp.weixin.qq.com", "weixin.qq.com"})

OK = "ok"
BLOCKED = "blocked"
OFFSITE_OK = "offsite_ok"
PROBE_FAILED = "probe_failed"

Resolver = Callable[[str], Awaitable[str]]


@dataclass
class RedirectVerdict:
    verdict: str
    final_url: str | None = None
    final_host: str | None = None
    reason: str | None = None


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def classify_redirect(requested_url: str, final_url: str, *,
                      blacklist: frozenset[str] = DEFAULT_BLACKLIST) -> RedirectVerdict:
    """Pure host-based classification: blacklisted final host → blocked; a different
    host → offsite_ok (personal homepage is legitimate); same host → ok."""
    final_host = _host(final_url)
    if final_host in blacklist:
        return RedirectVerdict(BLOCKED, final_url=final_url, final_host=final_host, reason="wechat_redirect")
    if final_host and final_host != _host(requested_url):
        return RedirectVerdict(OFFSITE_OK, final_url=final_url, final_host=final_host)
    return RedirectVerdict(OK, final_url=final_url, final_host=final_host)


async def _aiohttp_resolver(url: str, *, timeout: float = 8.0) -> str:
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(url, allow_redirects=True) as resp:
            return str(resp.url)


class RedirectGuard:
    def __init__(self, *, blacklist: frozenset[str] = DEFAULT_BLACKLIST,
                 resolver: Resolver | None = None) -> None:
        self._blacklist = blacklist
        self._resolver = resolver or _aiohttp_resolver

    async def probe_redirect(self, url: str) -> RedirectVerdict:
        try:
            final_url = await self._resolver(url)
        except Exception as exc:  # WAF / timeout / DNS — best-effort, never propagate
            logger.info("redirect probe failed for %s: %r", url, exc)
            return RedirectVerdict(PROBE_FAILED, reason="probe_failed")
        return classify_redirect(url, final_url, blacklist=self._blacklist)
```

- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add RedirectGuard (pure classify + injectable aiohttp probe)"`

---

### Task 8: `server.py` — aiohttp handlers + contract serialization

**Files:**
- Create: `src/dext/bridge/server.py`
- Test: `tests/test_bridge_server.py`

- [ ] **Step 1: Write the failing tests** (aiohttp test client; drives both the "driver" side via `bridge.fetch()` and the "script" side via HTTP)

```python
# tests/test_bridge_server.py
import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dext.bridge.decision import DecisionCenter, PendingDecision
from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.queue import JobContext
from dext.bridge.server import create_app


@pytest.fixture
async def harness():
    bridge = HumanFetcherBridge(SimpleNamespace(fetch_timeout_seconds=60))
    dc = DecisionCenter()
    client = TestClient(TestServer(create_app(bridge, dc)))
    await client.start_server()
    try:
        yield SimpleNamespace(client=client, bridge=bridge, dc=dc)
    finally:
        await client.close()


async def _poll_next(client):
    for _ in range(200):
        resp = await client.get("/api/jobs/next")
        if resp.status == 200:
            return await resp.json()
        assert resp.status == 204
        await asyncio.sleep(0)
    raise AssertionError("no job offered")


def _post(client, path, payload):
    return client.post(path, data=json.dumps(payload))


async def test_next_is_204_when_empty(harness):
    resp = await harness.client.get("/api/jobs/next")
    assert resp.status == 204


async def test_full_round_trip_complete(harness):
    ctx = JobContext(university_name="清华大学", intent="org_listing")
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=ctx))
    job = await _poll_next(harness.client)
    assert job["url"] == "https://x/list"
    assert job["status"] == "assigned"
    assert job["context"]["university_name"] == "清华大学"
    assert job["action"] is None and job["identity_url"] is None
    mojibake = "计算机学院".encode("utf-8").decode("latin-1")
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                       {"html": f"<h1>{mojibake}</h1>", "url": "https://x/list?p=1",
                        "title": "教师", "pagination_states": []})
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok" and body["next_job"] is None
    result = await task
    assert result.final_url == "https://x/list?p=1"
    assert "计算机学院" in result.html


async def test_single_in_flight_returns_204(harness):
    t1 = asyncio.ensure_future(harness.bridge.fetch(url="u1", context=JobContext()))
    t2 = asyncio.ensure_future(harness.bridge.fetch(url="u2", context=JobContext()))
    job = await _poll_next(harness.client)
    assert (await harness.client.get("/api/jobs/next")).status == 204
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "u1", "title": "", "pagination_states": []})
    await t1
    job2 = await _poll_next(harness.client)
    await _post(harness.client, f"/api/jobs/{job2['id']}/complete",
                {"html": "", "url": "u2", "title": "", "pagination_states": []})
    await t2


async def test_fail_path_sets_block_reason(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="u", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/fail", {"message": "页面打不开"})
    assert resp.status == 200 and (await resp.json())["status"] == "ok"
    assert (await task).block_reason == "页面打不开"


async def test_skip_path_is_human_skip(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="u", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await harness.client.post(f"/api/jobs/{job['id']}/skip")
    assert resp.status == 200
    assert (await task).block_reason == "human_skip"


async def test_override_swaps_url(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/wrong", context=JobContext()))
    job = await _poll_next(harness.client)
    resp = await _post(harness.client, f"/api/jobs/{job['id']}/override", {"new_url": "https://x/right"})
    assert resp.status == 200
    assert (await resp.json())["url"] == "https://x/right"
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "https://x/right", "title": "", "pagination_states": []})
    assert (await task).requested_url == "https://x/right"


async def test_override_unknown_job_is_204(harness):
    resp = await _post(harness.client, "/api/jobs/nope/override", {"new_url": "https://x/y"})
    assert resp.status == 204


async def test_stale_complete_is_ignored_200(harness):
    resp = await _post(harness.client, "/api/jobs/ghost/complete",
                       {"html": "", "url": "u", "title": "", "pagination_states": []})
    assert resp.status == 200
    assert (await resp.json())["status"] == "ignored"


async def test_status_reports_counts_and_utf8(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=JobContext(university_name="北京大学")))
    await _poll_next(harness.client)
    resp = await harness.client.get("/api/status")
    assert resp.status == 200
    assert resp.headers["Content-Type"] == "application/json; charset=utf-8"
    raw = await resp.text()
    assert "北京大学" in raw          # ensure_ascii=False
    data = json.loads(raw)
    assert data["queue"]["assigned"] == 1
    assert data["current_job"]["url"] == "https://x/list"
    assert isinstance(data["server_uptime_seconds"], (int, float))
    assert data["pending_decision"] is None
    # cleanup
    await _post(harness.client, f"/api/jobs/{data['current_job']['id']}/complete",
                {"html": "", "url": "https://x/list", "title": "", "pagination_states": []})
    await task


async def test_decision_get_204_then_resolve(harness):
    assert (await harness.client.get("/api/decision")).status == 204
    seen = []
    harness.dc.on_resolve(lambda d, a: seen.append(a))
    harness.dc.set_decision(PendingDecision(id="d1", kind="detail_failures", org_unit_name="物理学院",
                                            failure_count=3, sample_urls=["https://x/1"],
                                            suggested_action="switch_failed_to_human"))
    got = await harness.client.get("/api/decision")
    assert got.status == 200
    body = await got.json()
    assert body["id"] == "d1" and body["org_unit_name"] == "物理学院"
    assert body["failure_count"] == 3 and body["resolved_at"] is None
    resp = await _post(harness.client, "/api/decision/d1/resolve", {"action": "switch_failed_to_human"})
    assert resp.status == 200 and (await resp.json())["status"] == "ok"
    assert seen == ["switch_failed_to_human"]
    assert (await harness.client.get("/api/decision")).status == 204


async def test_pagination_states_parsed_into_result(harness):
    task = asyncio.ensure_future(harness.bridge.fetch(url="https://x/list", context=JobContext()))
    job = await _poll_next(harness.client)
    state = {"kind": "form_submit", "state_id": "form:f:p:2", "label": "f 第 2 页", "page_index": 2,
             "total_pages": 5, "form_name": "f", "fields": {"p": "2"}, "submit": True,
             "synthetic_url": "https://x/list?__ycl_page=2", "url": "https://x/list"}
    await _post(harness.client, f"/api/jobs/{job['id']}/complete",
                {"html": "", "url": "https://x/list", "title": "", "pagination_states": [state, {"bad": 1}]})
    result = await task
    assert len(result.pagination_states) == 1          # malformed one dropped
    assert result.pagination_states[0].page_index == 2
    assert result.pagination_states[0].total_pages == 5
```

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** — `src/dext/bridge/server.py`:

```python
"""aiohttp adapter for the fixed userscript HTTP contract (overview §4, api.ts).

Pure HTTP↔domain boundary: read body as forced UTF-8, call HumanFetcherBridge /
DecisionCenter, serialize back to the TS interface shapes with ensure_ascii=False.
No job logic lives here.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web

from dext.bridge.queue import FetchJob, JobContext
from dext.types import FetchAction, PaginationState

if TYPE_CHECKING:
    from dext.bridge.decision import DecisionCenter, PendingDecision
    from dext.bridge.fetcher import HumanFetcherBridge
    from dext.bridge.queue import QueueStats

logger = logging.getLogger(__name__)

API_PREFIX = "/api"


# ---------- serialization (backend dataclass → script JSON shape) ----------

def serialize_action(action: FetchAction | None) -> dict | None:
    if action is None:
        return None
    out: dict[str, Any] = {"kind": action.kind}
    for key in ("form_name", "fields", "submit", "synthetic_url", "label", "page_index", "state_id"):
        value = getattr(action, key)
        if value is not None:
            out[key] = value
    return out


def serialize_context(ctx: JobContext) -> dict:
    return {
        "university_name": ctx.university_name, "agent_state": ctx.agent_state,
        "intent": ctx.intent, "parent_url": ctx.parent_url, "depth": ctx.depth,
        "org_unit_name": ctx.org_unit_name, "hints": list(ctx.hints),
    }


def serialize_job(job: FetchJob) -> dict:
    return {
        "id": job.id, "url": job.url, "status": job.status.value,
        "context": serialize_context(job.context), "created_at": job.created_at.isoformat(),
        "timeout_seconds": job.timeout_seconds, "action": serialize_action(job.action),
        "identity_url": job.identity_url,
    }


def serialize_decision(d: "PendingDecision") -> dict:
    return {
        "id": d.id, "kind": d.kind, "org_unit_name": d.org_unit_name,
        "failure_count": d.failure_count, "sample_urls": list(d.sample_urls),
        "suggested_action": d.suggested_action, "status": d.status, "action": d.action,
        "created_at": d.created_at.isoformat(),
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at is not None else None,
    }


def serialize_stats(stats: "QueueStats") -> dict:
    return {"pending": stats.pending, "assigned": stats.assigned, "completed": stats.completed,
            "failed": stats.failed, "skipped": stats.skipped}


# ---------- deserialization (script JSON → backend dataclass) ----------

def parse_pagination_state(d: dict) -> PaginationState | None:
    try:
        return PaginationState(
            kind=d["kind"], state_id=d["state_id"], label=d["label"],
            page_index=int(d["page_index"]), form_name=d["form_name"], fields=dict(d["fields"]),
            submit=bool(d["submit"]), synthetic_url=d["synthetic_url"], url=d["url"],
            total_pages=d.get("total_pages"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("dropping malformed pagination_state %r: %r", d, exc)
        return None


def parse_pagination_states(items: object) -> list[PaginationState]:
    if not isinstance(items, list):
        return []
    parsed = (parse_pagination_state(x) for x in items if isinstance(x, dict))
    return [s for s in parsed if s is not None]


# ---------- response / request helpers ----------

def json_response(data: object, *, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, ensure_ascii=False), status=status,
                        content_type="application/json", charset="utf-8")


async def _read_json(request: web.Request) -> dict:
    raw = await request.read()
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _bridge(request: web.Request) -> "HumanFetcherBridge":
    return request.app["bridge"]


def _decision_center(request: web.Request) -> "DecisionCenter":
    return request.app["decision_center"]


# ---------- handlers ----------

async def handle_next_job(request: web.Request) -> web.Response:
    job = _bridge(request).next_job()
    return web.Response(status=204) if job is None else json_response(serialize_job(job))


async def handle_complete(request: web.Request) -> web.Response:
    body = await _read_json(request)
    ok = _bridge(request).complete(
        request.match_info["id"], html=str(body.get("html", "")), final_url=str(body.get("url", "")),
        title=str(body.get("title", "")), pagination_states=parse_pagination_states(body.get("pagination_states")),
    )
    return json_response({"status": "ok" if ok else "ignored", "next_job": None})


async def handle_fail(request: web.Request) -> web.Response:
    body = await _read_json(request)
    ok = _bridge(request).fail(request.match_info["id"], str(body.get("message", "")))
    return json_response({"status": "ok" if ok else "ignored"})


async def handle_skip(request: web.Request) -> web.Response:
    ok = _bridge(request).skip(request.match_info["id"])
    return json_response({"status": "ok" if ok else "ignored"})


async def handle_override(request: web.Request) -> web.Response:
    body = await _read_json(request)
    new_url = str(body.get("new_url", "")).strip()
    if not new_url:
        return web.Response(status=204)
    job = _bridge(request).override(request.match_info["id"], new_url)
    return web.Response(status=204) if job is None else json_response(serialize_job(job))


async def handle_status(request: web.Request) -> web.Response:
    bridge, dc = _bridge(request), _decision_center(request)
    current, pending = bridge.current_job(), dc.current()
    return json_response({
        "queue": serialize_stats(bridge.stats()),
        "current_job": serialize_job(current) if current is not None else None,
        "pending_decision": serialize_decision(pending) if pending is not None else None,
        "agent": {},
        "server_uptime_seconds": time.monotonic() - request.app["start_time"],
    })


async def handle_decision(request: web.Request) -> web.Response:
    decision = _decision_center(request).current()
    return web.Response(status=204) if decision is None else json_response(serialize_decision(decision))


async def handle_resolve_decision(request: web.Request) -> web.Response:
    body = await _read_json(request)
    ok = await _decision_center(request).resolve(request.match_info["id"], str(body.get("action", "")).strip())
    return json_response({"status": "ok" if ok else "ignored"})


# ---------- app wiring ----------

def create_app(bridge: "HumanFetcherBridge", decision_center: "DecisionCenter") -> web.Application:
    app = web.Application()
    app["bridge"] = bridge
    app["decision_center"] = decision_center
    app["start_time"] = time.monotonic()
    app.add_routes([
        web.get(f"{API_PREFIX}/jobs/next", handle_next_job),
        web.post(f"{API_PREFIX}/jobs/{{id}}/complete", handle_complete),
        web.post(f"{API_PREFIX}/jobs/{{id}}/fail", handle_fail),
        web.post(f"{API_PREFIX}/jobs/{{id}}/skip", handle_skip),
        web.post(f"{API_PREFIX}/jobs/{{id}}/override", handle_override),
        web.get(f"{API_PREFIX}/status", handle_status),
        web.get(f"{API_PREFIX}/decision", handle_decision),
        web.post(f"{API_PREFIX}/decision/{{id}}/resolve", handle_resolve_decision),
    ])
    return app


async def run_server(app: web.Application, host: str, port: int) -> web.AppRunner:
    """Start the server in the running loop and return the runner for SP7 lifecycle
    management (await runner.cleanup() to stop)."""
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    logger.info("dext fetch bridge listening on http://%s:%d%s", host, port, API_PREFIX)
    return runner
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_bridge_server.py -v` → PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(sp4): add aiohttp server implementing the fixed userscript contract"`

---

### Task 9: Public interface re-exports + import/purity test

**Files:**
- Modify: `src/dext/bridge/__init__.py`
- Test: `tests/test_bridge_import.py`

- [ ] **Step 1: Extend the test**

```python
# tests/test_bridge_import.py
from pathlib import Path


def test_bridge_package_imports():
    import dext.bridge  # noqa: F401


def test_public_interface_is_reexported():
    import dext.bridge as b
    for name in ("HumanFetcherBridge", "FetchQueue", "FetchJob", "JobContext", "JobStatus",
                 "QueueStats", "DecisionCenter", "PendingDecision", "repair_mojibake_text",
                 "RedirectGuard", "RedirectVerdict", "classify_redirect", "create_app",
                 "run_server", "FetchResult"):
        assert hasattr(b, name), f"dext.bridge missing public export {name}"


def test_bridge_has_no_db_llm_or_page_imports():
    bridge_dir = Path(__file__).resolve().parent.parent / "src" / "dext" / "bridge"
    forbidden = ("sqlalchemy", "aiosqlite", "openai", "html2text", "beautifulsoup", "bs4",
                 "dext.storage", "dext.llm", "dext.page")
    for py in sorted(bridge_dir.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{py.name} must not reference {token}"
```

- [ ] **Step 2: Run** — `test_public_interface_is_reexported` FAILs (names not exported).

- [ ] **Step 3: Implement** — replace `src/dext/bridge/__init__.py`:

```python
"""dext fetch bridge — aiohttp server + in-memory job queue + HumanFetcherBridge.

Implements the fixed userscript HTTP contract (overview §4). No DB, no LLM, no HTML
parsing. Public interface (spec §10) re-exported below.
"""

from dext.bridge.decision import DecisionCenter, PendingDecision
from dext.bridge.fetcher import HumanFetcherBridge
from dext.bridge.mojibake import repair_mojibake_text
from dext.bridge.queue import FetchJob, FetchQueue, JobContext, JobStatus, QueueStats
from dext.bridge.redirect import RedirectGuard, RedirectVerdict, classify_redirect
from dext.bridge.server import create_app, run_server
from dext.types import FetchResult

__all__ = [
    "HumanFetcherBridge",
    "FetchQueue",
    "FetchJob",
    "JobContext",
    "JobStatus",
    "QueueStats",
    "DecisionCenter",
    "PendingDecision",
    "repair_mojibake_text",
    "RedirectGuard",
    "RedirectVerdict",
    "classify_redirect",
    "create_app",
    "run_server",
    "FetchResult",
]
```

- [ ] **Step 4: Run** — `uv run pytest tests/test_bridge_import.py -v` → PASS.
- [ ] **Step 5: Full suite** — `uv run pytest -q` → all green (SP1–SP4).
- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(sp4): re-export bridge public interface; SP4 complete"`

---

## Self-Review

**Spec coverage:** §1 goals → Tasks 5/8 (server+queue), §2 await model → Task 5 fetch(), §3 data structures → Tasks 3/5, §4 endpoints → Task 8 (all 8 routes), §5 HumanFetcherBridge → Task 5, §6 mojibake → Task 4, §7 redirect guard → Task 7, §9 decision channel → Task 6, §10 public interface → Task 9, §11 tests → every task's test file, §13 decisions → Tasks 5/7/8. `FetchResult` (overview §5) → Task 2. ✓

**No placeholders:** every step has full code/commands. ✓

**Type consistency:** `HumanFetcherBridge.fetch(*, url, context, identity_url=None, action=None)`; `complete(job_id, *, html, final_url, title, pagination_states=None)`; `JobContext` defaults; `JobStatus.value` serialized; `RedirectVerdict(verdict, final_url, final_host, reason)`; verdict constants `OK/BLOCKED/OFFSITE_OK/PROBE_FAILED` used in both impl and tests. ✓

**Out of scope (YAGNI):** no auth/HTTPS, no `next_job` pre-dispatch (always `null`), no job persistence, no httpx, no retry classification (SP6 owns it), `agent` field `{}`.
