# Monitor frontend: sidebar routing + light restyle + topology drill-down

**Date:** 2026-06-30
**Scope:** `webui/` (Vue 3 + vue-router + ECharts) + one new read-only `dext_monitor` backend endpoint.
**Branch:** off `slice4` (current work branch).

## Problem

The monitor dashboard (`DashboardPage.vue`) crams ~11 panels onto one route
(metrics, builds, findings, severity, role dist, build stage, source ingestion,
throughput, export partitions, title families, runs, checkpoints), making it
noisy and unfocused. The theme is dark; the user wants light. The topology page
filters by university but has no college→teacher drill-down — selecting a
university only shows its college cluster, never the professors under a college.

## Goals

1. Replace the top nav with a **persistent left sidebar** that routes between
   focused pages.
2. Split the monolithic dashboard into **3 routes** (Overview / Data & Quality /
   Topology), each ~3–4 panels.
3. **Light palette** with a blue accent.
4. Topology page gains a **university→college two-level dropdown**; selecting a
   college renders that college's subgraph (college node + professor nodes +
   `AFFILIATED_WITH` edges) via a new backend endpoint.
5. ECharts colors centralized in `options.ts` + `tokens.css` (no scattered
   hardcoded values).

## Non-goals

- No new data sources; the existing `useMonitorData` shared poll stays.
- No professor drill-down beyond one college (no professor→research-statements
  expansion in this slice).
- No visual snapshot tests; light-theme aesthetics are human-verified.
- No auth, no pagination of the professor list (a college holds 10–150 profs;
  force-layout handles it without a cap).

## §1 Architecture & routing

**Layout** — `App.vue` changes from a top nav-link bar to a two-column
`sidebar + main` shell:

- `AppSidebar.vue` (≈220px fixed left): brand mark `dx · dext monitor`, three
  `router-link`s (Overview / Data & Quality / Topology) with active state, and
  a footer holding `StatusBadge` + `RefreshControl`.
- Main area: `<router-view>`. Each page owns its own hero.

**Routes** (3):

| Path | Name | Component | Responsibility |
|---|---|---|---|
| `/` | overview | OverviewPage | metric cards + build-stage timeline + throughput sparkline + Runs summary |
| `/data` | data | DataQualityPage | Builds list + Findings + severity + role dist + source ingestion + export partitions + title families + Checkpoints |
| `/topology` | topology | TopologyPage | university→college two-level dropdown; college drill-down subgraph |
| `/:pathMatch(.*)*` | — | redirect → `/` | (unchanged fallback) |

**Data layer unchanged.** `useMonitorData` continues to hold shared state
(health/builds/detail/metrics/topology/history/findings) at the `App.vue`
top level and pass it down via props — pages do not poll independently. The
college professor subgraph is **on-demand local state** owned by the topology
page: fetched only when a college is selected, never entering the shared poll.

## §2 Palette & chart theming

**`tokens.css` → light** (blue accent):

```css
color-scheme: light
--bg: #f6f8fc
--bg-strong: #eef2f8
--surface: #ffffff
--surface-strong: #ffffff
--surface-soft: #f1f4fa
--border: rgba(30, 58, 110, 0.12)
--border-strong: rgba(30, 58, 110, 0.22)
--text: #1a2333
--muted: #5b6b82
--subtle: #8a98ad
--accent: #2f6bff        /* blue (deepened from old --accent-2) */
--accent-2: #3ee6b5     /* mint demoted to secondary accent */
--warning / --danger / --success kept (saturation nudged for light bg)
--shadow: 0 8px 24px rgba(30,58,110,0.10)
radii unchanged
```

The mint green `#3ee6b5` is preserved as `--accent-2` (secondary accent for
progress/success states) so the brand isn't erased; the **primary** accent
becomes blue.

**`base.css`**: drop the dark radial gradients; `background: var(--bg)` with
an optional very-low-opacity blue top glow.

**`options.ts` — central ECharts light theme** (new export):

- `chartTheme` constant: `text #5b6b82`, `axisLine rgba(30,58,110,0.12)`,
  `splitLine rgba(30,58,110,0.06)`.
- `palette`: `['#2f6bff','#3ee6b5','#f6c85f','#ff6b7a','#9b8cff','#4dd0e1','#f59e6c']`.
- Each chart component removes hardcoded `#92a4bd`/`#e8f0ff`/`rgba(146,164,189,…)`
  and reads `chartTheme`. Topology `itemStyle.color` switches from `#3ee6b5` to
  per-category palette lookup.

Re-skinning later = edit `tokens.css` + `options.ts` only.

## §3 Topology drill-down + backend contract

### Frontend interaction

`TopologyPage.vue` PanelCard header holds two dropdowns:

1. **University dropdown** (existing logic): select a university → canvas shows
   that university's college cluster (college nodes + `PART_OF` edges — current
   behavior).
2. **College dropdown** (new, enabled only after a university is chosen): lists
   that university's colleges (`label · professor_count`). **Selecting one** →
   canvas switches to that college's subgraph.
3. **「返回大学视图」button** clears the college selection, returning to the
   selected university's college cluster.

**Selection coupling**: the college dropdown is enabled only after a university
is chosen, and its options are that university's colleges. Changing the
university **clears** the college selection and the subgraph (a college belongs
to exactly one university), then repopulates the college dropdown for the new
university's colleges. The university dropdown stays selected while a college is
drilled in (the college is under it).

**College subgraph content**:

- 1 central college node (OrgUnit, large blue node).
- N professor nodes (Professor, smaller; `symbolSize` fixed or by title).
- `AFFILIATED_WITH` edges professor→college.
- Professor tooltip: `name · title · title_family · role_status`.

When no college is selected: maintain current behavior (college cluster for a
selected university, or all universities when nothing is selected).

### New backend endpoint

`GET /api/monitor/builds/{build_id}/orgunit/{org_graph_key}/professors`

Returns the professors under one college + their `AFFILIATED_WITH` edges.
`org_graph_key` is the OrgUnit `id` returned by `graph_tree`
(= `{build_id}:{org_logical_id}`).

**Implementation** — new `MonitorService.orgunit_professors(build_id,
org_graph_key)` in `src/dext_monitor/service.py`:

1. Validate build exists (`_get_build`) — raises `MonitorCatalogError`/404 if
   unknown.
2. From `rel:AFFILIATED_WITH` rows, collect `start_graph_key` (professor keys)
   where `end_graph_key = org_graph_key`.
3. From `node:Professor` rows, read payloads where `graph_key ∈` professor-key
   set → professor nodes (name/title/title_family/role_status).
4. Assemble `nodes` (1 college + N professors) + `links` (AFFILIATED_WITH,
   `source`=professor, `target`=college).

**Response contract** (`OrgUnitProfessorResponse`):

```ts
{
  build_id: string
  orgunit: { graph_key: string; label: string; kind: string; university: string }
  professors: {
    graph_key: string
    name: string
    title: string | null
    title_family: string | null
    role_status: string
  }[]
  links: { source: string; target: string; label: 'AFFILIATED_WITH' }[]
}
```

**Invariants / notes**:

- Professor key = `node:Professor` row's `payload.graph_key`
  (= `{build_id}:{entity_id}`), which equals `AFFILIATED_WITH.start_graph_key` —
  every link endpoint resolves (same invariant `graph_tree` already relies on).
- College key comes from `graph_tree`'s OrgUnit `id`; passed as a path segment,
  URL-encoded (the key contains `:`, so `encodeURIComponent` → `%3A`).
- Read-only → flows through the existing ETag middleware (supports 304).
- Empty result for an unknown/empty college is legal (`professors: []`,
  `links: []`), not a 404.

### Frontend API client

`webui/src/services/api.ts`:

```ts
orgUnitProfessors: (buildId: string, orgGraphKey: string) =>
  request<OrgUnitProfessorResponse>(
    `/api/monitor/builds/${encodeURIComponent(buildId)}/orgunit/${encodeURIComponent(orgGraphKey)}/professors`
  )
```

## §4 Component inventory

### New files

| File | Purpose |
|---|---|
| `webui/src/components/layout/AppSidebar.vue` | Persistent left sidebar: brand + 3 router-links (active state) + footer StatusBadge + RefreshControl. Props: health/paused/lastUpdated. Emits: refresh/togglePause. |
| `webui/src/pages/OverviewPage.vue` | Overview: hero + 6 metric cards + BuildStageTimeline + ThroughputSparkline + Runs summary. |
| `webui/src/pages/DataQualityPage.vue` | Data & quality: hero + two-column layout — left Builds/Findings/severity/role dist; right source ingestion+table/export partitions/title families/Checkpoints. |
| `webui/src/components/charts/OrgUnitProfessorChart.vue` | College subgraph: consumes `OrgUnitProfessorResponse`, force-layout, college center + professors + AFFILIATED_WITH edges. Reads `chartTheme`. |
| `webui/tests/app-sidebar.test.ts` | Renders 3 links, active state, emits refresh/togglePause. |
| `webui/tests/orgunit-professor-chart.test.ts` | Given a response renders 1 college + N professors + N edges; EmptyState when empty. |
| `tests/test_monitor_orgunit_professors.py` | Returns profs+edges; unknown org → empty; unknown build → 404; cross-org professor excluded; ETag 304. |

### Changed files

| File | Change |
|---|---|
| `webui/src/App.vue` | Drop top nav; `sidebar + main` two-column shell; hero moves into pages. |
| `webui/src/router.ts` | Add `/data` route; DashboardPage split into Overview/DataQuality. |
| `webui/src/styles/tokens.css` | Light palette (§2). |
| `webui/src/styles/base.css` | Drop dark radial gradient; light bg. |
| `webui/src/components/charts/options.ts` | Add `chartTheme` + `palette` export. |
| chart `.vue` files | Hardcoded colors → read `chartTheme`. |
| `webui/src/types/monitor.ts` | Add `OrgUnitProfessorResponse` + sub-types. |
| `webui/src/services/api.ts` | Add `orgUnitProfessors`. |
| `webui/src/pages/TopologyPage.vue` | Add college dropdown + subgraph render + back button; slim hero. |
| `src/dext_monitor/server.py` | Register `GET .../orgunit/{org_key}/professors`. |
| `src/dext_monitor/service.py` | Add `orgunit_professors`. |
| `webui/tests/routing.test.ts` | Assert 3 routes + redirect. |
| `webui/tests/topology-page.test.ts` | Two-level dropdown + drill-down render. |
| `webui/tests/app-findings.test.ts` | Mock `orgUnitProfessors` too. |

### Deleted

- `webui/src/pages/DashboardPage.vue` — replaced by Overview/DataQuality.

### Untouched

- `useMonitorData`, feature components (BuildList/FindingSummary/RunSummary/
  CheckpointTable/SourceTaskTable/PanelCard), primitives (MetricCard/StatusBadge/
  EmptyState/ErrorPanel/RefreshControl), ETag logic, all other backend endpoints.

## §5 Testing

TDD per CLAUDE.md (write failing test → RED → implement → GREEN → commit).
Frontend: vitest + jsdom. Backend: pytest (pure-logic, no LLM — monitor is
LLM-free, matches SP1–SP4 class).

### Backend

| Test | Asserts |
|---|---|
| `test_returns_professors_and_edges` | Known build+org returns professor nodes + AFFILIATED_WITH edges; professor key == edge.source |
| `test_unknown_org_returns_empty` | Valid build, nonexistent org_key → `professors: []`, `links: []` (not 404) |
| `test_unknown_build_raises` | Unknown build_id → MonitorCatalogError/404 |
| `test_cross_org_professor_excluded` | Professor A affiliated with org1; querying org2 does not return A |
| `test_etag_304` | Repeat request with If-None-Match returns 304 |

### Frontend

| Test | Asserts |
|---|---|
| `routing.test.ts` | 3 routes `/`/`/data`/`/topology` mount the right page; unknown → redirect `/` |
| `app-sidebar.test.ts` | Renders 3 router-links with correct `to`; active class; footer StatusBadge; refresh/pause emit |
| `topology-page.test.ts` | University dropdown enables college dropdown; selecting college (mock `orgUnitProfessors` resolve) renders OrgUnitProfessorChart with professor nodes; back button clears |
| `orgunit-professor-chart.test.ts` | Given response renders 1 college + N professors + N edges; empty → EmptyState |
| `app-findings.test.ts` | Updated mock adds `orgUnitProfessors` |
| existing chart tests | Still pass (color source swap doesn't change assertions) |

### Verification commands

- Backend: `uv run pytest tests/test_monitor_orgunit_professors.py -v`
- Frontend: `cd webui && npm test` (full) · `npx vitest run tests/routing.test.ts` (single)
- Type gate: `cd webui && npm run build` (vue-tsc 0 errors)

### Not tested

- Visual aesthetics (whether the light theme "looks good") — human verification.
- ECharts rendered pixels — only assert node/edge counts in `option.series[0].data`.

## Build sequence (preview)

1. Light palette: `tokens.css` + `base.css` + `options.ts` `chartTheme`; update
   chart components to read it. (Tests stay green.)
2. Sidebar + routing: `AppSidebar.vue`, `App.vue` shell, `router.ts` `/data`,
   split DashboardPage → Overview + DataQuality. Update routing test RED→GREEN.
3. Backend drill-down endpoint: `service.orgunit_professors` + `server` route,
   `test_monitor_orgunit_professors.py` RED→GREEN.
4. Frontend drill-down: `OrgUnitProfessorResponse` type + `api.orgUnitProfessors`
   + `OrgUnitProfessorChart` + TopologyPage two-level dropdown. Tests RED→GREEN.
5. Full verification: `npm test`, `npm run build`, backend pytest.

The implementation plan (writing-plans skill) will expand these into TDD steps.
