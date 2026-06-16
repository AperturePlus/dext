# Async Decider (off the fetch loop) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the non-detail decider LLM round-trip off the single-threaded fetch loop so the browser fetches back-to-back while deciders run concurrently in the existing shared worker pool.

**Architecture:** The driver still owns the single in-flight `bridge.fetch`, `build_snapshot`, `save_page_cache`, and terminal-unavailable judging. After fetch it now *routes* the page to `extract_queue`: `detail_url → ExtractTask` (unchanged path), everything else → a new `DecideTask`. The shared `llm_worker` pool dispatches by task type — `DecideTask → handlers.dispatch(...)` (decide + build children + mark terminal), `ExtractTask → process_extract_task(...)`. `ExtractionTracker.in_flight` and `extract_queue` already gate DONE detection and cover both task kinds with no change.

**Tech Stack:** Python 3.11, asyncio, SQLAlchemy/aiosqlite (WAL), `uv run pytest` (`asyncio_mode="auto"`).

---

## Notes (read before starting)

- **Invariants preserved** (CLAUDE.md §non-negotiable, §bug traps): single in-flight fetch (driver still `await`s exactly one `bridge.fetch`), single-writer DB, DONE detection (`no claimable ∧ extract_queue.empty() ∧ in_flight==0`), single-round no-resteal (`_attempted_this_run`). No userscript/DB-schema/prompt/page/storage changes.

- **Import-cycle constraint (why the deferred import).** `dext.engine.handlers` imports `ExtractTask` from `dext.engine.workers` at module load. Therefore `workers.py` MUST NOT add a top-level `from dext.engine.handlers import dispatch` — that creates a hard import cycle (order-dependent `ImportError`). We import `dispatch` *inside* `llm_worker` (once, at worker start-up). This keeps the module-level import DAG acyclic (CLAUDE.md §conventions) with the smallest possible change. Do not "clean this up" into a top-level import.

- **DONE-race window stays closed.** In `llm_worker` there must be **no `await` between `queue.get()` and `tracker.in_flight += 1`**. Combined with handlers building *all* children and marking the node terminal *before* returning (i.e. before the `finally` decrements `in_flight`), the instant `in_flight` hits 0 the children are already persisted as `pending`, so the driver's next `claim_next` finds them instead of terminating.

- **Testing policy / deliberate decider fakes.** CLAUDE.md mandates *real live DeepSeek* for any test that exercises **LLM behavior**. The scheduling/concurrency tests here do **not** test LLM behavior — they test driver/worker *timing and routing* — so they swap `handlers.decide_links` for a deterministic fake. This is the project's established pattern (see `tests/test_engine_handlers.py`, which already does exactly `orig = handlers_mod.decide_links; handlers_mod.decide_links = _fake_decide`). The **one** test that exercises real extraction (Task 6) uses the live `LLMClient` and **skips without `DEEPSEEK_API_KEY`** via the `live_settings` fixture — never a fake. Keep this split; do not convert the live test to a fake or the scheduling tests to live calls.

- **Why the decoupling test is the RED driver.** Single-in-flight holds both before and after the change (the driver awaits fetch either way), so it can't drive the change — it's a regression guard. The decider *concurrency* assertion (`max_concurrent >= 2`) is `1` while deciders run inline and `>= 2` once they run in the pool; that is the failing test that forces Task 2's implementation.

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/dext/engine/workers.py` | Task DTOs + shared LLM worker loop | **Add** `DecideTask`; **rewrite** `llm_worker` to take `*, deps_factory=None`, route by task type, deferred-import `dispatch`. |
| `src/dext/engine/driver.py` | Single-threaded driver: claim → fetch → route-dispatch | `_handle_claimed_node` tail: replace inline `await dispatch(...)` with route-to-queue (`detail → ExtractTask`, else `DecideTask`). Add `_deps_factory`. `run()` injects `deps_factory`. Fix imports. |
| `src/dext/engine/handlers.py` | Node handlers (`dispatch`, `handle_*`) | **No change.** `dispatch` is now called by the worker; `handle_detail` stays (no longer reached via the new path — optional cleanup, NOT in scope). |
| `tests/test_engine_workers.py` | Worker-level unit tests | **Add** `test_llm_worker_routes_decide_task_to_dispatch`. |
| `tests/test_engine_driver.py` | Engine integration tests (fake bridge) | **Add** `CountingBridge` + `FakeDecider` helpers and 5 tests (decoupling, single-in-flight, DONE-convergence, retry-terminal, live e2e). |

---

## Task 1: `DecideTask` + worker type-routing (`workers.py`)

**Files:**
- Modify: `src/dext/engine/workers.py`
- Test: `tests/test_engine_workers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine_workers.py`:

```python
async def test_llm_worker_routes_decide_task_to_dispatch(tmp_path):
    import dext.engine.handlers as handlers_mod
    from dext.engine.handlers import HandlerDeps
    from dext.engine.workers import DecideTask, ExtractionTracker, llm_worker
    from dext.storage.models import OrgUnit
    from dext.storage.writer import ClaimedNode

    h = await _storage(tmp_path)
    listing_id = await h.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url="https://x.edu.cn/schools.htm",
                  settings=_settings(), run_id=1)
    )
    snap = build_snapshot("<html><body>院系设置</body></html>",
                          "https://x.edu.cn/schools.htm", "https://x.edu.cn/schools.htm", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[SimpleNamespace(
            url="https://x.edu.cn/math.htm", label="college", confidence=0.9,
            is_leaf=False, org_unit_name="数学学院", exclusion_reason=None)])

    node = ClaimedNode(id=listing_id, node_key="listing", type=NodeType.org_listing_url,
                       url=snap.url, org_unit_id=None, org_unit_name=None, depth=0,
                       attempt_count=1, priority_score=100, content_hash=None, metadata=None)
    q = asyncio.Queue()
    tracker = ExtractionTracker()

    def deps_factory(task):
        return HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=q,
                           reported_pagination_states=task.reported_pagination_states,
                           raw_html=task.raw_html, decision_center=None)

    await q.put(DecideTask(node=node, snapshot=snap, raw_html="<html></html>",
                           reported_pagination_states=[]))
    await q.put(None)
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        await llm_worker("w", q, h, None, _settings(), tracker, deps_factory=deps_factory)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        listing = (await s.execute(select(GraphNode).where(GraphNode.id == listing_id))).scalar_one()
        org_node = (await s.execute(select(GraphNode).where(GraphNode.type == NodeType.org_unit))).scalar_one()
        org = (await s.execute(select(OrgUnit))).scalar_one()
        assert listing.status == NodeStatus.done
        assert org_node.org_unit_name == "数学学院"
        assert org.name == "数学学院"
    assert tracker.in_flight == 0
    await _close(h)
```

- [ ] **Step 2: Run it RED**

Run: `uv run pytest tests/test_engine_workers.py::test_llm_worker_routes_decide_task_to_dispatch -v`
Expected: FAIL at collection — `ImportError: cannot import name 'DecideTask' from 'dext.engine.workers'`.

- [ ] **Step 3: Implement `DecideTask` + routing**

In `src/dext/engine/workers.py`, add the `ClaimedNode` import near the other storage import (top of file, after `from dext.storage.models import NodeStatus`):

```python
from dext.storage.writer import ClaimedNode
```

Add `DecideTask` immediately after the `ExtractTask` dataclass (right before `class ExtractionTracker`):

```python
@dataclass
class DecideTask:
    node: ClaimedNode
    snapshot: PageSnapshot
    raw_html: str
    reported_pagination_states: list
```

Replace the existing `llm_worker` function body entirely with:

```python
async def llm_worker(
    name: str,
    queue: asyncio.Queue,
    storage,
    llm_client,
    settings,
    tracker: ExtractionTracker,
    *,
    deps_factory=None,
) -> None:
    # Deferred import: handlers imports ExtractTask from this module, so a top-level
    # `from dext.engine.handlers import dispatch` would create an import cycle.
    from dext.engine.handlers import dispatch

    while True:
        task = await queue.get()
        if task is None:
            queue.task_done()
            return
        tracker.in_flight += 1  # no await between get() and increment — closes the DONE-race window
        try:
            if isinstance(task, DecideTask):
                await dispatch(task.node, task.snapshot, deps_factory(task))
            else:
                await process_extract_task(task, storage, llm_client, settings)
        finally:
            tracker.in_flight -= 1
            queue.task_done()
```

- [ ] **Step 4: Run it GREEN**

Run: `uv run pytest tests/test_engine_workers.py -v`
Expected: PASS (new test + the 3 existing worker tests).

- [ ] **Step 5: Commit**

```bash
git add src/dext/engine/workers.py tests/test_engine_workers.py
git commit -m "feat(engine): add DecideTask and type-routing to llm_worker"
```

---

## Task 2: Driver route-dispatch + `deps_factory` (`driver.py`)

**Files:**
- Modify: `src/dext/engine/driver.py`
- Test: `tests/test_engine_driver.py`

- [ ] **Step 1: Add shared test helpers**

At the top of `tests/test_engine_driver.py`, after the existing imports, add the `FetchResult` import and the two helpers (used by Tasks 2–6):

```python
from dext.types import FetchResult


class CountingBridge:
    """Fake bridge: serves canned HTML by identity_url, asserts single in-flight fetch."""

    def __init__(self, pages, *, fetch_delay=0.0):
        self.pages = pages
        self.fetch_delay = fetch_delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.fetch_count = 0

    async def fetch(self, *, url, identity_url, action, context):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            assert self.in_flight == 1, f"single in-flight violated: {self.in_flight}"
            if self.fetch_delay:
                await asyncio.sleep(self.fetch_delay)
            self.fetch_count += 1
            html = self.pages.get(identity_url, "<html><body></body></html>")
            return FetchResult(
                identity_url=identity_url, requested_url=url, final_url=identity_url,
                status_code=200, html=html, title="", pagination_states=[], block_reason=None,
            )
        finally:
            self.in_flight -= 1


class FakeDecider:
    """Swapped in for handlers.decide_links. The parent page yields N pagination links;
    pagination pages are leaves. Optional delays expose decider concurrency / DONE timing."""

    def __init__(self, parent_url, pagination_urls, *, child_delay=0.0, parent_delay=0.0):
        self.parent_url = parent_url
        self.pagination_urls = pagination_urls
        self.child_delay = child_delay
        self.parent_delay = parent_delay
        self.now = 0
        self.max_concurrent = 0

    async def __call__(self, snapshot, candidates, node, context, *, client):
        if snapshot.url == self.parent_url:
            if self.parent_delay:
                await asyncio.sleep(self.parent_delay)
            return SimpleNamespace(
                links=[SimpleNamespace(url=u, label="pagination", confidence=0.9,
                                       is_leaf=False, exclusion_reason=None)
                       for u in self.pagination_urls],
                parse_error=None,
            )
        self.now += 1
        self.max_concurrent = max(self.max_concurrent, self.now)
        try:
            if self.child_delay:
                await asyncio.sleep(self.child_delay)
            return SimpleNamespace(links=[], parse_error=None)
        finally:
            self.now -= 1
```

- [ ] **Step 2: Write the failing decoupling test**

Append to `tests/test_engine_driver.py`:

```python
async def test_decider_runs_concurrently_off_the_fetch_loop(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=4)
    parent = "https://x.edu.cn/szdw.htm"
    pagination_urls = [
        "https://x.edu.cn/szdw/2.htm",
        "https://x.edu.cn/szdw/3.htm",
        "https://x.edu.cn/szdw/4.htm",
    ]
    pages = {parent: "<html><body>师资</body></html>"}
    for u in pagination_urls:
        pages[u] = "<html><body>该页没有更多</body></html>"
    bridge = CountingBridge(pages)
    decider = FakeDecider(parent, pagination_urls, child_delay=0.05)

    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=parent, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = decider
    try:
        engine = CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                             run_id=1, university_name="测试大学")
        summary = await engine.run()
    finally:
        handlers_mod.decide_links = orig

    assert decider.max_concurrent >= 2     # deciders overlapped → off the fetch loop
    assert bridge.max_in_flight == 1       # still single in-flight fetch
    assert bridge.fetch_count == 4         # parent + 3 pagination pages all fetched
    assert summary.status == "completed"
    async with storage.session() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        assert len(nodes) == 4
        assert all(n.status == NodeStatus.done for n in nodes)
    await _close(storage)
```

- [ ] **Step 3: Run it RED**

Run: `uv run pytest tests/test_engine_driver.py::test_decider_runs_concurrently_off_the_fetch_loop -v`
Expected: FAIL — `assert decider.max_concurrent >= 2` fails (inline dispatch yields `max_concurrent == 1`).

- [ ] **Step 4: Implement the driver route-dispatch**

In `src/dext/engine/driver.py`:

**(a)** Change the handlers import (line ~11) to drop `dispatch` (no longer called by the driver):

```python
from dext.engine.handlers import HandlerDeps, fetch_action_from_metadata, identity_url_for
```

**(b)** Change the workers import (line ~13) to pull in the task DTOs:

```python
from dext.engine.workers import DecideTask, ExtractionTracker, ExtractTask, llm_worker
```

**(c)** In `run()`, inject `deps_factory` when constructing workers:

```python
        worker_tasks = [
            asyncio.create_task(
                llm_worker(
                    f"llm-{i}",
                    self.extract_queue,
                    self.storage,
                    self.llm_client,
                    self.settings,
                    self._tracker,
                    deps_factory=self._deps_factory,
                )
            )
            for i in range(max(1, self.settings.llm_workers))
        ]
```

**(d)** Replace the tail of `_handle_claimed_node` — the entire `await dispatch(...)` block plus the `self._summary.dispatched += 1` line (the old lines 180–195) — with route-to-queue:

```python
        if NodeType(node.type) == NodeType.detail_url:
            await self.extract_queue.put(
                ExtractTask(
                    node_id=node.id,
                    node_key=node.node_key,
                    snapshot=snapshot,
                    org_unit_id=node.org_unit_id,
                    org_unit_name=node.org_unit_name or "",
                    attempt_count=node.attempt_count,
                )
            )
        else:
            await self.extract_queue.put(
                DecideTask(
                    node=node,
                    snapshot=snapshot,
                    raw_html=result.html,
                    reported_pagination_states=result.pagination_states,
                )
            )
        self._summary.dispatched += 1
        # Return immediately → the driver loop claims the next pending node and keeps fetching.
```

**(e)** Add the `_deps_factory` method (place it directly after `_handle_claimed_node`, before `_save_failed_page_cache`):

```python
    def _deps_factory(self, task: DecideTask) -> HandlerDeps:
        return HandlerDeps(
            storage=self.storage,
            llm_client=self.llm_client,
            settings=self.settings,
            run_id=self.run_id,
            university_name=self.university_name,
            extract_queue=self.extract_queue,
            reported_pagination_states=task.reported_pagination_states,
            raw_html=task.raw_html,
            decision_center=self.decision_center,
        )
```

- [ ] **Step 5: Run it GREEN**

Run: `uv run pytest tests/test_engine_driver.py::test_decider_runs_concurrently_off_the_fetch_loop -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/dext/engine/driver.py tests/test_engine_driver.py
git commit -m "feat(engine): route decider off the fetch loop into the worker pool"
```

---

## Task 3: Single in-flight regression guard

**Files:**
- Test: `tests/test_engine_driver.py` (helpers from Task 2 reused)

- [ ] **Step 1: Write the test**

Append to `tests/test_engine_driver.py`:

```python
async def test_single_fetch_in_flight_across_sibling_nodes(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=4)
    parent = "https://x.edu.cn/szdw.htm"
    pagination_urls = [
        "https://x.edu.cn/szdw/2.htm",
        "https://x.edu.cn/szdw/3.htm",
        "https://x.edu.cn/szdw/4.htm",
    ]
    pages = {parent: "<html><body>师资</body></html>"}
    for u in pagination_urls:
        pages[u] = "<html><body>末页</body></html>"
    bridge = CountingBridge(pages, fetch_delay=0.01)
    decider = FakeDecider(parent, pagination_urls, child_delay=0.03)

    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=parent, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = decider
    try:
        summary = await CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                                    run_id=1, university_name="测试大学").run()
    finally:
        handlers_mod.decide_links = orig

    assert bridge.max_in_flight == 1
    assert bridge.fetch_count == 4
    assert summary.status == "completed"
    await _close(storage)
```

- [ ] **Step 2: Run it GREEN**

Run: `uv run pytest tests/test_engine_driver.py::test_single_fetch_in_flight_across_sibling_nodes -v`
Expected: PASS (regression guard — holds with the Task 2 implementation; `fetch_delay`+`child_delay` widen the window so any future concurrent-fetch regression trips `max_in_flight`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_driver.py
git commit -m "test(engine): guard single in-flight fetch under async decider"
```

---

## Task 4: DONE convergence — no early termination while a decider is in flight

**Files:**
- Test: `tests/test_engine_driver.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_engine_driver.py`. The parent decider is slow (`parent_delay`), so after fetching the parent the driver loops with `claim_next() is None` (children don't exist yet) while the parent `DecideTask` is in flight — it must NOT terminate. The children appear only after the slow decider runs:

```python
async def test_engine_does_not_terminate_while_decider_in_flight(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings(llm_workers=2)
    parent = "https://x.edu.cn/szdw.htm"
    pagination_urls = [
        "https://x.edu.cn/szdw/2.htm",
        "https://x.edu.cn/szdw/3.htm",
        "https://x.edu.cn/szdw/4.htm",
    ]
    pages = {parent: "<html><body>师资</body></html>"}
    for u in pagination_urls:
        pages[u] = "<html><body>末页</body></html>"
    bridge = CountingBridge(pages)
    decider = FakeDecider(parent, pagination_urls, parent_delay=0.05)

    await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=parent, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = decider
    try:
        summary = await CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                                    run_id=1, university_name="测试大学").run()
    finally:
        handlers_mod.decide_links = orig

    assert summary.status == "completed"
    assert bridge.fetch_count == 4
    async with storage.session() as s:
        pag = (await s.execute(
            select(GraphNode).where(GraphNode.type == NodeType.pagination_url)
        )).scalars().all()
        assert len(pag) == 3                                  # children materialized post-decide
        assert all(n.status == NodeStatus.done for n in pag)  # and fully processed
    await _close(storage)
```

- [ ] **Step 2: Run it GREEN**

Run: `uv run pytest tests/test_engine_driver.py::test_engine_does_not_terminate_while_decider_in_flight -v`
Expected: PASS. (If the DONE check ever regressed to terminate while `in_flight>0`/queue non-empty, the 3 children would never be created and this fails.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_driver.py
git commit -m "test(engine): assert no early termination while a decider is in flight"
```

---

## Task 5: Retry terminal state classified inside the worker

**Files:**
- Test: `tests/test_engine_driver.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_engine_driver.py`. A `parse_error=True` decision makes `_materialize_decided` raise `RuntimeError("decider_invalid_json")`, which `handle_faculty_page` catches and maps to `retry` — now executed in the worker, not the driver:

```python
async def test_decider_parse_error_marks_node_retry_via_worker(tmp_path):
    storage = await _storage(tmp_path)
    settings = _settings()
    url = "https://x.edu.cn/szdw.htm"
    bridge = CountingBridge({url: "<html><body>师资</body></html>"})

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(links=[], parse_error=True)

    node_id = await storage.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url=url, settings=settings, run_id=1)
    )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        summary = await CrawlEngine(storage, bridge, llm_client=None, settings=settings,
                                    run_id=1, university_name="测试大学").run()
    finally:
        handlers_mod.decide_links = orig

    async with storage.session() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
        assert node.status == NodeStatus.retry
        assert node.last_error == "decider_invalid_json"
    assert summary.status == "failed"
    await _close(storage)
```

- [ ] **Step 2: Run it GREEN**

Run: `uv run pytest tests/test_engine_driver.py::test_decider_parse_error_marks_node_retry_via_worker -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_driver.py
git commit -m "test(engine): retry classification holds through the async decider path"
```

---

## Task 6: Live end-to-end small graph (real DeepSeek; skips without key)

**Files:**
- Test: `tests/test_engine_driver.py`

- [ ] **Step 1: Write the test**

Append to `tests/test_engine_driver.py`. Fake bridge serves hand-crafted, unambiguous pages; the **real** decider + extractor drive the LLM calls. The `live_settings` fixture (from `tests/conftest.py`) skips the test when `DEEPSEEK_API_KEY` is absent — never a fake:

```python
async def test_async_decider_end_to_end_live(tmp_path, live_settings):
    from dext.llm.client import LLMClient

    storage = await _storage(tmp_path)
    settings = live_settings
    listing = "https://x.edu.cn/yx.htm"
    org = "https://x.edu.cn/math/"
    flist = "https://x.edu.cn/math/szdw.htm"
    d1 = "https://x.edu.cn/math/t/zhang.htm"
    d2 = "https://x.edu.cn/math/t/li.htm"
    pages = {
        listing: '<html><body><h2>院系设置</h2>'
                 '<a href="https://x.edu.cn/math/">数学学院</a></body></html>',
        org: '<html><body><h2>数学学院</h2>'
             '<a href="https://x.edu.cn/math/szdw.htm">师资队伍</a></body></html>',
        flist: '<html><body><h2>师资队伍</h2>'
               '<a href="https://x.edu.cn/math/t/zhang.htm">张三 教授</a>'
               '<a href="https://x.edu.cn/math/t/li.htm">李四 副教授</a></body></html>',
        d1: '<html><body><h1>张三</h1><p>职称：教授</p>'
            '<p>邮箱：zhang@x.edu.cn</p><p>研究方向：代数几何</p></body></html>',
        d2: '<html><body><h1>李四</h1><p>职称：副教授</p>'
            '<p>邮箱：li@x.edu.cn</p><p>研究方向：拓扑学</p></body></html>',
    }
    bridge = CountingBridge(pages)

    await storage.writer.upsert_node(
        node_spec(NodeType.org_listing_url, url=listing, settings=settings, run_id=1)
    )
    engine = CrawlEngine(storage, bridge, llm_client=LLMClient(settings), settings=settings,
                         run_id=1, university_name="测试大学")
    summary = await engine.run()

    assert bridge.max_in_flight == 1
    async with storage.session() as s:
        profs = (await s.execute(select(Professor))).scalars().all()
        affils = (await s.execute(select(ProfessorAffiliation))).scalars().all()
        assert len(profs) >= 1
        assert len(affils) >= 1
    await _close(storage)
```

- [ ] **Step 2: Run it (GREEN, or SKIP without key)**

Run: `uv run pytest tests/test_engine_driver.py::test_async_decider_end_to_end_live -v`
Expected: PASS with a valid `DEEPSEEK_API_KEY`; otherwise SKIPPED (`DEEPSEEK_API_KEY not set ...`). Per CLAUDE.md, validate the key before relying on this; do not downgrade to a fake.

- [ ] **Step 3: Commit**

```bash
git add tests/test_engine_driver.py
git commit -m "test(engine): live end-to-end converges through the async decider path"
```

---

## Final verification

- [ ] **Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (Task 6 may report `s`/skipped if no key). No regressions in `tests/test_engine_handlers.py`, `tests/test_engine_workers.py`, or the existing `tests/test_engine_driver.py` cases.

---

## Self-Review

**Spec coverage (§ → task):**
- §3 `DecideTask` DTO + `llm_worker` type-routing + deferred-import `dispatch` + no-await-before-`in_flight++` → **Task 1**.
- §4 driver route-dispatch (`detail → ExtractTask`, else `DecideTask`), `deps_factory` injection, single in-flight fetch unchanged → **Task 2**.
- §5 DONE detection reused unchanged → exercised by **Task 4** (and the convergence asserts in 2/3).
- §7 public-interface deltas (workers `+DecideTask` `+deps_factory`; driver route-dispatch; handlers unchanged) → Tasks 1–2; handlers intentionally untouched.
- §8 tests: single in-flight → **Task 3**; decoupling/解耦 → **Task 2**; DONE convergence → **Task 4**; retry terminal → **Task 5**; live e2e small graph → **Task 6**.
- §6 budget soft-overshoot & §9 not-doing items are explicitly out of scope (no code), consistent with this plan.

**Placeholder scan:** none — every code step is complete and runnable.

**Type/name consistency:** `DecideTask(node, snapshot, raw_html, reported_pagination_states)` is constructed identically in `driver._handle_claimed_node` and consumed in `driver._deps_factory` and `workers.llm_worker`; `deps_factory` is defined as `CrawlEngine._deps_factory` and passed as `deps_factory=self._deps_factory`; `CountingBridge.fetch(*, url, identity_url, action, context)` matches `driver.py`'s call site; `FakeDecider.__call__(snapshot, candidates, node, context, *, client)` matches `handlers._decide`'s call into `decide_links`; decision objects expose `.links`/`.parse_error` and links expose `.label/.url/.confidence/.is_leaf/.exclusion_reason`, matching `handlers._materialize_decided`/`_infer_query_filter_reslice` access.
