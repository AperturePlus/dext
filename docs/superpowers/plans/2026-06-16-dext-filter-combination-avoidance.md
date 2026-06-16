# Filter-Combination Avoidance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the decider from turning title/letter/advisor re-slice facets into a crawlable 26×N×M cartesian tree, collapsing per-faculty-subtree traversal to ~M (organizational sub-units) without losing real teachers.

**Architecture:** Split the decider's `followup` concept into `reslice` (same-population attribute filter → collapse) vs `followup` (disjoint sub-collection → traverse). The handler drops `reslice` links when a page already yields people, else descends exactly one axis; a per-`org_unit` facet-node budget is a deterministic backstop. Pure orchestration change — no DB schema change, no userscript contract change. Spec: `docs/superpowers/specs/2026-06-16-dext-filter-combination-avoidance-design.md`.

**Tech Stack:** Python 3.11, `uv run pytest`, SQLAlchemy async + aiosqlite (WAL), DeepSeek live LLM (decider). `asyncio_mode="auto"` (async tests need no marker).

---

## File Structure

- **Modify** `src/dext/config.py` — add `facet_node_budget: int = 150`.
- **Modify** `src/dext/llm/decider.py` — `_LABELS` += `"reslice"`; `DecidedLink` += `facet_axis`; parse + validate `facet_axis`.
- **Modify** `src/dext/llm/prompts/decider.md` — replace facet rules (reslice vs followup + `facet_axis` + real examples); remove "emit every facet".
- **Modify** `src/dext/storage/writer.py` — add `count_subtree_facet_nodes()` read command (+ `func` import).
- **Modify** `src/dext/engine/handlers.py` — `RESLICE_AXIS_PRIORITY` const; reslice handling + `pagination_created`/`over_budget` in `_materialize_decided`; `_materialize_reslices`; `_choose_reslice_axis`; `_over_facet_budget`; `HandlerDeps.decision_center`.
- **Modify** `src/dext/engine/driver.py` — pass `decision_center=self.decision_center` into `HandlerDeps`.
- **Tests** `tests/test_config.py`, `tests/test_llm_decider.py`, `tests/test_storage_writer.py`, `tests/test_engine_handlers.py`, `tests/test_llm_decider_live.py`.

Each task is independent and ends green + committed. Run all tests with `uv run pytest -q`.

---

### Task 1: Config — facet budget setting

**Files:**
- Modify: `src/dext/config.py:45-49`
- Test: `tests/test_config.py:19-38`

- [ ] **Step 1: Add the failing assertion**

In `tests/test_config.py`, inside `test_defaults_are_sane`, add after the `followup_page_limit` assertion (line 36):

```python
    assert s.followup_page_limit == 36
    assert s.facet_node_budget == 150
    assert s.attempt_penalty == 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_defaults_are_sane -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'facet_node_budget'`.

- [ ] **Step 3: Add the setting**

In `src/dext/config.py`, in the `# Scheduling / retry` block, add the field:

```python
    # Scheduling / retry
    max_depth: int = 4
    max_attempts: int = 3
    followup_page_limit: int = 36
    facet_node_budget: int = 150  # per-org_unit facet/list/pagination node cap (deterministic anti-explosion backstop)
    attempt_penalty: float = 5.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all config tests).

- [ ] **Step 5: Commit**

```bash
git add src/dext/config.py tests/test_config.py
git commit -m "feat(engine): add facet_node_budget setting (anti-explosion backstop)"
```

---

### Task 2: Decider — `reslice` label + `facet_axis` field

**Files:**
- Modify: `src/dext/llm/decider.py:18` (`_LABELS`), `:36-43` (`DecidedLink`), `:55-91` (`_parse_decision`)
- Test: `tests/test_llm_decider.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_decider.py`:

```python
def test_parse_keeps_reslice_label_and_facet_axis():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "reslice", "confidence": 0.8, '
           '"is_leaf": false, "facet_axis": "title"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].label == "reslice"
    assert d.links[0].facet_axis == "title"


def test_parse_nulls_invalid_facet_axis():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "reslice", "confidence": 0.8, '
           '"is_leaf": false, "facet_axis": "bogus"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].facet_axis is None


def test_parse_ignores_facet_axis_when_label_not_reslice():
    cands = [_sig("https://x/a")]
    raw = ('{"links": [{"url": "https://x/a", "label": "detail", "confidence": 0.9, '
           '"is_leaf": true, "facet_axis": "title"}]}')
    d = _parse_decision(raw, cands)
    assert d.links[0].facet_axis is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_decider.py -k "reslice or facet_axis" -v`
Expected: FAIL — `reslice` coerced to `noise` (not in `_LABELS`) and/or `DecidedLink` has no `facet_axis`.

- [ ] **Step 3: Implement label + field + parse**

In `src/dext/llm/decider.py`, extend `_LABELS` (line 18):

```python
_LABELS = {"college", "faculty_list", "pagination", "followup", "reslice", "detail", "noise", "login"}
_RESLICE_AXES = {"title", "letter", "advisor"}
```

Add `facet_axis` to `DecidedLink` (after `exclusion_reason`):

```python
@dataclass
class DecidedLink:
    url: str
    label: str
    confidence: float
    is_leaf: bool
    org_unit_name: str | None = None
    exclusion_reason: str | None = None
    facet_axis: str | None = None
```

In `_parse_decision`, inside the `for item in (data.get("links") or [])` loop, after computing `label` (the block at lines 68-69) and before building `DecidedLink`, derive `facet_axis`:

```python
        label = item.get("label")
        if label not in _LABELS:
            label = "noise"
        raw_axis = item.get("facet_axis")
        facet_axis = raw_axis if (label == "reslice" and raw_axis in _RESLICE_AXES) else None
```

Then pass it into the appended `DecidedLink(...)`:

```python
        links.append(
            DecidedLink(
                url=url,
                label=label,
                confidence=conf,
                is_leaf=bool(item.get("is_leaf", False)),
                org_unit_name=item.get("org_unit_name"),
                exclusion_reason=raw_reason if is_valid_exclusion_reason(raw_reason) else None,
                facet_axis=facet_axis,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_llm_decider.py -v`
Expected: PASS (new + all existing parse tests, including `test_parse_keeps_only_candidate_urls` which compares a full `DecidedLink` — `facet_axis` defaults `None` so equality holds).

- [ ] **Step 5: Commit**

```bash
git add src/dext/llm/decider.py tests/test_llm_decider.py
git commit -m "feat(llm): add decider reslice label and facet_axis field"
```

---

### Task 3: Handler — collapse re-slice facets (drop when people present, else one axis)

**Files:**
- Modify: `src/dext/engine/handlers.py` — add imports + `RESLICE_AXIS_PRIORITY`; rewrite `_materialize_decided` (`:331-383`); add `_materialize_reslices`, `_choose_reslice_axis`; edit `handle_faculty_page` (`:406-407`)
- Test: `tests/test_engine_handlers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_engine_handlers.py`:

```python
async def test_faculty_page_drops_reslice_when_people_present(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="哲学系", url="https://x.edu.cn/phil/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/phil/index.html",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="哲学系")
    )
    html = """
    <html><body>
      <a href="/phil/jiaoshou/index.html">教授</a>
      <a href="/phil/fujiaoshou/index.html">副教授</a>
      <a href="/phil/teacher/1.html">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/phil/index.html", "https://x.edu.cn/phil/index.html", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/phil/jiaoshou/index.html", label="reslice",
                                confidence=0.9, is_leaf=False, facet_axis="title"),
                SimpleNamespace(url="https://x.edu.cn/phil/fujiaoshou/index.html", label="reslice",
                                confidence=0.9, is_leaf=False, facet_axis="title"),
                SimpleNamespace(url="https://x.edu.cn/phil/teacher/1.html", label="detail",
                                confidence=0.9, is_leaf=True),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="哲学系", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        details = [n for n in nodes if n.type == NodeType.detail_url]
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        parent = next(n for n in nodes if n.id == node_id)
        assert [n.url for n in details] == ["https://x.edu.cn/phil/teacher/1.html"]
        assert followups == []  # both title re-slices collapsed
        assert parent.status == NodeStatus.done
    await _close(h)


async def test_faculty_page_takes_one_axis_when_no_people(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="某系", url="https://x.edu.cn/u/"))
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/u/people.html",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="某系")
    )
    # No detail/pagination/followup links; only two reslice axes (letter + title).
    html = """
    <html><body>
      <a href="/u/letter_a.html">A</a>
      <a href="/u/letter_b.html">B</a>
      <a href="/u/prof.html">教授</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/u/people.html", "https://x.edu.cn/u/people.html", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/u/letter_a.html", label="reslice",
                                confidence=0.8, is_leaf=False, facet_axis="letter"),
                SimpleNamespace(url="https://x.edu.cn/u/letter_b.html", label="reslice",
                                confidence=0.8, is_leaf=False, facet_axis="letter"),
                SimpleNamespace(url="https://x.edu.cn/u/prof.html", label="reslice",
                                confidence=0.8, is_leaf=False, facet_axis="title"),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="某系", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings(), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        followups = [n for n in nodes if n.type == NodeType.faculty_followup_url]
        assert {n.url for n in followups} == {
            "https://x.edu.cn/u/letter_a.html", "https://x.edu.cn/u/letter_b.html",
        }  # letter axis chosen (priority over title); title dropped
        assert all((n.metadata_json or {}).get("facet_axis") == "letter" for n in followups)
    await _close(h)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine_handlers.py -k "reslice or one_axis" -v`
Expected: FAIL — current `_materialize_decided` ignores the `reslice` label, so no nodes are created and the drop-test's `followups == []` may pass spuriously while the one-axis test fails (`followups` empty). Both should be RED before implementation; the one-axis test fails on the empty set.

- [ ] **Step 3: Implement the reslice collapse**

In `src/dext/engine/handlers.py`, add to the imports near the top (after `from dataclasses import asdict`):

```python
from collections import Counter
```

Add a module constant after `logger = logging.getLogger(__name__)`:

```python
RESLICE_AXIS_PRIORITY = ("letter", "title", "advisor")
```

Replace the entire `_materialize_decided` function (currently `:331-383`) with:

```python
async def _materialize_decided(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    decision,
    filter_result,
    *,
    pagination_created: int = 0,
) -> int:
    count = 0
    if not _within_depth(node, deps.settings):
        return 0
    actionable = [link for link in decision.links if not _link_exclusion_reason(link)]
    has_detail = any(link.label == "detail" or link.is_leaf for link in actionable if link.label != "reslice")
    has_followup = any(link.label == "followup" for link in actionable)
    yields_people = has_detail or has_followup or pagination_created > 0
    reslice_links = [link for link in actionable if link.label == "reslice"]
    for link in actionable:
        if link.label == "reslice":
            continue  # handled in _materialize_reslices
        if link.label == "detail" or link.is_leaf:
            await _create_child(
                deps, node, NodeType.detail_url, url=link.url,
                edge_type=EdgeType.detail_candidate_of, confidence=link.confidence,
                metadata={"label": link.label},
            )
            count += 1
        elif link.label == "pagination":
            await _create_child(
                deps, node, NodeType.pagination_url, url=link.url,
                edge_type=EdgeType.pagination_of, confidence=link.confidence,
                metadata={"label": link.label, "pagination_kind": "decider"},
            )
            count += 1
        elif link.label == "followup":
            await _create_child(
                deps, node, NodeType.faculty_followup_url, url=link.url,
                edge_type=EdgeType.discovered_on_page, confidence=link.confidence,
                metadata={"label": link.label},
            )
            count += 1
    count += await _materialize_reslices(node, snapshot, deps, reslice_links, yields_people=yields_people)
    logger.info(
        "navigation filter for %s kept=%d dropped=%s selected=%d parse_error=%s",
        snapshot.url, len(filter_result.kept), filter_result.dropped, count, decision.parse_error,
    )
    if decision.parse_error:
        raise RuntimeError("decider_invalid_json")
    if filter_result.kept and count == 0 and not decision.links:
        raise RuntimeError("decider_no_navigation_links")
    return count


async def _materialize_reslices(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    reslice_links: list,
    *,
    yields_people: bool,
) -> int:
    if not reslice_links:
        return 0
    if yields_people:
        # Trust-broad: the same people are already reachable here → collapse all re-slices.
        for axis, n in Counter(getattr(link, "facet_axis", None) or "unknown" for link in reslice_links).items():
            logger.info("redundant_facet:%s dropped=%d url=%s", axis, n, snapshot.url)
        return 0
    # No people anywhere on this page → re-slices are the only way forward; descend ONE axis.
    chosen = _choose_reslice_axis(reslice_links)
    count = 0
    for link in reslice_links:
        if (getattr(link, "facet_axis", None) or "unknown") != chosen:
            continue
        await _create_child(
            deps, node, NodeType.faculty_followup_url, url=link.url,
            edge_type=EdgeType.discovered_on_page, confidence=getattr(link, "confidence", None),
            metadata={"label": "reslice", "reslice": True, "facet_axis": chosen},
        )
        count += 1
    for axis in {getattr(link, "facet_axis", None) or "unknown" for link in reslice_links} - {chosen}:
        logger.info("reslice_axis_skipped:%s url=%s", axis, snapshot.url)
    return count


def _choose_reslice_axis(reslice_links: list) -> str:
    present = {getattr(link, "facet_axis", None) or "unknown" for link in reslice_links}
    for axis in RESLICE_AXIS_PRIORITY:
        if axis in present:
            return axis
    return sorted(present)[0]
```

In `handle_faculty_page`, change the `_materialize_decided` call (currently line 407) to thread the pagination count:

```python
    try:
        created += await _materialize_decided(
            node, snapshot, deps, decision, filter_result, pagination_created=created
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine_handlers.py -v`
Expected: PASS — new reslice tests pass; all existing handler tests still pass (the `followup`/`detail`/`pagination` branches are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/dext/engine/handlers.py tests/test_engine_handlers.py
git commit -m "feat(engine): collapse reslice facets (trust-broad drop / one-axis fallback)"
```

---

### Task 4: Writer — count facet nodes in a subtree

**Files:**
- Modify: `src/dext/storage/writer.py:15` (import), add command method on `DBWriter` + module impl
- Test: `tests/test_storage_writer.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_storage_writer.py`:

```python
import asyncio
from sqlalchemy import select  # noqa: F401 (may already be imported at top)

from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import NodeType
from dext.storage.writer import DBWriter, NodeSpec, OrgUnitSpec


async def test_count_subtree_facet_nodes(tmp_path):
    eng = create_engine_for_path(tmp_path / "count.db")
    await create_all(eng)
    w = DBWriter(make_session_factory(eng))
    task = asyncio.create_task(w.run())
    try:
        org = await w.upsert_org_unit(OrgUnitSpec(name="某院", url="https://x.edu.cn/c/"))
        other = await w.upsert_org_unit(OrgUnitSpec(name="它院", url="https://x.edu.cn/o/"))
        for i, t in enumerate(
            [NodeType.faculty_list_url, NodeType.faculty_followup_url, NodeType.pagination_url, NodeType.detail_url]
        ):
            await w.upsert_node(NodeSpec(node_key=f"k{i}", type=t, url=f"https://x.edu.cn/c/{i}", org_unit_id=org))
        await w.upsert_node(NodeSpec(node_key="ko", type=NodeType.faculty_followup_url,
                                     url="https://x.edu.cn/o/1", org_unit_id=other))
        # org subtree: faculty_list + followup + pagination = 3 (detail_url NOT counted)
        assert await w.count_subtree_facet_nodes(org) == 3
        assert await w.count_subtree_facet_nodes(other) == 1
    finally:
        await w.stop()
        await task
        await eng.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_storage_writer.py::test_count_subtree_facet_nodes -v`
Expected: FAIL — `AttributeError: 'DBWriter' object has no attribute 'count_subtree_facet_nodes'`.

- [ ] **Step 3: Implement the read command**

In `src/dext/storage/writer.py`, change the sqlalchemy import (line 15) to add `func`:

```python
from sqlalchemy import func, or_, select
```

Add a method on `DBWriter` (place it after `claim_next`, around line 152):

```python
    async def count_subtree_facet_nodes(self, org_unit_id) -> int:
        return await self._run(lambda s: _count_subtree_facet_nodes(s, org_unit_id))
```

Add the module-level command implementation (place it after `_claim_next`, around line 315):

```python
async def _count_subtree_facet_nodes(session, org_unit_id) -> int:
    stmt = (
        select(func.count())
        .select_from(GraphNode)
        .where(
            GraphNode.org_unit_id == org_unit_id,
            GraphNode.type.in_(
                [NodeType.faculty_list_url, NodeType.faculty_followup_url, NodeType.pagination_url]
            ),
        )
    )
    return (await session.execute(stmt)).scalar_one()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_storage_writer.py -v`
Expected: PASS (new + existing writer tests).

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/writer.py tests/test_storage_writer.py
git commit -m "feat(storage): add count_subtree_facet_nodes read command"
```

---

### Task 5: Handler — per-subtree facet budget backstop

**Files:**
- Modify: `src/dext/engine/handlers.py` — import `PendingDecision`; `HandlerDeps.__init__` += `decision_center`; add `_over_facet_budget`, `_maybe_set_budget_decision`; thread `over_budget` through `handle_faculty_page` + `_materialize_decided`
- Modify: `src/dext/engine/driver.py:182-193` — pass `decision_center`
- Test: `tests/test_engine_handlers.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_engine_handlers.py`:

```python
from dext.bridge.decision import DecisionCenter


def _settings_budget(n):
    return SimpleNamespace(max_attempts=3, max_depth=4, followup_page_limit=36, facet_node_budget=n)


async def test_faculty_page_facet_budget_stops_expansion_and_sets_decision(tmp_path):
    h = await _storage(tmp_path)
    org_id = await h.writer.upsert_org_unit(OrgUnitSpec(name="爆炸院", url="https://x.edu.cn/boom/"))
    # Pre-seed 2 followup nodes; together with this faculty_list node that is 3 facet
    # nodes for the subtree, which is >= the budget of 2 → over budget.
    for i in range(2):
        await h.writer.upsert_node(
            node_spec(NodeType.faculty_followup_url, url=f"https://x.edu.cn/boom/seed{i}.html",
                      settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="爆炸院")
        )
    node_id = await h.writer.upsert_node(
        node_spec(NodeType.faculty_list_url, url="https://x.edu.cn/boom/index.html",
                  settings=_settings(), run_id=1, org_unit_id=org_id, org_unit_name="爆炸院")
    )
    html = """
    <html><body>
      <a href="/boom/more.html">系所</a>
      <a href="/boom/teacher/1.html">张三</a>
    </body></html>
    """
    snap = build_snapshot(html, "https://x.edu.cn/boom/index.html", "https://x.edu.cn/boom/index.html", "")

    async def _fake_decide(*args, **kwargs):
        return SimpleNamespace(
            links=[
                SimpleNamespace(url="https://x.edu.cn/boom/more.html", label="followup",
                                confidence=0.9, is_leaf=False),
                SimpleNamespace(url="https://x.edu.cn/boom/teacher/1.html", label="detail",
                                confidence=0.9, is_leaf=True),
            ],
            parse_error=None, page_exclusion_reason=None,
        )

    import dext.engine.handlers as handlers_mod
    orig = handlers_mod.decide_links
    handlers_mod.decide_links = _fake_decide
    center = DecisionCenter()
    try:
        node = ClaimedNode(id=node_id, node_key="f", type=NodeType.faculty_list_url, url=snap.url,
                           org_unit_id=org_id, org_unit_name="爆炸院", depth=2, attempt_count=1,
                           priority_score=80, content_hash=None, metadata=None)
        deps = HandlerDeps(storage=h, llm_client=None, settings=_settings_budget(2), run_id=1,
                           university_name="测试大学", extract_queue=asyncio.Queue(), raw_html=html,
                           decision_center=center)
        await handle_faculty_page(node, snap, deps)
    finally:
        handlers_mod.decide_links = orig

    async with h.session_factory() as s:
        nodes = (await s.execute(select(GraphNode))).scalars().all()
        # detail leaf still created (not budget-limited); no NEW followup beyond the 2 seeds.
        assert any(n.type == NodeType.detail_url and n.url.endswith("/teacher/1.html") for n in nodes)
        assert sum(1 for n in nodes if n.type == NodeType.faculty_followup_url) == 2
    assert center.current() is not None
    assert center.current().kind == "facet_budget"
    await _close(h)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_engine_handlers.py::test_faculty_page_facet_budget_stops_expansion_and_sets_decision -v`
Expected: FAIL — `HandlerDeps.__init__` rejects `decision_center`; and the budget is not enforced (a 3rd followup would be created).

- [ ] **Step 3: Implement the budget backstop**

In `src/dext/engine/handlers.py`, add the import (near the other `dext` imports):

```python
from dext.bridge.decision import PendingDecision
```

Add `decision_center` to `HandlerDeps.__init__` (signature + body):

```python
    def __init__(
        self,
        *,
        storage,
        llm_client,
        settings,
        run_id: int,
        university_name: str,
        extract_queue,
        reported_pagination_states: list[PaginationState] | None = None,
        raw_html: str = "",
        decision_center=None,
    ) -> None:
        self.storage = storage
        self.llm_client = llm_client
        self.settings = settings
        self.run_id = run_id
        self.university_name = university_name
        self.extract_queue = extract_queue
        self.reported_pagination_states = reported_pagination_states or []
        self.raw_html = raw_html
        self.decision_center = decision_center
```

Add two helpers (place them just above `handle_faculty_page`):

```python
async def _over_facet_budget(node: ClaimedNode, deps: HandlerDeps) -> bool:
    if node.org_unit_id is None:
        return False
    budget = getattr(deps.settings, "facet_node_budget", 0) or 0
    if budget <= 0:
        return False
    count = await deps.storage.writer.count_subtree_facet_nodes(node.org_unit_id)
    if count < budget:
        return False
    logger.info("facet_budget_exceeded org_unit=%s budget=%d count=%d", node.org_unit_id, budget, count)
    _maybe_set_budget_decision(node, deps, count)
    return True


def _maybe_set_budget_decision(node: ClaimedNode, deps: HandlerDeps, count: int) -> None:
    center = getattr(deps, "decision_center", None)
    if center is None or center.current() is not None:
        return
    center.set_decision(
        PendingDecision(
            id=f"facet_budget:{node.org_unit_id}",
            kind="facet_budget",
            org_unit_name=node.org_unit_name or "",
            failure_count=count,
            sample_urls=[],
            suggested_action="stop_subtree",
        )
    )
```

Edit `handle_faculty_page` to gate frontier expansion. Replace the body from the `created = 0` line through the `_materialize_decided` call with:

```python
    over_budget = await _over_facet_budget(node, deps)
    created = 0
    if not over_budget:
        detail_like_urls = _detail_like_urls(snapshot)
        created += await _create_url_pagination_nodes(node, snapshot, deps, exclude_urls=detail_like_urls)
        created += await _create_form_pagination_nodes(node, snapshot, deps)
    try:
        created += await _materialize_decided(
            node, snapshot, deps, decision, filter_result,
            pagination_created=created, over_budget=over_budget,
        )
    except RuntimeError as exc:
```

(Leave the existing `except RuntimeError as exc:` block and the final `mark_node(..., NodeStatus.done, ...)` unchanged.)

Update `_materialize_decided` signature and the two frontier-creating branches to respect `over_budget`. Change the signature line to:

```python
async def _materialize_decided(
    node: ClaimedNode,
    snapshot: PageSnapshot,
    deps: HandlerDeps,
    decision,
    filter_result,
    *,
    pagination_created: int = 0,
    over_budget: bool = False,
) -> int:
```

In its loop, guard the `pagination` and `followup` branches (detail is never budget-limited):

```python
        elif link.label == "pagination":
            if over_budget:
                continue
            await _create_child(
                deps, node, NodeType.pagination_url, url=link.url,
                edge_type=EdgeType.pagination_of, confidence=link.confidence,
                metadata={"label": link.label, "pagination_kind": "decider"},
            )
            count += 1
        elif link.label == "followup":
            if over_budget:
                continue
            await _create_child(
                deps, node, NodeType.faculty_followup_url, url=link.url,
                edge_type=EdgeType.discovered_on_page, confidence=link.confidence,
                metadata={"label": link.label},
            )
            count += 1
```

And pass `over_budget` to the reslice helper (re-slice fallback must not expand the frontier when over budget):

```python
    count += await _materialize_reslices(
        node, snapshot, deps, reslice_links, yields_people=(yields_people or over_budget)
    )
```

(Folding `over_budget` into `yields_people` makes `_materialize_reslices` drop all re-slices when over budget — no new code path needed there.)

- [ ] **Step 4: Wire the driver to pass `decision_center`**

In `src/dext/engine/driver.py`, in `_handle_claimed_node`, the `HandlerDeps(...)` construction (lines 182-192) — add the `decision_center` argument:

```python
        await dispatch(
            node,
            snapshot,
            HandlerDeps(
                storage=self.storage,
                llm_client=self.llm_client,
                settings=self.settings,
                run_id=self.run_id,
                university_name=self.university_name,
                extract_queue=self.extract_queue,
                reported_pagination_states=result.pagination_states,
                raw_html=result.html,
                decision_center=self.decision_center,
            ),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine_handlers.py tests/test_engine_driver.py -v`
Expected: PASS — budget test passes; existing handler tests (which build `HandlerDeps` without `decision_center` and use `_settings()` with no `facet_node_budget`, so `_over_facet_budget` returns `False`) still pass; driver tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/dext/engine/handlers.py src/dext/engine/driver.py tests/test_engine_handlers.py
git commit -m "feat(engine): per-subtree facet-node budget backstop with pending decision"
```

---

### Task 6: Decider prompt rewrite + live classification test

**Files:**
- Modify: `src/dext/llm/prompts/decider.md` (label list, 师资页导航规则, JSON example)
- Test: `tests/test_llm_decider_live.py` (module `_LABELS` + new live test)

- [ ] **Step 1: Write the failing live test**

In `tests/test_llm_decider_live.py`, update the module-level `_LABELS` (line 4) to include `reslice`:

```python
_LABELS = {"college", "faculty_list", "pagination", "followup", "reslice", "detail", "noise", "login"}
```

Append a new live test:

```python
async def test_decide_labels_title_reslice_and_keeps_dept_followup(llm_client):
    cands = [
        _sig("https://x.edu.cn/szdw/jiaoshou/index.html", "教授", ["szdw", "jiaoshou"]),
        _sig("https://x.edu.cn/szdw/fujiaoshou/index.html", "副教授", ["szdw", "fujiaoshou"]),
        _sig("https://x.edu.cn/szdw/zhexuexi/index.html", "哲学系", ["szdw", "zhexuexi"]),
        _sig("https://x.edu.cn/szdw/jianzhi/index.html", "兼职教授", ["szdw", "jianzhi"]),
        _sig("https://x.edu.cn/teacher/1001.htm", "张三 教授", ["teacher", "1001"]),
    ]
    snap = PageSnapshot(
        url="https://x.edu.cn/szdw.htm", final_url="https://x.edu.cn/szdw.htm",
        title="师资队伍",
        text_snapshot="师资队伍。按职称查看：教授、副教授。按系查看：哲学系。兼职教授。教师：张三 教授。",
        links=[c.url for c in cands], link_signals=cands, content_hash="h",
    )
    node = DeciderNode(type="faculty_list_url", url=snap.url, depth=1, org_unit_name="某学院")
    ctx = DeciderContext(university_name="X大学", visited_summary="", faculty_list_url=snap.url)

    decision = await decide_links(snap, cands, node, ctx, client=llm_client)
    assert decision.parse_error is None
    for link in decision.links:
        assert link.label in _LABELS
    by_url = {l.url: l for l in decision.links}

    jiaoshou = by_url.get("https://x.edu.cn/szdw/jiaoshou/index.html")
    if jiaoshou is not None:                        # 职称 → 同群体 re-slice
        assert jiaoshou.label == "reslice"
        assert jiaoshou.facet_axis == "title"
    dept = by_url.get("https://x.edu.cn/szdw/zhexuexi/index.html")
    if dept is not None:                            # 系 → 组织子单元，绝不是 reslice
        assert dept.label != "reslice"
    jianzhi = by_url.get("https://x.edu.cn/szdw/jianzhi/index.html")
    if jianzhi is not None:                         # 兼职 → 异群体，不塌缩为 reslice
        assert jianzhi.label != "reslice"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_decider_live.py::test_decide_labels_title_reslice_and_keeps_dept_followup -v`
Expected: FAIL (with a real `DEEPSEEK_API_KEY`) — the current prompt labels 职称 as `followup`, so `jiaoshou.label == "reslice"` fails. (Without a key the test SKIPs — that is not a pass; you must validate with a key per CLAUDE.md.)

- [ ] **Step 3: Rewrite the decider prompt**

In `src/dext/llm/prompts/decider.md`, add a label bullet after the `pagination` line (line 8):

```markdown
- `pagination`：翻页链接
- `reslice`：对**同一拨教师**按人人都有的属性再过滤的中间页（职称/姓氏字母/导师类别）；会被引擎塌缩以避免组合爆炸
- `followup`：需要进一步展开的中间页
```

Replace the `师资页导航规则` block (lines 14-19) with:

```markdown
师资页导航规则：
- **reslice（同群体再切片，将被塌缩）**：对**同一拨教师**按人人都有的属性再过滤的中间页 —— 职称（正高/副高/中级/教授/副教授/讲师）、姓氏字母（A–Z 或拼音首字母）、导师类别（博导/硕导）—— 标 `reslice` 并填 `facet_axis`（`title`/`letter`/`advisor`）。这些人已在宽表里，引擎据此塌缩，避免 26×N×M 组合爆炸。
- **followup（可能是另一拨人，照常展开）**：指向**组织子单元**（系/所/研究中心/学科组）或**不同群体类别**（兼职/外聘/特聘等，可能含宽表没有的人）的中间页标 `followup`。**拿不准 reslice 还是 followup 时标 `followup`**（宁可多遍历一页，不可静默漏人）。
- 列表页中指向单个教师姓名、个人简介、个人主页的链接标为 `detail`，`is_leaf=true`。
- 首页、上页、下页、尾页、页码等翻页链接标为 `pagination`，`is_leaf=false`。
- 新闻、通知、公告、搜索、登录、下载、联系我们、后台管理等非教师导航标为 `noise` 或 `login`。
- 同一页中同时存在 detail、翻页、followup、reslice 时分别输出；**务必输出 detail 与 followup**（reslice 仅作分类，引擎可能丢弃）。
- 判例：`jobType=教授`、`.../jiaoshou/index.html`（教授）、`.../fujiaoshou/index.html`（副教授）= `reslice` + `facet_axis=title`；A/B/C… 字母筛选 = `reslice` + `facet_axis=letter`；`兼职教授`、`产业教学教师` = `followup`（异群体，可能有宽表没有的人）。
```

Replace the JSON example line (line 33) to add `facet_axis`:

```markdown
{"links": [{"url": "<候选中的原样URL>", "label": "<上述之一>", "confidence": 0.0, "is_leaf": false, "org_unit_name": "<仅当 label=college 时填写学院规范名>", "exclusion_reason": "<命中轴B排除时填类别code，否则null>", "facet_axis": "<仅当 label=reslice 时填 title|letter|advisor，否则 null>"}], "page_is_leaf": false, "page_exclusion_reason": "<整页命中轴A排除时填类别code，否则null>"}
```

Add a bullet after the `is_leaf` explanation (after line 36):

```markdown
- `facet_axis` 仅在 `label=reslice` 时有意义，取 `title`|`letter`|`advisor`；其余情况留空（null）。
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_decider_live.py -v`
Expected: PASS with a real key (the new test + existing live decider tests). Re-run once more to confirm the LLM classification is stable; if `jiaoshou` is occasionally `followup`, strengthen the prompt judce wording (it must not be flaky on the obvious 职称 case).

- [ ] **Step 5: Verify the prompt edit didn't break prompt assertions**

Run: `uv run pytest tests/test_llm_prompts.py -v`
Expected: PASS unchanged. `test_llm_prompts.py` does **not** pin a literal decider hash (it only checks `prompt_hash` is deterministic/distinct/16-char), and it asserts `"is_leaf=false" in decider` plus the `{{EXCLUSION_POLICY}}`-injected terms — the rewrite keeps the `is_leaf=false` line and the `{{EXCLUSION_POLICY}}` placeholder, so no test edits are needed. (`prompt_hash("decider")` does change — that is the intended version bump recorded in `crawl_extraction_attempts.prompt_hash`.)

- [ ] **Step 6: Commit**

```bash
git add src/dext/llm/prompts/decider.md tests/test_llm_decider_live.py
git commit -m "feat(prompts): decider classifies reslice vs followup with facet_axis"
```

---

## Final Verification

- [ ] **Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (live LLM tests require `DEEPSEEK_API_KEY`; they SKIP without it — note any skips explicitly, do not report skips as passes).

- [ ] **Sanity-check against real data (optional, manual)**

The fix should make a re-crawl of an `lzu`-style dept page create detail nodes but no title sub-page nodes. There is no automated assertion against the live DB; the synthetic handler tests (Task 3) stand in for it.
