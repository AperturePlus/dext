# Monitor — University ↔ 学院 topology view

**Date:** 2026-06-30
**Branch:** slice4 (off `tc3`)
**Status:** design — awaiting plan
**Touches:** `dext_monitor` (backend), `webui` (frontend), `tests`

## Problem

The "Graph preview" panel in the monitor WebUI renders the catalog graph as a
field of **isolated points** with no visible University↔学院 structure. The user
asked for two things:

1. Visualize the **relationship between universities and their colleges/OrgUnits**
   (currently shown as disconnected dots).
2. Allow **selecting a single university** to view it in isolation.

## Root cause

Two compounding issues, neither of which is "the data has no relationships."

### Data has the relationships

Real catalog, latest build (`019f135d-…`):

| Entity | Count |
|--------|-------|
| University | 8 |
| OrgUnit (学院/institute/department/hospital via `kind`) | 315 |
| Professor | 11 801 |

Relationships (`graph_export_rows`, `row_kind='relationship'`):

| Type | Endpoint pair | Count |
|------|---------------|-------|
| `PART_OF` | OrgUnit → University | 315 |
| `AFFILIATED_WITH` | Professor → OrgUnit | 11 977 |
| `HAS_RESEARCH_STATEMENT` | Professor → ResearchStatement | 40 042 |
| `HAS_PUBLICATION_MENTION` | Professor → PublicationMention | 75 135 |
| `OBSERVED_IN` | Professor → SourceDocument | 11 550 |
| `FROM_UNIVERSITY` | SourceDocument → University | 11 550 |

All 11 977 professors map cleanly to one of the 8 universities through the
`PART_OF` + `AFFILIATED_WITH` join (verified against the real catalog).

### Issue 1: `graph_preview` is a truncated topology sample, not a per-university view

`MonitorService.graph_preview` (service.py:234) returns a **flat, truncated**
sample: it picks `node_limit = limit // 2` nodes (≈150 at the default `limit=300`)
ordered `Build → University → OrgUnit → Professor`, then keeps only relationships
whose **both endpoints** are in that node set.

Because the 150-node budget is eaten by `Build` + 8 `University` + ~141 `OrgUnit`
nodes, almost no `Professor` nodes make the cut, so nearly all `AFFILIATED_WITH`
links are dropped (their endpoints aren't in the returned set). What survives is a
handful of `PART_OF` stars buried in a force-layout hairball with long UUID
labels. It reads as "isolated points."

This is **by design** for a topology preview — it was never meant to answer "show
me university X and its colleges." Reusing it for that purpose is the wrong tool.

### Issue 2: the rendering config compounds it

`GraphNetworkPreview.vue` passes node `id` (the long `graph_key`) as both
`data.id` and `data.name`, and link `source`/`target` as the same keys. ECharts'
graph builder (`createGraphFromNodeEdge` in echarts.esm.js:55959) adds a node
under `retrieve(nodes[i].id, nodes[i].name, i)` — i.e. it prefers `id`, falling
back to `name`, then index. Links resolve via `graph.addEdge(source, target)`,
which looks the endpoint up in `_nodesMap` by `generateNodeKey(id)` (the `_EC_` +
id prefix). If either endpoint isn't in the node set, `addEdge` returns falsy and
the link is silently dropped (`createGraphFromNodeEdge` pushes only into
`validEdges`).

So the current code is **correct** when all link endpoints are present — but
issue 1 ensures they usually aren't. The fix is therefore on the **data**
side (give the panel a complete, endpoint-closed subgraph), not the render side.

## Non-goals (YAGNI)

- Professor nodes (11 801 — would turn the graph into an unreadable hairball).
  Professor counts are surfaced as numbers on the OrgUnit nodes instead.
- Research-statement / publication / source-document edges.
- Server-side `?university=` filtering. The full tree (8 + 315 = 323 nodes) is
  small enough to fetch once and filter client-side; a server filter is YAGNI
  until the graph grows by orders of magnitude. Because the endpoint returns the
  complete tree, a future Professor drill-down can be added as a client-side
  filter with **no backend change**.
- Drill-down / expand interaction.

## Design

### Backend — new read-only endpoint

`GET /api/monitor/builds/{build_id}/graph-tree`

Returns the complete University→OrgUnit tree for a build, with professor counts.
**No truncation, no `limit` parameter.**

Response shape:

```jsonc
{
  "build_id": "019f135d-…",
  "universities": [
    { "graph_key": "019f135d-…:univ:fudan", "name": "复旦大学",
      "logical_id": "univ:fudan", "orgunit_count": 36, "professor_count": 2760 }
  ],
  "nodes": [
    { "id": "019f135d-…:univ:fudan", "label": "复旦大学",
      "category": "University", "professor_count": 2760, "orgunit_count": 36 },
    { "id": "019f135d-…:<uuid>", "label": "计算机学院",
      "category": "OrgUnit", "kind": "college", "professor_count": 42,
      "university": "019f135d-…:univ:fudan" }
  ],
  "links": [
    { "source": "019f135d-…:<uuid>", "target": "019f135d-…:univ:fudan",
      "label": "PART_OF" }
  ]
}
```

`MonitorService.graph_tree(build_id) -> dict` in `src/dext_monitor/service.py`,
assembled from four indexed queries over `graph_export_rows`:

1. `partition_key='node:University'` rows → university nodes. `name` and
   `graph_key` from `payload_json` (node rows carry `start_graph_key`/`end_graph_key`
   as **NULL** — the endpoint keys live in `payload_json.graph_key`; only
   relationship rows populate `start_graph_key`/`end_graph_key`).
2. `partition_key='node:OrgUnit'` rows → orgunit nodes. `name`, `kind` from
   `payload_json`.
3. `partition_key='rel:PART_OF'` rows → links (org `start_graph_key` → univ
   `end_graph_key`) **and** an in-memory `org_key → univ_key` map.
4. `partition_key='rel:AFFILIATED_WITH'` grouped by `end_graph_key` (the org) →
   professor count per org. University totals = sum of its orgs' counts.

All node `id`s use `payload.graph_key` — the **same** key the relationship rows
carry in `start_graph_key`/`end_graph_key` — so links resolve in ECharts with no
endpoint drops.

**Route registration** in `server.py:create_app`, alongside the existing
`graph-preview` route:

```python
web.get(f"{API_PREFIX}/builds/{{build_id}}/graph-tree", handle_graph_tree),
```

with a thin `handle_graph_tree` handler mirroring `handle_build_detail` (no query
params). The existing `error_middleware` applies the weak-ETag / 304 handling
automatically (terminal-build payload is byte-stable across polls → zero-byte
304s, same as the other read-only endpoints).

The existing `graph_preview` / `graph-preview` endpoint is left untouched (it is
tested and harmless; it simply stops being wired into the dashboard).

### Frontend — new chart + university selector

**Replace** the "Graph preview" `PanelCard` in
[DashboardPage.vue](webui/src/pages/DashboardPage.vue) (lines 144–148) with a
`UniversityTopologyChart` panel in the same slot. The `GraphNetworkPreview.vue`
component is deleted.

**New types** in [monitor.ts](webui/src/types/monitor.ts):

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
  orgunit_count?: number      // University only
  kind?: string               // OrgUnit only ('college' | 'institute' | …)
  university?: string         // OrgUnit only — parent university graph_key
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

**New API method** in [api.ts](webui/src/services/api.ts):

```ts
universityTopology: (buildId: string) =>
  request<UniversityTopologyResponse>(
    `/api/monitor/builds/${encodeURIComponent(buildId)}/graph-tree`
  ),
```

Goes through the existing `request<T>` helper, so it inherits ETag/304 caching.

**Fetch** in `useMonitorData.ts`: add `monitorApi.universityTopology(buildId)` as
a fourth member of the existing `Promise.all` in `loadBuild`, store in a new
`topology` ref, return it from the composable. Polls on the existing 5 s (active)
/ 30 s (terminal) cadence. `App.vue` passes `topology` down to `DashboardPage`
alongside `graph` (which can be dropped from the page once the panel is replaced,
but leaving the prop wired is harmless — minimize churn).

**`UniversityTopologyChart.vue`** (new, `components/charts/`):

- Props: `topology: UniversityTopologyResponse | null`.
- Local `selectedUniversity = ref<string | null>(null)` (`null` ⇒ show all).
- A `<select>` (or styled list) of universities + a leading "全部大学" option,
  bound to `selectedUniversity`.
- A `computed` filtered view: when `selectedUniversity` is set, keep University
  nodes whose `graph_key` matches and OrgUnit nodes whose `university` matches;
  otherwise keep all.
- A `computed<ChartOption>` ECharts graph series over the filtered set:
  - `categories`: `['University', 'college', 'institute', 'department',
    'hospital']` so the legend colors OrgUnits by `kind`.
  - `data`: each node → `{ id, name, category, symbolSize }`. University
    `symbolSize: 44`; OrgUnit scaled by professor count, e.g.
    `12 + clamp(professor_count / 30, 0, 24)`.
  - Node label: University → `name`; OrgUnit → `name ·{professor_count}`.
  - `links`: `PART_OF` edges among the filtered nodes (already endpoint-closed
    by construction).
  - `force: { repulsion: 120, edgeLength: 60, gravity: 0.1 }` — tighter than
    the old preview since per-university clusters are small.
  - `roam: true`, `draggable: true`, dark-theme label/line colors matching the
    existing `GraphNetworkPreview` palette.
- Reuses `ChartFrame.vue` (inherits dark theme + merge-mode polling).
- `EmptyState` when `topology` is null or has no nodes (build pre-export).

### Why this fixes "isolated points"

- The endpoint returns the **complete** tree — every `PART_OF` link's endpoints
  are present in the node set, so ECharts drops none.
- Filtering by university is a pure subset of an endpoint-closed graph, so the
  filtered subgraph is also endpoint-closed.
- Node `id` === relationship-row `start_graph_key`/`end_graph_key`, so ECharts'
  `_nodesMap` lookup resolves every link.

## Testing

**Backend** — extend [test_monitor_service.py](tests/test_monitor_service.py):

The existing `_write_catalog` fixture already inserts one University + one
OrgUnit + one `PART_OF` + one `FROM_UNIVERSITY`. Extend it (or add a sibling
fixture) to insert a **second university** with two OrgUnits and a couple of
`AFFILIATED_WITH` rows, then assert `service.graph_tree(build_id)`:

- Returns two university nodes with correct `name`, `orgunit_count`,
  `professor_count`.
- Every OrgUnit node has a `PART_OF` link whose `target` is its university's
  `graph_key`.
- `professor_count` per university equals the sum of `AFFILIATED_WITH` counts
  over its OrgUnits.
- University `professor_count` sums to the total `AFFILIATED_WITH` count.

Add an `aiohttp.test_utils` client test that `GET
/api/monitor/builds/{id}/graph-tree` returns 200 with the envelope, mirroring the
existing endpoint tests.

**Frontend** — new `webui/tests/university-topology.test.ts`, modeled on
`role-title-charts.test.ts` (stub `ChartFrame`, assert on the computed `option`):

- Renders all universities + OrgUnits + `PART_OF` links when no university is
  selected.
- When a university is selected, the `option.series[0].data` contains only that
  university's node + its OrgUnits, and `links` contains only its `PART_OF` edges.
- OrgUnit label formatter includes the professor count.
- Shows `EmptyState` when `topology` is null.

## Verification commands

- `uv run pytest tests/test_monitor_service.py -v`
- `cd webui && npm test` (vitest)
- `cd webui && npm run build` (typecheck + vite build → `dist/`)
- `uv run dext monitor serve` + open `http://localhost:21530`, pick a university,
  confirm the cluster renders with connected `PART_OF` edges and professor counts.

## Conventions / invariants honored

- Read-only monitor: no writes, no graph-workflow imports (settings.py already
  forbids them).
- Full UTF-8 end to end (`ensure_ascii=False` in `json_response`).
- ETag/304 reuse the existing middleware — no new caching code.
- KISS: no connection pools, no server-side filtering, one new endpoint + one
  new component. Existing `graph_preview` endpoint and its tests are left
  intact (the panel that consumed it is replaced).
- One conventional commit per green step (`feat(monitor): …`, `feat(webui): …`).
