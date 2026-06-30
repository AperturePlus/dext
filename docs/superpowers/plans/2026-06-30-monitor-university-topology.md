# University ↔ 学院 Topology View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the monitor WebUI's truncated "Graph preview" panel (which renders the catalog graph as isolated points because its node-limit cut drops relationship endpoints) with a `UniversityTopologyChart` that shows the complete University→OrgUnit tree, filtered client-side by a selected university.

**Architecture:** A new read-only backend endpoint `GET /api/monitor/builds/{build_id}/graph-tree` returns the complete University→OrgUnit tree with professor counts (no truncation). The frontend fetches it alongside the existing per-build data, and a new `UniversityTopologyChart.vue` renders an ECharts force graph filtered by a `<select>` of universities. The old `GraphNetworkPreview.vue` is deleted; the `graph-preview` endpoint is left intact.

**Tech Stack:** Python 3.11 + aiohttp (`dext_monitor`), SQLite (`graph_export_rows`), Vue 3 + TypeScript + ECharts 5 (`webui`), vitest + `@vue/test-utils`. `uv` runner for Python, `npm` for webui.

## Global Constraints

- Read-only monitor: no writes; `dext_monitor` must not import `dext_graph` workflow code (enforced by `settings.py` docstring + existing convention).
- Full UTF-8 end to end: `json_response` already serializes with `ensure_ascii=False` — do not change it. University/OrgUnit names are Chinese; the DB stores proper UTF-8.
- ETag/304 reuse the existing `error_middleware` in `server.py` — no new caching code.
- KISS: one new endpoint, one new component, one new test file each. No server-side `?university=` filtering (client-side only).
- TDD always: write the failing test, run it RED, implement minimally, run GREEN, commit — one conventional commit per green step (`feat(monitor): …` / `feat(webui): …` / `test(monitor): …` / `test(webui): …`).
- Python runner: `uv run pytest …`. webui: `cd webui && npm test` / `npm run build`.
- Node-row `start_graph_key`/`end_graph_key` are **NULL** on `node:*` rows — the graph key lives in `payload_json.graph_key`. Only `rel:*` rows populate `start_graph_key`/`end_graph_key`. Every node `id` emitted by the new endpoint MUST be `payload_json.graph_key` so relationship-row endpoints resolve.
- `pytest` is `asyncio_mode="auto"` — async tests/fixtures need no marker (but the existing file uses `@pytest.mark.asyncio` on the aiohttp test; follow that locally for consistency).

---

## File Structure

**Backend (Python):**
- `src/dext_monitor/service.py` — **modify**: add `graph_tree(build_id)` method to `MonitorService`.
- `src/dext_monitor/server.py` — **modify**: add `handle_graph_tree` handler + route in `create_app`.
- `tests/test_monitor_service.py` — **modify**: extend `_write_catalog` fixture with a second university + OrgUnits + `AFFILIATED_WITH` rows; add `graph_tree` service test + aiohttp endpoint test.

**Frontend (TypeScript/Vue):**
- `webui/src/types/monitor.ts` — **modify**: add `UniversitySummary`, `UniversityTopologyNode`, `UniversityTopologyLink`, `UniversityTopologyResponse`.
- `webui/src/services/api.ts` — **modify**: add `universityTopology(buildId)` method.
- `webui/src/composables/useMonitorData.ts` — **modify**: add `topology` ref, fetch it in `loadBuild`, return it.
- `webui/src/App.vue` — **modify**: destructure `topology`, pass `:topology="topology"` to `DashboardPage`.
- `webui/src/pages/DashboardPage.vue` — **modify**: replace `GraphNetworkPreview` import/panel with `UniversityTopologyChart`; swap the `graph` prop for `topology`.
- `webui/src/components/charts/UniversityTopologyChart.vue` — **create**: the chart + university `<select>`.
- `webui/src/components/charts/GraphNetworkPreview.vue` — **delete** (only consumer is `DashboardPage`).
- `webui/tests/university-topology.test.ts` — **create**: vitest for the new chart.
- `webui/tests/useMonitorData.test.ts` — **modify**: add `universityTopology` mock to `apiMocks` + `mockActive`.

---

## Task 1: Backend `graph_tree` service method (TDD)

**Files:**
- Modify: `src/dext_monitor/service.py` (add method to `MonitorService`, before `_get_build`)
- Test: `tests/test_monitor_service.py` (extend `_write_catalog` + add service test)

**Interfaces:**
- Produces: `MonitorService.graph_tree(build_id: str) -> dict[str, Any]` returning
  `{"build_id", "universities", "nodes", "links"}` where:
  - `universities`: list of `{"graph_key", "name", "logical_id", "orgunit_count", "professor_count"}`
  - `nodes`: list of `{"id", "label", "category", "professor_count", "orgunit_count"?, "kind"?, "university"?}`
    (`orgunit_count` only on University nodes; `kind`/`university` only on OrgUnit nodes)
  - `links`: list of `{"source", "target", "label"}` with `label="PART_OF"`

This task extends the test fixture so Task 2's endpoint test has rich data to assert against.

- [ ] **Step 1: Extend the `_write_catalog` fixture with a second university**

In `tests/test_monitor_service.py`, find the `rows = [...]` list (around line 236) that inserts `graph_export_rows`. The current list is:

```python
rows = [
    ("node:Build", "build", "node", "Build", None, None, '{"id":"build-1","graph_key":"build-1"}'),
    ("node:University", "u", "node", "University", None, None, '{"graph_key":"u","name":"大学"}'),
    ("node:OrgUnit", "org", "node", "OrgUnit", None, None, '{"graph_key":"org","name":"学院"}'),
    ("rel:PART_OF", "org|u", "relationship", "PART_OF", "org", "u", '{"graph_key":"r1"}'),
    ("rel:FROM_UNIVERSITY", "x|u", "relationship", "FROM_UNIVERSITY", "x", "u", '{"graph_key":"r2"}'),
]
```

Replace it with a richer set that adds a second university (`u2`), a second OrgUnit (`org2`) under it, and `AFFILIATED_WITH` rows so professor counts are non-zero:

```python
rows = [
    ("node:Build", "build", "node", "Build", None, None, '{"id":"build-1","graph_key":"build-1"}'),
    ("node:University", "u", "node", "University", None, None, '{"graph_key":"u","name":"大学A","logical_id":"univ:a"}'),
    ("node:University", "u2", "node", "University", None, None, '{"graph_key":"u2","name":"大学B","logical_id":"univ:b"}'),
    ("node:OrgUnit", "org", "node", "OrgUnit", None, None, '{"graph_key":"org","name":"学院A1","kind":"college"}'),
    ("node:OrgUnit", "org2", "node", "OrgUnit", None, None, '{"graph_key":"org2","name","研究所B1","kind":"institute"}'),
    ("rel:PART_OF", "org|u", "relationship", "PART_OF", "org", "u", '{"graph_key":"r1"}'),
    ("rel:PART_OF", "org2|u2", "relationship", "PART_OF", "org2", "u2", '{"graph_key":"r1b"}'),
    ("rel:AFFILIATED_WITH", "p1|org", "relationship", "AFFILIATED_WITH", "p1", "org", '{"graph_key":"a1"}'),
    ("rel:AFFILIATED_WITH", "p2|org", "relationship", "AFFILIATED_WITH", "p2", "org", '{"graph_key":"a2"}'),
    ("rel:AFFILIATED_WITH", "p3|org2", "relationship", "AFFILIATED_WITH", "p3", "org2", '{"graph_key":"a3"}'),
    ("rel:FROM_UNIVERSITY", "x|u", "relationship", "FROM_UNIVERSITY", "x", "u", '{"graph_key":"r2"}'),
]
```

Then update the `graph_export_partitions` insert loop (around line 248) to cover the new partitions. Replace its list:

```python
for partition, kind, label, count in [
    ("node:Build", "node", "Build", 1),
    ("node:University", "node", "University", 2),
    ("node:OrgUnit", "node", "OrgUnit", 2),
    ("rel:PART_OF", "relationship", "PART_OF", 2),
    ("rel:AFFILIATED_WITH", "relationship", "AFFILIATED_WITH", 3),
    ("rel:FROM_UNIVERSITY", "relationship", "FROM_UNIVERSITY", 1),
]:
```

Run the existing suite to confirm the fixture change didn't break existing tests:

`uv run pytest tests/test_monitor_service.py -q`
Expected: all existing tests still PASS (the `graph_preview` tests assert on a subset of nodes/links — verify `test_graph_preview_filters_relationships_by_node_set_in_sql` still passes; if its `node_ids` assertion breaks because there are now more nodes within `limit=10`/`node_limit=5`, that test's expectation needs updating — but `node_limit=5` still caps at 5 nodes and the fixture now has 5 node rows (build, u, u2, org, org2), so the assertion `{build-1, u, org}` will FAIL because u2/org2 now also load). **If it breaks:** update that test's expectation to the new node set. Specifically, change:

```python
assert node_ids == {"build-1", "u", "org"}
# only PART_OF (org->u) has both endpoints in the node set.
assert len(preview["links"]) == 1
```

to:

```python
# node_limit=5 loads all 5 node rows; FROM_UNIVERSITY x->u is still dropped
# (x not a node). Both PART_OF edges (org->u, org2->u2) survive.
assert node_ids == {"build-1", "u", "u2", "org", "org2"}
assert len(preview["links"]) == 2
assert preview["total_nodes"] == 5
assert preview["total_relationships"] == 4  # PART_OF x2 + AFFILIATED_WITH x3 would be 5, but FROM_UNIVERSITY x1 -> total rel rows = 2+3+1 = 6
```

Hold on — recompute totals honestly before editing. Total relationship rows in the new fixture: `PART_OF`×2 + `AFFILIATED_WITH`×3 + `FROM_UNIVERSITY`×1 = **6**. Total node rows: Build×1 + University×2 + OrgUnit×2 = **5**. So set:

```python
assert preview["total_nodes"] == 5
assert preview["total_relationships"] == 6
# truncated: totals (5+6=11) > shown (5 nodes + 2 links=7) -> True
assert preview["truncated"] is True
```

And `test_monitor_service_lists_build_detail_metrics_and_preview` asserts `preview["total_nodes"] == 3` and `preview["truncated"] is True` at `limit=4` (node_limit=2). With the new fixture, `limit=4` → node_limit=2 → loads Build + first University (u) only (ORDER BY Build=0, University=1). So `total_nodes` is still 5 (totals count all rows), `nodes` returned = 2. Update:

```python
preview = service.graph_preview(build_id, limit=4)
assert len(preview["nodes"]) == 2  # node_limit=2: Build + University(u)
assert preview["total_nodes"] == 5
assert preview["truncated"] is True
```

Commit the fixture + expectation updates together as `test(monitor): enrich catalog fixture with second university + AFFILIATED_WITH`.

- [ ] **Step 2: Write the failing `graph_tree` service test**

Add to `tests/test_monitor_service.py` (after the `test_graph_preview_respects_rel_limit_after_sql_filter` test, before `test_monitor_handles_empty_and_incompatible_catalog`):

```python
def test_graph_tree_returns_complete_university_orgunit_tree(tmp_path: Path) -> None:
    """graph_tree returns the full University→OrgUnit tree with professor
    counts — no truncation. Every PART_OF link endpoint is present in the node
    set, so the frontend can render connected edges (the bug the topology view
    fixes: graph_preview's truncated sample dropped endpoints)."""
    catalog = tmp_path / "catalog.db"
    build_id = _write_catalog(catalog)
    assert build_id is not None
    service = MonitorService(_settings(catalog))

    tree = service.graph_tree(build_id)
    assert set(tree.keys()) == {"build_id", "universities", "nodes", "links"}
    assert tree["build_id"] == build_id

    # 2 universities, 2 orgunits, 1 build node -> 5 nodes total.
    assert len(tree["nodes"]) == 5
    universities = [n for n in tree["nodes"] if n["category"] == "University"]
    orgunits = [n for n in tree["nodes"] if n["category"] == "OrgUnit"]
    assert len(universities) == 2
    assert len(orgunits) == 2

    # University summary list mirrors the node set.
    by_key = {u["graph_key"]: u for u in tree["universities"]}
    assert by_key["u"]["name"] == "大学A"
    assert by_key["u"]["logical_id"] == "univ:a"
    assert by_key["u"]["orgunit_count"] == 1
    assert by_key["u"]["professor_count"] == 2  # 2 AFFILIATED_WITH -> org
    assert by_key["u2"]["name"] == "大学B"
    assert by_key["u2"]["orgunit_count"] == 1
    assert by_key["u2"]["professor_count"] == 1  # 1 AFFILIATED_WITH -> org2

    # OrgUnit nodes carry kind + parent university + professor_count.
    org_node = next(n for n in orgunits if n["label"] == "学院A1")
    assert org_node["kind"] == "college"
    assert org_node["university"] == "u"
    assert org_node["professor_count"] == 2
    org2_node = next(n for n in orgunits if n["label"] == "研究所B1")
    assert org2_node["kind"] == "institute"
    assert org2_node["university"] == "u2"
    assert org2_node["professor_count"] == 1

    # Every PART_OF link endpoint is a node id in the set (no drops).
    node_ids = {n["id"] for n in tree["nodes"]}
    assert len(tree["links"]) == 2
    for link in tree["links"]:
        assert link["label"] == "PART_OF"
        assert link["source"] in node_ids
        assert link["target"] in node_ids
    targets = {link["target"] for link in tree["links"]}
    assert targets == {"u", "u2"}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest tests/test_monitor_service.py::test_graph_tree_returns_complete_university_orgunit_tree -v`
Expected: FAIL with `AttributeError: 'MonitorService' object has no attribute 'graph_tree'`

- [ ] **Step 4: Implement `graph_tree` minimally**

Add this method to `MonitorService` in `src/dext_monitor/service.py`, immediately before `_get_build` (around line 373):

```python
    def graph_tree(self, build_id: str) -> dict[str, Any]:
        """Complete University→OrgUnit tree with professor counts (no truncation).

        Unlike ``graph_preview`` (a truncated topology sample whose node-limit
        cut drops relationship endpoints), this returns every University and
        OrgUnit node plus their ``PART_OF`` edges, so the frontend can render a
        connected per-university subgraph. Node ``id`` is ``payload.graph_key``
        — the same key ``rel:PART_OF`` rows carry in ``start_graph_key`` /
        ``end_graph_key`` — so ECharts resolves every link endpoint.
        """
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            self._get_build(connection, build_id)
            university_rows = list(
                connection.execute(
                    "SELECT payload_json FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='node:University' "
                    "ORDER BY row_key",
                    (build_id,),
                )
            )
            orgunit_rows = list(
                connection.execute(
                    "SELECT payload_json FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='node:OrgUnit' "
                    "ORDER BY row_key",
                    (build_id,),
                )
            )
            part_of_rows = list(
                connection.execute(
                    "SELECT start_graph_key, end_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:PART_OF'",
                    (build_id,),
                )
            )
            affiliated_rows = list(
                connection.execute(
                    "SELECT end_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:AFFILIATED_WITH'",
                    (build_id,),
                )
            )

        # Professor count per OrgUnit (end_graph_key is the org).
        prof_count_by_org: dict[str, int] = {}
        for row in affiliated_rows:
            prof_count_by_org[row["end_graph_key"]] = (
                prof_count_by_org.get(row["end_graph_key"], 0) + 1
            )

        # org_key -> univ_key from PART_OF edges; also build links.
        org_to_univ: dict[str, str] = {}
        links: list[dict[str, Any]] = []
        for row in part_of_rows:
            org_key = str(row["start_graph_key"])
            univ_key = str(row["end_graph_key"])
            org_to_univ[org_key] = univ_key
            links.append(
                {"source": org_key, "target": univ_key, "label": "PART_OF"}
            )

        nodes: list[dict[str, Any]] = []
        universities: list[dict[str, Any]] = []
        prof_count_by_univ: dict[str, int] = {}
        org_count_by_univ: dict[str, int] = {}
        for row in university_rows:
            payload = json_loads(row["payload_json"], {})
            graph_key = str(payload.get("graph_key") or payload.get("id") or "")
            nodes.append(
                {
                    "id": graph_key,
                    "label": str(payload.get("name") or graph_key),
                    "category": "University",
                    "professor_count": 0,
                    "orgunit_count": 0,
                }
            )
            universities.append(
                {
                    "graph_key": graph_key,
                    "name": str(payload.get("name") or graph_key),
                    "logical_id": str(payload.get("logical_id") or ""),
                    "orgunit_count": 0,
                    "professor_count": 0,
                }
            )
            prof_count_by_univ[graph_key] = 0
            org_count_by_univ[graph_key] = 0

        for row in orgunit_rows:
            payload = json_loads(row["payload_json"], {})
            graph_key = str(payload.get("graph_key") or payload.get("id") or "")
            univ_key = org_to_univ.get(graph_key, "")
            profs = prof_count_by_org.get(graph_key, 0)
            nodes.append(
                {
                    "id": graph_key,
                    "label": str(payload.get("name") or graph_key),
                    "category": "OrgUnit",
                    "kind": str(payload.get("kind") or ""),
                    "professor_count": profs,
                    "university": univ_key,
                }
            )
            if univ_key in prof_count_by_univ:
                prof_count_by_univ[univ_key] += profs
                org_count_by_univ[univ_key] += 1

        # Fold per-university aggregates back onto the node + summary entries.
        for node in nodes:
            if node["category"] == "University":
                node["professor_count"] = prof_count_by_univ.get(node["id"], 0)
                node["orgunit_count"] = org_count_by_univ.get(node["id"], 0)
        for uni in universities:
            uni["professor_count"] = prof_count_by_univ.get(uni["graph_key"], 0)
            uni["orgunit_count"] = org_count_by_univ.get(uni["graph_key"], 0)

        return {
            "build_id": build_id,
            "universities": universities,
            "nodes": nodes,
            "links": links,
        }
```

Note: `json_loads` and `Any` are already imported at the top of `service.py` (`from dext_monitor.catalog_reader import … json_loads …` and `from typing import Any`). Confirm by reading the imports; if `json_loads` is not imported, add it to the existing `from dext_monitor.catalog_reader import (...)` block.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_monitor_service.py::test_graph_tree_returns_complete_university_orgunit_tree -v`
Expected: PASS

- [ ] **Step 6: Run the full monitor suite to confirm no regressions**

Run: `uv run pytest tests/test_monitor_service.py -q`
Expected: all tests PASS (including the updated `graph_preview` expectations from Step 1).

- [ ] **Step 7: Commit**

```bash
git add src/dext_monitor/service.py tests/test_monitor_service.py
git commit -m "feat(monitor): add graph_tree service — full University→OrgUnit tree with professor counts"
```

---

## Task 2: Backend HTTP endpoint (TDD)

**Files:**
- Modify: `src/dext_monitor/server.py` (add handler + route)
- Test: `tests/test_monitor_service.py` (extend the aiohttp API test)

**Interfaces:**
- Consumes: `MonitorService.graph_tree(build_id)` from Task 1.
- Produces: `GET /api/monitor/builds/{build_id}/graph-tree` → `{"data": UniversityTopologyResponse}`, with ETag/304 via the existing `error_middleware`.

- [ ] **Step 1: Write the failing endpoint test**

In `tests/test_monitor_service.py`, inside `test_monitor_aiohttp_api` (the `async with TestClient(...)` block, after the `/metrics` assertion around line 570), add:

```python
        resp = await client.get(f"/api/monitor/builds/{build_id}/graph-tree")
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["build_id"] == build_id
        # complete tree: 2 universities + 2 orgunits + 1 build = 5 nodes
        assert len(body["data"]["nodes"]) == 5
        assert len(body["data"]["links"]) == 2
        assert len(body["data"]["universities"]) == 2
        # ETag present (read-only endpoint, cacheable like /builds)
        etag = resp.headers.get("ETag")
        assert etag and etag.startswith("W/")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_monitor_service.py::test_monitor_aiohttp_api -v`
Expected: FAIL with 404 (route not registered) — `assert resp.status == 200` fails because `/api/monitor/builds/build-1/graph-tree` hits the catch-all `api_not_found` → 404.

- [ ] **Step 3: Add the handler + route**

In `src/dext_monitor/server.py`, add a new handler after `handle_graph_preview` (around line 70):

```python
async def handle_graph_tree(request: web.Request) -> web.Response:
    return json_response(
        {"data": service(request).graph_tree(request.match_info["build_id"])}
    )
```

Then in `create_app`'s `app.add_routes([...])` list (around line 130), add the route immediately after the `graph-preview` line:

```python
            web.get(f"{API_PREFIX}/builds/{{build_id}}/graph-tree", handle_graph_tree),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_monitor_service.py::test_monitor_aiohttp_api -v`
Expected: PASS

- [ ] **Step 5: Run the full monitor suite**

Run: `uv run pytest tests/test_monitor_service.py -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/dext_monitor/server.py tests/test_monitor_service.py
git commit -m "feat(monitor): expose graph-tree endpoint with ETag/304"
```

---

## Task 3: Frontend types + API method (TDD-light)

**Files:**
- Modify: `webui/src/types/monitor.ts` (append new interfaces)
- Modify: `webui/src/services/api.ts` (add method)
- Modify: `webui/tests/useMonitorData.test.ts` (add mock)

**Interfaces:**
- Produces: `UniversityTopologyResponse` type + `monitorApi.universityTopology(buildId)` used by Task 4.

- [ ] **Step 1: Add the TypeScript types**

Append to `webui/src/types/monitor.ts` (after `GraphPreviewResponse`):

```ts
export interface UniversitySummary {
  graph_key: string
  name: string
  logical_id: string
  orgunit_count: number
  professor_count: number
}

export interface UniversityTopologyNode {
  id: string
  label: string
  category: 'University' | 'OrgUnit'
  professor_count: number
  orgunit_count?: number
  kind?: string
  university?: string
}

export interface UniversityTopologyLink {
  source: string
  target: string
  label: 'PART_OF'
}

export interface UniversityTopologyResponse {
  build_id: string
  universities: UniversitySummary[]
  nodes: UniversityTopologyNode[]
  links: UniversityTopologyLink[]
}
```

- [ ] **Step 2: Add the API method**

In `webui/src/services/api.ts`, add `UniversityTopologyResponse` to the type import (line 1-9) and add a method to the `monitorApi` object (after `graphPreview`):

```ts
  universityTopology: (buildId: string) =>
    request<UniversityTopologyResponse>(
      `/api/monitor/builds/${encodeURIComponent(buildId)}/graph-tree`
    ),
```

The full import block becomes:

```ts
import type {
  ApiEnvelope,
  BuildDetailResponse,
  BuildsResponse,
  FindingsResponse,
  GraphPreviewResponse,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
```

- [ ] **Step 3: Add the mock to `useMonitorData.test.ts`**

In `webui/tests/useMonitorData.test.ts`, add `universityTopology: vi.fn()` to the `apiMocks` hoisted object (line 5-12):

```ts
const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  builds: vi.fn(),
  buildDetail: vi.fn(),
  metrics: vi.fn(),
  graphPreview: vi.fn(),
  universityTopology: vi.fn(),
  findings: vi.fn()
}))
```

And in `mockActive()` (around line 56), add after the `graphPreview` mock:

```ts
  apiMocks.universityTopology.mockResolvedValue({
    build_id: 'build-1', universities: [], nodes: [], links: []
  })
```

- [ ] **Step 4: Verify types compile**

Run: `cd webui && npx vue-tsc --noEmit`
Expected: 0 errors

- [ ] **Step 5: Verify the composable test still passes (mock now covers the new fetch)**

Run: `cd webui && npx vitest run tests/useMonitorData.test.ts`
Expected: PASS — note `useMonitorData` doesn't fetch topology yet (Task 4 wires that), so this just confirms the mock addition didn't break the existing suite.

- [ ] **Step 6: Commit**

```bash
cd webui && git add src/types/monitor.ts src/services/api.ts tests/useMonitorData.test.ts
git commit -m "feat(webui): add UniversityTopology types + API method"
```

---

## Task 4: Wire `topology` into `useMonitorData` + `App.vue` + `DashboardPage` (TDD)

**Files:**
- Modify: `webui/src/composables/useMonitorData.ts`
- Modify: `webui/tests/useMonitorData.test.ts` (assert `topology` ref populated)
- Modify: `webui/src/App.vue`
- Modify: `webui/src/pages/DashboardPage.vue`

**Interfaces:**
- Consumes: `monitorApi.universityTopology(buildId)` from Task 3.
- Produces: a `topology` ref on `useMonitorData()` return, passed as `:topology` prop to `DashboardPage`, which renders `UniversityTopologyChart` (created in Task 5). To keep Task 4 independently testable, Task 4 swaps the panel to `UniversityTopologyChart` only in Task 5; here it just adds the prop plumbing and leaves the old panel in place — but the old panel reads `graph`, not `topology`. So Task 4 also replaces the panel import. To avoid a dangling import in Task 4, **Task 4 and Task 5 are ordered: Task 4 plumbs the ref + prop and KEEPS `GraphNetworkPreview` (still wired to `graph`); Task 5 creates `UniversityTopologyChart` and swaps the panel.** Re-ordered below.

- [ ] **Step 1: Write the failing composable test**

In `webui/tests/useMonitorData.test.ts`, add a test inside the `describe('useMonitorData', ...)` block (after the existing tests). First check what the existing tests look like by reading the file fully; the new test asserts that after the initial refresh resolves, `topology` holds the mocked payload:

```ts
  it('exposes a topology ref populated from universityTopology', async () => {
    apiMocks.universityTopology.mockResolvedValue({
      build_id: 'build-1',
      universities: [{ graph_key: 'u', name: '大学A', logical_id: 'univ:a', orgunit_count: 1, professor_count: 2 }],
      nodes: [{ id: 'u', label: '大学A', category: 'University', professor_count: 2, orgunit_count: 1 }],
      links: []
    })
    const { topology } = useMonitorData()
    await nextTick()
    await vi.runOnlyPendingTimersAsync()
    expect(apiMocks.universityTopology).toHaveBeenCalledWith('build-1')
    expect(topology.value?.universities).toHaveLength(1)
    expect(topology.value?.universities[0].name).toBe('大学A')
  })
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd webui && npx vitest run tests/useMonitorData.test.ts`
Expected: FAIL — `topology` is undefined (not returned by `useMonitorData`).

- [ ] **Step 3: Add `topology` to `useMonitorData.ts`**

In `webui/src/composables/useMonitorData.ts`:

1. Add `UniversityTopologyResponse` to the type import (line 3-9):

```ts
import type {
  BuildDetailResponse,
  BuildsResponse,
  GraphPreviewResponse,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
```

2. Add the ref near the other refs (after `graph`, line 30):

```ts
  const topology = ref<UniversityTopologyResponse | null>(null)
```

3. In `loadBuild`, replace the `Promise.all` triple with a quadruple (line 66-73):

```ts
    const [nextDetail, nextMetrics, nextGraph, nextTopology] = await Promise.all([
      monitorApi.buildDetail(buildId),
      monitorApi.metrics(buildId),
      monitorApi.graphPreview(buildId),
      monitorApi.universityTopology(buildId)
    ])
    detail.value = nextDetail
    metrics.value = nextMetrics
    graph.value = nextGraph
    topology.value = nextTopology
```

4. In the `loadBuild` early-return guard (when `!buildId`), also clear topology:

```ts
    if (!buildId) {
      detail.value = null
      metrics.value = null
      graph.value = null
      topology.value = null
      return
    }
```

5. Add `topology` to the returned object (line 143-161):

```ts
    topology,
```

- [ ] **Step 4: Run the composable test to verify it passes**

Run: `cd webui && npx vitest run tests/useMonitorData.test.ts`
Expected: PASS

- [ ] **Step 5: Plumb `topology` through `App.vue`**

In `webui/src/App.vue`, destructure `topology` from `useMonitorData()` (add to the destructure list, line 8-23) and pass it to `DashboardPage`:

```ts
const {
  health,
  builds,
  selectedBuildId,
  activeBuildId,
  detail,
  metrics,
  graph,
  topology,
  error,
  paused,
  lastUpdated,
  history,
  refresh,
  selectBuild,
  togglePause
} = useMonitorData()
```

And in the template, add the prop (after `:graph="graph"`, line 63):

```html
    :graph="graph"
    :topology="topology"
```

- [ ] **Step 6: Add the `topology` prop to `DashboardPage.vue` (without swapping the panel yet)**

In `webui/src/pages/DashboardPage.vue`, add `UniversityTopologyResponse` to the type import (line 2-9) and add the prop to `defineProps` (after `graph`, line 37):

```ts
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  GraphPreviewResponse,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
```

```ts
  graph: GraphPreviewResponse | null
  topology: UniversityTopologyResponse | null
```

(The panel swap happens in Task 5 — for now `topology` is accepted but unused, which is fine; `vue-tsc` won't error on an unused prop.)

- [ ] **Step 7: Verify typecheck + full webui test suite**

Run: `cd webui && npx vue-tsc --noEmit && npx vitest run`
Expected: 0 type errors; all tests PASS.

- [ ] **Step 8: Commit**

```bash
cd webui && git add src/composables/useMonitorData.ts src/App.vue src/pages/DashboardPage.vue tests/useMonitorData.test.ts
git commit -m "feat(webui): plumb topology ref through useMonitorData → App → DashboardPage"
```

---

## Task 5: `UniversityTopologyChart` component + panel swap (TDD)

**Files:**
- Create: `webui/src/components/charts/UniversityTopologyChart.vue`
- Create: `webui/tests/university-topology.test.ts`
- Modify: `webui/src/pages/DashboardPage.vue` (swap panel + imports)
- Delete: `webui/src/components/charts/GraphNetworkPreview.vue`

**Interfaces:**
- Consumes: `topology: UniversityTopologyResponse | null` prop (from Task 4); `ChartFrame.vue` + `EmptyState.vue` (existing).
- Produces: the rendered topology panel replacing `GraphNetworkPreview`.

- [ ] **Step 1: Write the failing chart test**

Create `webui/tests/university-topology.test.ts`, modeled on `role-title-charts.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const stubs = vi.hoisted(() => ({
  ChartFrame: {
    name: 'ChartFrame',
    props: ['option', 'minHeight'],
    template: '<div class="chart-stub" />'
  },
  EmptyState: {
    name: 'EmptyState',
    props: ['title', 'message'],
    template: '<div class="empty-stub">{{ title }}</div>'
  }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

import UniversityTopologyChart from '../src/components/charts/UniversityTopologyChart.vue'
import type { UniversityTopologyResponse } from '../src/types/monitor'

function buildTopology(): UniversityTopologyResponse {
  return {
    build_id: 'b1',
    universities: [
      { graph_key: 'u', name: '大学A', logical_id: 'univ:a', orgunit_count: 1, professor_count: 2 },
      { graph_key: 'u2', name: '大学B', logical_id: 'univ:b', orgunit_count: 1, professor_count: 1 }
    ],
    nodes: [
      { id: 'u', label: '大学A', category: 'University', professor_count: 2, orgunit_count: 1 },
      { id: 'u2', label: '大学B', category: 'University', professor_count: 1, orgunit_count: 1 },
      { id: 'org', label: '学院A1', category: 'OrgUnit', kind: 'college', professor_count: 2, university: 'u' },
      { id: 'org2', label: '研究所B1', category: 'OrgUnit', kind: 'institute', professor_count: 1, university: 'u2' }
    ],
    links: [
      { source: 'org', target: 'u', label: 'PART_OF' },
      { source: 'org2', target: 'u2', label: 'PART_OF' }
    ]
  }
}

describe('UniversityTopologyChart', () => {
  it('renders all universities + orgunits + PART_OF links when no university is selected', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const series = (frame.props('option') as { series: Array<{ data: Array<{ id: string; name: string }>; links: Array<{ source: string; target: string }> }> }).series[0]
    expect(series.data.map((n) => n.id).sort()).toEqual(['org', 'org2', 'u', 'u2'])
    expect(series.links).toHaveLength(2)
  })

  it('filters to the selected university cluster', async () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    // select 大学B (graph_key 'u2') via the dropdown
    await wrapper.find('select').setValue('u2')
    const series = (wrapper.findComponent(stubs.ChartFrame).props('option') as { series: Array<{ data: Array<{ id: string }>; links: Array<{ source: string }> }> }).series[0]
    expect(series.data.map((n) => n.id).sort()).toEqual(['org2', 'u2'])
    expect(series.links).toHaveLength(1)
    expect(series.links[0].source).toBe('org2')
  })

  it('shows EmptyState when topology is null', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: null },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
  })

  it('labels OrgUnit nodes with name + professor count', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const series = (wrapper.findComponent(stubs.ChartFrame).props('option') as { series: Array<{ data: Array<{ id: string; name: string }> }> }).series[0]
    const org = series.data.find((n) => n.id === 'org')
    expect(org?.name).toBe('学院A1 ·2')
    const uni = series.data.find((n) => n.id === 'u')
    expect(uni?.name).toBe('大学A')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd webui && npx vitest run tests/university-topology.test.ts`
Expected: FAIL — `UniversityTopologyChart` module not found / doesn't exist.

- [ ] **Step 3: Create `UniversityTopologyChart.vue`**

Create `webui/src/components/charts/UniversityTopologyChart.vue`:

```vue
<script setup lang="ts">
import { computed, ref } from 'vue'
import type { UniversityTopologyResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import type { ChartOption } from './options'

const props = defineProps<{
  topology: UniversityTopologyResponse | null
}>()

// null = show all universities. A university graph_key filters to its cluster.
const selectedUniversity = ref<string | null>(null)

const KIND_CATEGORIES = ['University', 'college', 'institute', 'department', 'hospital']

function categoryIndex(node: { category: string; kind?: string }): number {
  if (node.category === 'University') return 0
  const idx = KIND_CATEGORIES.indexOf(node.kind || '')
  return idx > 0 ? idx : 1 // unknown kinds fall back to 'college' bucket
}

const filtered = computed(() => {
  const all = props.topology
  if (!all) return { nodes: [], links: [] }
  if (!selectedUniversity.value) return { nodes: all.nodes, links: all.links }
  const keep = new Set<string>([selectedUniversity.value])
  for (const n of all.nodes) {
    if (n.university === selectedUniversity.value) keep.add(n.id)
  }
  return {
    nodes: all.nodes.filter((n) => keep.has(n.id)),
    links: all.links.filter((l) => keep.has(l.source) && keep.has(l.target))
  }
})

function symbolSize(node: { category: string; professor_count: number }): number {
  if (node.category === 'University') return 44
  return 12 + Math.min(Math.max(Math.floor(node.professor_count / 30), 0), 24)
}

function nodeName(node: { category: string; label: string; professor_count: number }): string {
  if (node.category === 'University') return node.label
  return `${node.label} ·${node.professor_count}`
}

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: {},
  legend: { top: 0, textStyle: { color: '#92a4bd' } },
  series: [
    {
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      categories: KIND_CATEGORIES.map((name) => ({ name })),
      data: filtered.value.nodes.map((node) => ({
        id: node.id,
        name: nodeName(node),
        category: categoryIndex(node),
        symbolSize: symbolSize(node)
      })),
      links: filtered.value.links.map((link) => ({
        source: link.source,
        target: link.target,
        name: link.label
      })),
      force: { repulsion: 120, edgeLength: 60, gravity: 0.1 },
      label: {
        show: true,
        color: '#e8f0ff',
        fontSize: 10,
        formatter: (params: { name: string }) =>
          params.name.length > 14 ? `${params.name.slice(0, 14)}…` : params.name
      },
      lineStyle: { color: 'rgba(146,164,189,0.55)', curveness: 0.18 },
      itemStyle: { color: '#3ee6b5' }
    }
  ]
}))

function onSelect(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedUniversity.value = value === '__all__' ? null : value
}
</script>

<template>
  <div>
    <select
      v-if="topology && topology.universities.length"
      class="uni-select"
      :value="selectedUniversity ?? '__all__'"
      @change="onSelect"
    >
      <option value="__all__">全部大学</option>
      <option
        v-for="uni in topology.universities"
        :key="uni.graph_key"
        :value="uni.graph_key"
      >
        {{ uni.name }} ({{ uni.orgunit_count }}学院 / {{ uni.professor_count }}教授)
      </option>
    </select>
    <ChartFrame v-if="topology && topology.nodes.length" :option="option" :min-height="390" />
    <EmptyState
      v-else
      title="No topology rows"
      message="University→学院 topology is not available for the selected build yet."
    />
  </div>
</template>

<style scoped>
.uni-select {
  margin-bottom: 0.6rem;
  padding: 0.35rem 0.5rem;
  background: rgba(62, 230, 181, 0.06);
  color: var(--text, #e8f0ff);
  border: 1px solid rgba(146, 164, 189, 0.35);
  border-radius: 6px;
  font-size: 0.84rem;
}
</style>
```

- [ ] **Step 4: Run the chart test to verify it passes**

Run: `cd webui && npx vitest run tests/university-topology.test.ts`
Expected: PASS (all 4 cases).

- [ ] **Step 5: Swap the panel in `DashboardPage.vue`**

In `webui/src/pages/DashboardPage.vue`:

1. Replace the `GraphNetworkPreview` import with `UniversityTopologyChart` (line 15):

```ts
import UniversityTopologyChart from '../components/charts/UniversityTopologyChart.vue'
```

(remove the `import GraphNetworkPreview from '../components/charts/GraphNetworkPreview.vue'` line)

2. If `GraphPreviewResponse` is now unused in the type import, leave it — it's still referenced by the `graph` prop. Keep the `graph` prop.

3. Replace the panel template (lines 144-148):

```html
          <PanelCard title="University topology" subtitle="大学 → 学院 with professor counts">
            <div class="panel-body">
              <UniversityTopologyChart :topology="topology" />
            </div>
          </PanelCard>
```

- [ ] **Step 6: Delete `GraphNetworkPreview.vue`**

```bash
cd webui && git rm src/components/charts/GraphNetworkPreview.vue
```

- [ ] **Step 7: Verify typecheck + full webui test suite + build**

Run: `cd webui && npx vue-tsc --noEmit && npx vitest run && npm run build`
Expected: 0 type errors; all tests PASS; `dist/` builds.

- [ ] **Step 8: Commit**

```bash
cd webui && git add src/components/charts/UniversityTopologyChart.vue src/pages/DashboardPage.vue tests/university-topology.test.ts
cd webui && git rm src/components/charts/GraphNetworkPreview.vue 2>/dev/null; git add -A
git commit -m "feat(webui): UniversityTopologyChart with university selector + professor counts"
```

(If `git rm` already staged the deletion in Step 6, the second `git rm` is a no-op — `git add -A` captures everything.)

---

## Task 6: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full Python suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 2: Run the full webui suite + build**

Run: `cd webui && npm test && npm run build`
Expected: all PASS; `dist/` rebuilt.

- [ ] **Step 3: Manual smoke test against the real catalog**

Run: `uv run dext monitor serve` (from repo root; serves `webui/dist` on `http://localhost:21530`).

Open `http://localhost:21530` in a browser. In the "University topology" panel:
- Confirm the dropdown lists all 8 universities (复旦大学, 兰州大学, 南开大学, …) with `N学院 / M教授` counts.
- Confirm "全部大学" shows 8 university nodes each connected to its college cluster via `PART_OF` edges — **no isolated points** (the original bug).
- Select a single university (e.g. 复旦大学) — confirm only its node + its OrgUnits render, connected.
- Confirm OrgUnit node labels show `学院名 ·N` (professor count).
- Confirm the legend colors OrgUnits by `kind` (college/institute/department/hospital).

- [ ] **Step 4: Commit any verification-driven fixes (if any)**

If the smoke test reveals a layout issue (e.g. repulsion too tight for a 73-OrgUnit university like 南开), tune the `force` numbers in `UniversityTopologyChart.vue` and re-run the chart test. Commit as `fix(webui): tune topology force layout for large clusters`. If no fixes needed, skip.

---

## Self-Review (run after writing, before handoff)

**1. Spec coverage:**
- ✅ New endpoint `GET /graph-tree` returning complete tree — Tasks 1+2.
- ✅ `UniversityTopologyChart` with university `<select>` + client-side filter — Task 5.
- ✅ Fetch alongside existing per-build data (Promise.all) — Task 4.
- ✅ ETag/304 reuse — Task 2 (automatic via middleware; asserted in test).
- ✅ Name + professor count on nodes — Task 5 (`nodeName`, test case 4).
- ✅ Replace `GraphNetworkPreview` panel — Task 5 Step 5-6.
- ✅ Backend pytest + frontend vitest — Tasks 1, 2, 4, 5.
- ✅ OrgUnit `kind` legend categories — Task 5 (`KIND_CATEGORIES`).
- ✅ Root-cause fix (endpoint-closed graph) — Task 1 emits `payload.graph_key` as node `id`; test asserts every link endpoint is in the node set.

**2. Placeholder scan:** none — every step has concrete code or exact commands. The one "If it breaks" branch in Task 1 Step 1 is resolved inline with exact replacement code and recomputed totals (5 nodes / 6 rels).

**3. Type consistency:**
- `graph_tree` return keys (`build_id`, `universities`, `nodes`, `links`) match the TS `UniversityTopologyResponse` (Task 3) and the service test (Task 1).
- Node fields: `id`, `label`, `category`, `professor_count`, `orgunit_count` (University), `kind`/`university` (OrgUnit) — consistent across Python dict, TS interface, and chart test.
- `monitorApi.universityTopology` (Task 3) ↔ `useMonitorData` call (Task 4) ↔ mock (Task 3 Step 3) — name matches.
- `topology` ref name consistent: composable → App prop `:topology` → DashboardPage prop → chart prop.
- Route path `/graph-tree` consistent across server route, handler, API method, and aiohttp test.

**One gap fixed during review:** Task 1 Step 1's fixture change ripples into two existing `graph_preview` tests (`test_graph_preview_filters_relationships_by_node_set_in_sql` and `test_monitor_service_lists_build_detail_metrics_and_preview`). The plan now spells out the exact assertion updates and recomputes the totals (5 nodes, 6 relationship rows) so the implementer doesn't have to derive them.
