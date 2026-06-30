# Monitor sidebar routing + light restyle + topology drill-down Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape the monitor webui into a left-sidebar-routed, light-themed, 3-page app (Overview / Data & Quality / Topology) and add a university→college drill-down that renders a college's professors via a new read-only backend endpoint.

**Architecture:** `App.vue` becomes a `sidebar + <router-view>` shell. A persistent `AppSidebar.vue` owns navigation + the single StatusBadge/RefreshControl. `DashboardPage` is split into `OverviewPage` + `DataQualityPage`. Light palette + ECharts theme centralize in `tokens.css` + `options.ts`. The topology page gains a college dropdown; selecting one fetches `GET /api/monitor/builds/{build_id}/orgunit/{org_graph_key}/professors` (new `MonitorService.orgunit_professors`) and renders an `OrgUnitProfessorChart` subgraph.

**Tech Stack:** Vue 3.5 + vue-router 4 + ECharts 5 (webui); aiohttp + sqlite (dext_monitor); vitest + jsdom (frontend tests); pytest (backend tests). Python 3.11, `uv` runner.

## Global Constraints

- **TDD always:** write the failing test → run RED → implement minimally → run GREEN → **commit** one conventional commit per green step (`feat(webui): …`, `feat(monitor): …`, `test(...): …`, `refactor(...): …`).
- **Monitor is LLM-free** — no `DEEPSEEK_API_KEY` needed for any task here.
- **Read-only backend:** the new endpoint only `SELECT`s from `graph_export_rows`; never writes. It flows through the existing `error_middleware` ETag (304 support comes free).
- **Backend tests** seed a sqlite catalog by hand (see `tests/test_monitor_service.py::_write_tree_catalog` for the pattern) — no real catalog file.
- **Frontend tests** use `@vue/test-utils` `mount` + `flushPromises`; ECharts is stubbed via `vi.mock('../src/components/charts/ChartFrame.vue')` so tests assert on `option.series[0].data`, not pixels.
- **LF/CRLF warnings are benign** (Windows + Git Bash) — ignore them.
- **One conventional commit per green step.** Frontend test command: `cd webui && npx vitest run <file>`. Type gate: `cd webui && npm run build` (vue-tsc 0 errors). Backend: `uv run pytest tests/test_monitor_service.py -v`.
- **Key invariants** (from spec): professor `graph_key` == `AFFILIATED_WITH.start_graph_key` (link endpoints always resolve); college `graph_key` contains `:` so path segment must be `encodeURIComponent`'d → `%3A`; changing the university clears the college selection.

---

## File Structure

**New backend:**
- `src/dext_monitor/service.py` — add `orgunit_professors(build_id, org_graph_key)` method (reads `rel:AFFILIATED_WITH` + `node:Professor` rows).
- `src/dext_monitor/server.py` — register `GET /api/monitor/builds/{build_id}/orgunit/{org_graph_key}/professors` + `handle_orgunit_professors`.

**New frontend files:**
- `webui/src/components/layout/AppSidebar.vue` — persistent left sidebar (brand + 3 router-links + StatusBadge + RefreshControl).
- `webui/src/pages/OverviewPage.vue` — overview route (metrics + stage + throughput + runs).
- `webui/src/pages/DataQualityPage.vue` — data & quality route (builds/findings/severity/role/source/export/title/checkpoints).
- `webui/src/components/charts/OrgUnitProfessorChart.vue` — college subgraph (college center + professors + AFFILIATED_WITH).

**Modified frontend:**
- `webui/src/App.vue` — sidebar + main shell; StatusBadge/RefreshControl move to sidebar; hero moves into pages.
- `webui/src/router.ts` — add `/data` route; point `/` at OverviewPage, `/data` at DataQualityPage.
- `webui/src/styles/tokens.css` — light palette (blue accent).
- `webui/src/styles/base.css` — drop dark radial gradient.
- `webui/src/components/charts/options.ts` — add `chartTheme` + `palette` exports.
- `webui/src/components/charts/ChartFrame.vue` — `echarts.init(container, 'dark')` → `echarts.init(container)` (light).
- All chart `.vue` files — hardcoded colors → `chartTheme`/`palette`.
- `webui/src/types/monitor.ts` — add `OrgUnitProfessorResponse` + sub-types.
- `webui/src/services/api.ts` — add `orgUnitProfessors`.
- `webui/src/pages/TopologyPage.vue` — college dropdown + subgraph + back button.

**Deleted:**
- `webui/src/pages/DashboardPage.vue` — replaced by Overview/DataQuality.

**New tests:**
- `tests/test_monitor_service.py` — append `orgunit_professors` tests (reuses `_write_tree_catalog`).
- `webui/tests/app-sidebar.test.ts`, `webui/tests/orgunit-professor-chart.test.ts`.
- Update `webui/tests/routing.test.ts`, `topology-page.test.ts`, `app-findings.test.ts`.

---

## Task 1: Light palette + chart theme centralization

**Goal:** Flip the theme to light with a blue accent and route all ECharts colors through one `chartTheme`. No routing changes yet — this task is pure theming so tests stay green throughout.

**Files:**
- Modify: `webui/src/styles/tokens.css` (whole file)
- Modify: `webui/src/styles/base.css` (whole file)
- Modify: `webui/src/components/charts/options.ts` (add exports)
- Modify: `webui/src/components/charts/ChartFrame.vue:40` (`'dark'` → light)
- Modify: `webui/src/components/charts/RoleDistributionChart.vue`, `FindingSummaryChart.vue`, `SourceTaskChart.vue`, `ExportPartitionChart.vue`, `TitleFamilyChart.vue`, `ThroughputSparkline.vue`, `UniversityTopologyChart.vue` (hardcoded colors → theme)
- Test: existing `webui/tests/*.test.ts` must stay green (color-source swap changes no assertions).

**Interfaces:**
- Produces: `chartTheme` and `palette` named exports from `options.ts`, consumed by every chart component. Shape:

```ts
export const palette = ['#2f6bff', '#3ee6b5', '#f6c85f', '#ff6b7a', '#9b8cff', '#4dd0e1', '#f59e6c']
export const chartTheme = {
  text: '#5b6b82',
  axisLine: 'rgba(30,58,110,0.22)',
  splitLine: 'rgba(30,58,110,0.08)',
  label: '#1a2333',
  edge: 'rgba(30,58,110,0.30)'
}
```

- [ ] **Step 1: Write `tokens.css` light palette**

Replace the entire contents of `webui/src/styles/tokens.css` with:

```css
:root {
  color-scheme: light;
  --bg: #f6f8fc;
  --bg-strong: #eef2f8;
  --surface: #ffffff;
  --surface-strong: #ffffff;
  --surface-soft: #f1f4fa;
  --border: rgba(30, 58, 110, 0.12);
  --border-strong: rgba(30, 58, 110, 0.22);
  --text: #1a2333;
  --muted: #5b6b82;
  --subtle: #8a98ad;
  --accent: #2f6bff;
  --accent-2: #3ee6b5;
  --warning: #e0a72e;
  --danger: #e0556a;
  --success: #2fae6e;
  --radius-lg: 22px;
  --radius-md: 16px;
  --radius-sm: 10px;
  --shadow: 0 8px 24px rgba(30, 58, 110, 0.10);
  --font-ui:
    Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
    "PingFang SC", "Microsoft YaHei", sans-serif;
  --font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
}
```

- [ ] **Step 2: Write `base.css` light background**

Replace the entire contents of `webui/src/styles/base.css` with:

```css
* {
  box-sizing: border-box;
}

html,
body,
#app {
  min-height: 100%;
  margin: 0;
}

body {
  background:
    radial-gradient(circle at 12% 0%, rgba(47, 107, 255, 0.10), transparent 34rem),
    var(--bg);
  color: var(--text);
  font-family: var(--font-ui);
  line-height: 1.5;
}

button,
select,
input {
  font: inherit;
}

button {
  cursor: pointer;
}

a {
  color: inherit;
}

.mono {
  font-family: var(--font-mono);
}
```

- [ ] **Step 3: Add `chartTheme` + `palette` to `options.ts`**

Replace the entire contents of `webui/src/components/charts/options.ts` with:

```ts
import type { ComposeOption } from 'echarts/core'
import type { BarSeriesOption, GraphSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  TitleComponentOption,
  TooltipComponentOption
} from 'echarts/components'

export type ChartOption = ComposeOption<
  | BarSeriesOption
  | GraphSeriesOption
  | LineSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
  | TitleComponentOption
>

/** Shared light-theme ECharts colors. Read these instead of hardcoding hex. */
export const palette = [
  '#2f6bff', '#3ee6b5', '#f6c85f', '#ff6b7a', '#9b8cff', '#4dd0e1', '#f59e6c'
]

export const chartTheme = {
  text: '#5b6b82',
  axisLine: 'rgba(30,58,110,0.22)',
  splitLine: 'rgba(30,58,110,0.08)',
  label: '#1a2333',
  edge: 'rgba(30,58,110,0.30)'
}
```

- [ ] **Step 4: Switch ChartFrame to light init**

In `webui/src/components/charts/ChartFrame.vue`, change line 40 from:

```ts
  instance = echarts.init(container.value, 'dark')
```

to:

```ts
  instance = echarts.init(container.value)
```

- [ ] **Step 5: Re-theme `RoleDistributionChart.vue`**

Replace the `<script setup lang="ts">` block's `option` computed in `webui/src/components/charts/RoleDistributionChart.vue` so the imports + option read the theme. Replace the whole `<script setup>` section:

```ts
<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  counts: Record<string, number>
}>()

const entries = computed(() => Object.entries(props.counts))
const hasData = computed(() => entries.value.some(([, value]) => value > 0))

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'item' },
  legend: {
    bottom: 0,
    textStyle: { color: chartTheme.text }
  },
  series: [
    {
      type: 'pie',
      radius: ['48%', '70%'],
      center: ['50%', '44%'],
      data: entries.value.map(([name, value]) => ({ name, value })),
      color: palette,
      label: { color: chartTheme.label }
    }
  ]
}))
</script>
```

- [ ] **Step 6: Re-theme `FindingSummaryChart.vue`**

In `webui/src/components/charts/FindingSummaryChart.vue` add the import and replace hardcoded colors. The resulting `<script setup>`:

```ts
<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  counts: Record<string, number>
}>()

const entries = computed(() =>
  Object.entries(props.counts)
    .filter(([, value]) => value > 0)
    .sort((a, b) => b[1] - a[1])
)
const hasData = computed(() => entries.value.length > 0)

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'item' },
  legend: {
    bottom: 0,
    textStyle: { color: chartTheme.text }
  },
  series: [
    {
      type: 'pie',
      radius: ['48%', '70%'],
      center: ['50%', '44%'],
      data: entries.value.map(([name, value]) => ({ name, value })),
      color: palette,
      label: { color: chartTheme.label }
    }
  ]
}))
</script>
```

> **Note:** confirm the existing `entries`/`hasData` logic matches the above before overwriting — if the current file filters differently, preserve its filter and only swap the color literals (`#92a4bd`→`chartTheme.text`, `['#ff6b7a',...]`→`palette`, `#e8f0ff`→`chartTheme.label`). Read the file first; the goal is "hardcoded colors → theme", not a logic rewrite.

- [ ] **Step 7: Re-theme `SourceTaskChart.vue`**

In `webui/src/components/charts/SourceTaskChart.vue`, add `import { chartTheme, palette } from './options'` and replace:
- `textStyle: { color: '#92a4bd' }` → `textStyle: { color: chartTheme.text }`
- `axisLabel: { color: '#92a4bd' }` → `axisLabel: { color: chartTheme.text }`
- `axisLine: { lineStyle: { color: 'rgba(136,162,199,0.28)' } }` → `axisLine: { lineStyle: { color: chartTheme.axisLine } }`
- `splitLine: { lineStyle: { color: 'rgba(136,162,199,0.12)' } }` → `splitLine: { lineStyle: { color: chartTheme.splitLine } }`
- `itemStyle: { color: '#70a7ff', borderRadius: [6, 6, 0, 0] }` → `itemStyle: { color: palette[0], borderRadius: [6, 6, 0, 0] }`
- `itemStyle: { color: '#3ee6b5', borderRadius: [6, 6, 0, 0] }` → `itemStyle: { color: palette[1], borderRadius: [6, 6, 0, 0] }`

- [ ] **Step 8: Re-theme `ExportPartitionChart.vue`**

In `webui/src/components/charts/ExportPartitionChart.vue`, add `import { chartTheme, palette } from './options'` and replace:
- `axisLabel: { color: '#92a4bd' }` (both x and y) → `chartTheme.text`
- `splitLine: { lineStyle: { color: 'rgba(136,162,199,0.12)' } }` → `chartTheme.splitLine`
- `axisLine: { lineStyle: { color: 'rgba(136,162,199,0.28)' } }` → `chartTheme.axisLine`
- The `itemStyle.color` function `sorted.value[params.dataIndex]?.row_kind === 'node' ? '#3ee6b5' : '#70a7ff'` → `... === 'node' ? palette[1] : palette[0]`

- [ ] **Step 9: Re-theme `TitleFamilyChart.vue`**

In `webui/src/components/charts/TitleFamilyChart.vue`, add `import { chartTheme, palette } from './options'` and replace:
- `axisLabel: { color: '#92a4bd' }` → `chartTheme.text`
- `splitLine: { lineStyle: { color: 'rgba(136,162,199,0.12)' } }` → `chartTheme.splitLine`
- `axisLine: { lineStyle: { color: 'rgba(136,162,199,0.28)' } }` → `chartTheme.axisLine`
- `itemStyle: { color: '#70a7ff', ... }` → `itemStyle: { color: palette[0], ... }`

- [ ] **Step 10: Re-theme `ThroughputSparkline.vue`**

In `webui/src/components/charts/ThroughputSparkline.vue`, add `import { chartTheme, palette } from './options'` and replace:
- `textStyle: { color: '#92a4bd' }` → `chartTheme.text`
- `axisLabel: { color: '#92a4bd', ... }` → `chartTheme.text`
- `axisLine: { lineStyle: { color: 'rgba(136,162,199,0.28)' } }` → `chartTheme.axisLine`
- `splitLine: { lineStyle: { color: 'rgba(136,162,199,0.12)' } }` → `chartTheme.splitLine`
- rows series: `lineStyle: { color: '#70a7ff', width: 2 }` + `itemStyle: { color: '#70a7ff' }` → `palette[0]`
- observations series: `lineStyle: { color: '#3ee6b5', width: 2 }` + `itemStyle: { color: '#3ee6b5' }` → `palette[1]`

- [ ] **Step 11: Re-theme `UniversityTopologyChart.vue` colors**

In `webui/src/components/charts/UniversityTopologyChart.vue`, add `import { chartTheme, palette } from './options'` to the script. Replace in the `option` computed:
- `legend: { top: 0, textStyle: { color: '#92a4bd' } }` → `chartTheme.text`
- label `color: '#e8f0ff'` → `chartTheme.label`
- `lineStyle: { color: 'rgba(146,164,189,0.55)', curveness: 0.18 }` → `lineStyle: { color: chartTheme.edge, curveness: 0.18 }`
- `itemStyle: { color: '#3ee6b5' }` → `itemStyle: { color: (params: { category: number }) => palette[params.category % palette.length] }`

Also update the scoped `<style>`: the select focus `box-shadow: 0 0 0 2px rgba(62, 230, 181, 0.4)` → `rgba(47, 107, 255, 0.35)` and `border-color: rgba(62, 230, 181, 0.42)` → `var(--accent)`.

- [ ] **Step 12: Run the frontend test suite + type gate**

Run: `cd webui && npm test && npm run build`
Expected: all tests PASS; `vue-tsc` reports 0 errors. (Color-source swaps do not change any assertion; chart tests assert on data, not colors. `routing.test.ts` still mounts `DashboardPage` — it stays green because we have not touched routing yet.)

- [ ] **Step 13: Commit**

```bash
git add webui/src/styles/tokens.css webui/src/styles/base.css webui/src/components/charts/
git commit -m "refactor(webui): light palette + centralize ECharts theme in options.ts"
```

---

## Task 2: Sidebar shell + 3-route split

**Goal:** Replace the top nav with a persistent left sidebar, split `DashboardPage` into `OverviewPage` + `DataQualityPage`, and add the `/data` route. StatusBadge + RefreshControl move into the sidebar (single source). After this task the dashboard content is unchanged — only its container + routing change.

**Files:**
- Create: `webui/src/components/layout/AppSidebar.vue`
- Create: `webui/src/pages/OverviewPage.vue`
- Create: `webui/src/pages/DataQualityPage.vue`
- Modify: `webui/src/App.vue` (whole file)
- Modify: `webui/src/router.ts` (whole file)
- Delete: `webui/src/pages/DashboardPage.vue` (last step, after Overview/DataQuality exist)
- Test: rewrite `webui/tests/routing.test.ts`; create `webui/tests/app-sidebar.test.ts`.

**Interfaces:**
- Consumes: the props that `App.vue` currently passes to `<router-view>` (`health`, `builds`, `detail`, `metrics`, `topology`, `findings`, `selectedBuildId`, `error`, `paused`, `lastUpdated`, `history`) + emits `refresh`/`togglePause`/`selectBuild`.
- Produces: `OverviewPage` and `DataQualityPage` accept the SAME prop/emits surface the old `DashboardPage` did (so `App.vue`'s `<router-view>` binding is unchanged). `AppSidebar` accepts `{ health, paused, lastUpdated }` and emits `refresh`/`togglePause`.

- [ ] **Step 1: Write the failing routing test (RED)**

Replace `webui/tests/routing.test.ts` entirely with:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  builds: vi.fn(),
  buildDetail: vi.fn(),
  metrics: vi.fn(),
  universityTopology: vi.fn(),
  findings: vi.fn()
}))

vi.mock('../src/services/api', () => ({ monitorApi: apiMocks }))

import App from '../src/App.vue'
import router from '../src/router'
import OverviewPage from '../src/pages/OverviewPage.vue'
import DataQualityPage from '../src/pages/DataQualityPage.vue'
import TopologyPage from '../src/pages/TopologyPage.vue'

function buildRow(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'build-1',
    status: 'WRITING_VECTOR',
    started_at: '2026-06-29T00:00:00+00:00',
    finished_at: null,
    last_error: null,
    summary: {
      source_count: 1, rows_read: 0, observations_written: 0, documents_seen: 0,
      canonical_active: 0, unresolved_findings: 0, graph_export_rows: 0
    },
    is_active: true,
    stage: [],
    ...overrides
  }
}

async function mountAt(path: string) {
  router.push(path)
  await router.isReady()
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  await nextTick()
  return wrapper
}

describe('monitor routing', () => {
  beforeEach(() => {
    apiMocks.health.mockResolvedValue({ catalog_path: 'p', server_time: 0, readable: true, schema_version: 3 })
    apiMocks.builds.mockResolvedValue({ catalog_path: 'p', schema_version: 3, builds: [buildRow()], latest_build_id: 'build-1' })
    apiMocks.buildDetail.mockResolvedValue({
      catalog_path: 'p', build: buildRow(), sources: [], checkpoints: [],
      export_partitions: [], unresolved_findings: {}, curation: null, graph: null
    })
    apiMocks.metrics.mockResolvedValue({
      build_id: 'build-1', stage: [], source_status_counts: {}, finding_counts: {},
      role_counts: {}, title_family_counts: {}, export_partitions: [], observations_by_source: []
    })
    apiMocks.universityTopology.mockResolvedValue({ build_id: 'build-1', universities: [], nodes: [], links: [] })
    apiMocks.findings.mockResolvedValue({ findings: [], limit: 100 })
  })
  afterEach(() => { vi.clearAllMocks() })

  it('renders OverviewPage at /', async () => {
    const wrapper = await mountAt('/')
    expect(wrapper.findComponent(OverviewPage).exists()).toBe(true)
  })

  it('renders DataQualityPage at /data', async () => {
    const wrapper = await mountAt('/data')
    expect(wrapper.findComponent(DataQualityPage).exists()).toBe(true)
  })

  it('renders TopologyPage at /topology', async () => {
    const wrapper = await mountAt('/topology')
    expect(wrapper.findComponent(TopologyPage).exists()).toBe(true)
  })

  it('redirects unknown paths to /', async () => {
    const wrapper = await mountAt('/does-not-exist')
    expect(wrapper.findComponent(OverviewPage).exists()).toBe(true)
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd webui && npx vitest run tests/routing.test.ts`
Expected: FAIL — `OverviewPage` / `DataQualityPage` modules do not exist yet (import errors).

- [ ] **Step 3: Create `OverviewPage.vue`**

Create `webui/src/pages/OverviewPage.vue`. It reuses the existing feature/chart components and takes the same props/emits DashboardPage did, but renders ONLY the overview panels (metrics + build stage + throughput + runs):

```vue
<script setup lang="ts">
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
import { formatNumber, shortId } from '../utils/format'
import type { ThroughputSample } from '../composables/useMonitorData'
import BuildStageTimeline from '../components/charts/BuildStageTimeline.vue'
import ThroughputSparkline from '../components/charts/ThroughputSparkline.vue'
import PanelCard from '../components/features/PanelCard.vue'
import RunSummary from '../components/features/RunSummary.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'
import MetricCard from '../components/primitives/MetricCard.vue'

defineProps<{
  health: HealthResponse | null
  builds: BuildsResponse | null
  detail: BuildDetailResponse | null
  metrics: MetricsResponse | null
  topology: UniversityTopologyResponse | null
  findings: Finding[]
  selectedBuildId: string | null
  error: string | null
  paused: boolean
  lastUpdated: Date | null
  history: ThroughputSample[]
}>()

defineEmits<{
  refresh: []
  togglePause: []
  selectBuild: [buildId: string]
}>()
</script>

<template>
  <main class="page">
    <header class="hero">
      <div>
        <div class="brand-row"><span class="brand-mark">dx</span><span>dext monitor</span></div>
        <h1>Overview</h1>
        <p>Build health, pipeline stage, throughput and run progress at a glance.</p>
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <EmptyState
      v-if="builds && builds.builds.length === 0"
      title="No catalog builds"
      message="Run a graph build first, then refresh this monitor."
    />

    <template v-if="detail && metrics">
      <section class="metric-grid">
        <MetricCard label="Build status" :value="detail.build.status" tone="accent" :hint="shortId(detail.build.id)" />
        <MetricCard label="Sources" :value="formatNumber(detail.build.summary.source_count)" />
        <MetricCard label="Canonical professors" :value="formatNumber(detail.build.summary.canonical_active)" tone="accent" />
        <MetricCard
          label="Unresolved findings"
          :value="formatNumber(detail.build.summary.unresolved_findings)"
          :tone="detail.build.summary.unresolved_findings ? 'warning' : 'default'"
        />
        <MetricCard label="Rows read" :value="formatNumber(detail.build.summary.rows_read)" />
        <MetricCard label="Graph export rows" :value="formatNumber(detail.build.summary.graph_export_rows)" tone="accent" />
      </section>

      <section class="content">
        <PanelCard title="Build stage" subtitle="Current phase and completed pipeline steps">
          <div class="panel-body">
            <BuildStageTimeline :stages="metrics.stage" />
          </div>
        </PanelCard>

        <PanelCard title="Throughput" subtitle="Rows read and observations written over recent polls">
          <div class="panel-body">
            <ThroughputSparkline :history="history" />
          </div>
        </PanelCard>

        <PanelCard title="Runs" subtitle="Curation and graph materialization">
          <div class="run-grid panel-body">
            <RunSummary label="Curation" :run="detail.curation" />
            <RunSummary label="Graph" :run="detail.graph" />
          </div>
        </PanelCard>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page {
  width: min(1680px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 1.2rem 0 2.5rem;
}
.hero { padding: 1.2rem 0 1.4rem; }
.brand-row {
  display: flex; align-items: center; gap: 0.7rem;
  color: var(--accent); font-size: 0.86rem; font-weight: 900;
  letter-spacing: 0.08em; text-transform: uppercase;
}
.brand-mark {
  display: inline-grid; place-items: center; width: 36px; height: 36px;
  border: 1px solid var(--border-strong); border-radius: 12px;
  background: var(--surface-soft); color: var(--text);
}
h1 {
  max-width: 760px; margin: 0.7rem 0 0;
  font-size: clamp(2rem, 4vw, 3.4rem); line-height: 0.98; letter-spacing: -0.05em;
}
.hero p { max-width: 720px; margin: 0.9rem 0 0; color: var(--muted); font-size: 1rem; }
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
  gap: 0.9rem; margin-bottom: 1rem;
}
.content { display: grid; gap: 1rem; }
.run-grid { display: grid; gap: 0.8rem; }
:deep(.panel-body) { min-width: 0; overflow: hidden; }
@media (max-width: 760px) {
  .page { width: min(100% - 1rem, 1680px); }
  .metric-grid { grid-template-columns: 1fr; }
}
</style>
```

- [ ] **Step 4: Create `DataQualityPage.vue`**

Create `webui/src/pages/DataQualityPage.vue` with the remaining panels (builds / findings / severity / role / source ingestion / export partitions / title families / checkpoints):

```vue
<script setup lang="ts">
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
import type { ThroughputSample } from '../composables/useMonitorData'
import ExportPartitionChart from '../components/charts/ExportPartitionChart.vue'
import FindingSummaryChart from '../components/charts/FindingSummaryChart.vue'
import RoleDistributionChart from '../components/charts/RoleDistributionChart.vue'
import SourceTaskChart from '../components/charts/SourceTaskChart.vue'
import TitleFamilyChart from '../components/charts/TitleFamilyChart.vue'
import BuildList from '../components/features/BuildList.vue'
import CheckpointTable from '../components/features/CheckpointTable.vue'
import FindingSummary from '../components/features/FindingSummary.vue'
import PanelCard from '../components/features/PanelCard.vue'
import SourceTaskTable from '../components/features/SourceTaskTable.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'

defineProps<{
  health: HealthResponse | null
  builds: BuildsResponse | null
  detail: BuildDetailResponse | null
  metrics: MetricsResponse | null
  topology: UniversityTopologyResponse | null
  findings: Finding[]
  selectedBuildId: string | null
  error: string | null
  paused: boolean
  lastUpdated: Date | null
  history: ThroughputSample[]
}>()

defineEmits<{
  refresh: []
  togglePause: []
  selectBuild: [buildId: string]
}>()
</script>

<template>
  <main class="page">
    <header class="hero">
      <div>
        <div class="brand-row"><span class="brand-mark">dx</span><span>dext monitor</span></div>
        <h1>Data &amp; quality</h1>
        <p>Source ingestion, findings, entity roles, export partitions and checkpoints.</p>
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <EmptyState
      v-if="builds && builds.builds.length === 0"
      title="No catalog builds"
      message="Run a graph build first, then refresh this monitor."
    />

    <template v-if="detail && metrics">
      <section class="layout">
        <aside class="sidebar">
          <PanelCard title="Builds" subtitle="Recent catalog builds">
            <div class="panel-body">
              <BuildList
                :builds="builds?.builds ?? []"
                :selected-build-id="selectedBuildId"
                @select="$emit('selectBuild', $event)"
              />
            </div>
          </PanelCard>
          <PanelCard title="Findings" subtitle="Unresolved quality signals">
            <FindingSummary :counts="detail.unresolved_findings" :findings="findings" />
          </PanelCard>
          <PanelCard title="Finding severity" subtitle="Open findings only">
            <div class="panel-body">
              <FindingSummaryChart :counts="detail.unresolved_findings" />
            </div>
          </PanelCard>
          <PanelCard title="Role distribution" subtitle="Canonical entity roles">
            <div class="panel-body">
              <RoleDistributionChart :counts="metrics.role_counts ?? {}" />
            </div>
          </PanelCard>
        </aside>

        <section class="content">
          <PanelCard title="Source ingestion" subtitle="Rows and observation throughput by university">
            <SourceTaskChart :sources="metrics.observations_by_source" />
            <SourceTaskTable :sources="detail.sources" />
          </PanelCard>

          <section class="split">
            <PanelCard title="Export partitions" subtitle="Largest node/relationship partitions">
              <div class="panel-body">
                <ExportPartitionChart :partitions="metrics.export_partitions" />
              </div>
            </PanelCard>
            <PanelCard title="Title families" subtitle="Top normalized title families">
              <div class="panel-body">
                <TitleFamilyChart :counts="metrics.title_family_counts ?? {}" />
              </div>
            </PanelCard>
          </section>

          <PanelCard title="Checkpoints" subtitle="Read-only sink progress checkpoints">
            <CheckpointTable :checkpoints="detail.checkpoints" />
          </PanelCard>
        </section>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page {
  width: min(1680px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 1.2rem 0 2.5rem;
}
.hero { padding: 1.2rem 0 1.4rem; }
.brand-row {
  display: flex; align-items: center; gap: 0.7rem;
  color: var(--accent); font-size: 0.86rem; font-weight: 900;
  letter-spacing: 0.08em; text-transform: uppercase;
}
.brand-mark {
  display: inline-grid; place-items: center; width: 36px; height: 36px;
  border: 1px solid var(--border-strong); border-radius: 12px;
  background: var(--surface-soft); color: var(--text);
}
h1 {
  max-width: 760px; margin: 0.7rem 0 0;
  font-size: clamp(2rem, 4vw, 3.4rem); line-height: 0.98; letter-spacing: -0.05em;
}
.hero p { max-width: 720px; margin: 0.9rem 0 0; color: var(--muted); font-size: 1rem; }
.layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  gap: 1rem; align-items: start;
}
.layout > * { min-width: 0; }
.sidebar, .content { display: grid; gap: 1rem; min-width: 0; }
.split {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr);
  gap: 1rem;
}
.split > * { min-width: 0; }
:deep(.panel-body) { min-width: 0; overflow: hidden; }
@media (max-width: 1180px) {
  .layout, .split { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .page { width: min(100% - 1rem, 1680px); }
}
</style>
```

- [ ] **Step 5: Create `AppSidebar.vue`**

Create `webui/src/components/layout/AppSidebar.vue`:

```vue
<script setup lang="ts">
import type { HealthResponse } from '../../types/monitor'
import StatusBadge from '../primitives/StatusBadge.vue'
import RefreshControl from '../primitives/RefreshControl.vue'

defineProps<{
  health: HealthResponse | null
  paused: boolean
  lastUpdated: Date | null
}>()

defineEmits<{
  refresh: []
  togglePause: []
}>()
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-mark">dx</span>
      <div class="brand-text">
        <strong>dext</strong>
        <span>monitor</span>
      </div>
    </div>

    <nav class="nav">
      <router-link to="/" class="nav-link">概览</router-link>
      <router-link to="/data" class="nav-link">数据与质量</router-link>
      <router-link to="/topology" class="nav-link">拓扑图</router-link>
    </nav>

    <div class="footer">
      <StatusBadge :status="health?.readable ? 'catalog_readable' : 'catalog_unavailable'" />
      <RefreshControl
        :paused="paused"
        :last-updated="lastUpdated"
        @refresh="$emit('refresh')"
        @toggle-pause="$emit('togglePause')"
      />
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  gap: 1.4rem;
  width: 220px;
  min-height: 100vh;
  padding: 1.4rem 1rem;
  background: var(--surface);
  border-right: 1px solid var(--border);
  position: sticky;
  top: 0;
}
.brand {
  display: flex; align-items: center; gap: 0.6rem;
  padding: 0 0.3rem;
}
.brand-mark {
  display: inline-grid; place-items: center;
  width: 34px; height: 34px;
  border: 1px solid var(--border-strong); border-radius: 10px;
  background: var(--surface-soft); color: var(--accent);
  font-weight: 900; font-size: 0.8rem;
}
.brand-text { display: flex; flex-direction: column; line-height: 1.1; }
.brand-text strong { font-size: 0.95rem; color: var(--text); }
.brand-text span { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
.nav { display: flex; flex-direction: column; gap: 0.25rem; }
.nav-link {
  padding: 0.55rem 0.8rem;
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 0.86rem;
  font-weight: 600;
  text-decoration: none;
  transition: color 0.15s, background 0.15s;
}
.nav-link:hover { color: var(--text); background: var(--surface-soft); }
.nav-link.router-link-active {
  color: var(--accent);
  background: rgba(47, 107, 255, 0.10);
}
.footer {
  margin-top: auto;
  display: grid;
  gap: 0.7rem;
  align-content: end;
}
@media (max-width: 760px) {
  .sidebar {
    position: static;
    flex-direction: row;
    width: 100%;
    min-height: auto;
    align-items: center;
    gap: 0.8rem;
    padding: 0.7rem 1rem;
    overflow-x: auto;
  }
  .nav { flex-direction: row; }
  .footer { margin-top: 0; grid-auto-flow: column; }
}
</style>
```

- [ ] **Step 6: Rewrite `App.vue`**

Replace the entire contents of `webui/src/App.vue` with:

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { monitorApi } from './services/api'
import { useMonitorData } from './composables/useMonitorData'
import AppSidebar from './components/layout/AppSidebar.vue'
import type { Finding } from './types/monitor'

const {
  health,
  builds,
  selectedBuildId,
  activeBuildId,
  detail,
  metrics,
  topology,
  error,
  paused,
  lastUpdated,
  history,
  refresh,
  selectBuild,
  togglePause
} = useMonitorData()

const findings = ref<Finding[]>([])

const findingsSignature = computed(() => {
  const build = detail.value?.build
  if (!build) return ''
  const severities = Object.keys(detail.value?.unresolved_findings ?? {}).sort().join(',')
  return `${build.id}|${severities}`
})

watch(
  findingsSignature,
  async () => {
    const buildId = detail.value?.build.id
    if (!buildId) {
      findings.value = []
      return
    }
    try {
      findings.value = (await monitorApi.findings(buildId)).findings
    } catch {
      findings.value = []
    }
  },
  { immediate: true }
)

const selected = computed(() => selectedBuildId.value)
</script>

<template>
  <div class="app-shell">
    <AppSidebar
      :health="health"
      :paused="paused"
      :last-updated="lastUpdated"
      @refresh="refresh"
      @toggle-pause="togglePause"
    />
    <router-view v-slot="{ Component }">
      <component
        :is="Component"
        :health="health"
        :builds="builds"
        :detail="detail"
        :metrics="metrics"
        :topology="topology"
        :findings="findings"
        :selected-build-id="selected"
        :error="error"
        :paused="paused"
        :last-updated="lastUpdated"
        :history="history"
        @refresh="refresh"
        @toggle-pause="togglePause"
        @select-build="selectBuild"
      />
    </router-view>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100%;
  align-items: flex-start;
}
.app-shell > :deep(router-view) {
  flex: 1;
  min-width: 0;
}
@media (max-width: 760px) {
  .app-shell { flex-direction: column; }
}
</style>
```

- [ ] **Step 7: Rewrite `router.ts`**

Replace the entire contents of `webui/src/router.ts` with:

```ts
import { createRouter, createWebHistory } from 'vue-router'
import OverviewPage from './pages/OverviewPage.vue'
import DataQualityPage from './pages/DataQualityPage.vue'
import TopologyPage from './pages/TopologyPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: OverviewPage },
    { path: '/data', name: 'data', component: DataQualityPage },
    { path: '/topology', name: 'topology', component: TopologyPage },
    // Unknown paths fall back to the overview (KISS: no separate 404 page).
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
```

- [ ] **Step 8: Run routing test (GREEN)**

Run: `cd webui && npx vitest run tests/routing.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 9: Write the failing sidebar test (RED)**

Create `webui/tests/app-sidebar.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'

import AppSidebar from '../src/components/layout/AppSidebar.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<div />' } },
      { path: '/data', name: 'data', component: { template: '<div />' } },
      { path: '/topology', name: 'topology', component: { template: '<div />' } }
    ]
  })
}

const baseProps = {
  health: null,
  paused: false,
  lastUpdated: null
}

describe('AppSidebar', () => {
  it('renders three nav links pointing at the three routes', async () => {
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: baseProps,
      global: { plugins: [router] }
    })
    await nextTick()
    const links = wrapper.findAll('.nav-link')
    expect(links).toHaveLength(3)
    expect(links[0].attributes('href')).toBe('/')
    expect(links[1].attributes('href')).toBe('/data')
    expect(links[2].attributes('href')).toBe('/topology')
  })

  it('marks the active route with router-link-active', async () => {
    const router = makeRouter()
    router.push('/data')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: baseProps,
      global: { plugins: [router] }
    })
    await nextTick()
    const active = wrapper.findAll('.nav-link.router-link-active')
    expect(active).toHaveLength(1)
    expect(active[0].text()).toBe('数据与质量')
  })

  it('emits refresh and toggle-pause from the footer RefreshControl', async () => {
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: baseProps,
      global: { plugins: [router] }
    })
    await nextTick()
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('refresh')).toBeTruthy()
    expect(wrapper.emitted('togglePause')).toBeTruthy()
  })
})
```

- [ ] **Step 10: Run it to verify it fails**

Run: `cd webui && npx vitest run tests/app-sidebar.test.ts`
Expected: FAIL until `AppSidebar` exists — but it was created in Step 5, so if Step 5 landed this may already pass. If it FAILS, read the failure (likely the RefreshControl button order) and adjust the button index, not the component.

- [ ] **Step 11: Run sidebar test (GREEN)**

Run: `cd webui && npx vitest run tests/app-sidebar.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 12: Delete `DashboardPage.vue`**

```bash
git rm webui/src/pages/DashboardPage.vue
```

- [ ] **Step 13: Run the full suite + type gate**

Run: `cd webui && npm test && npm run build`
Expected: all tests PASS; `vue-tsc` 0 errors. (Note: `app-findings.test.ts` mounts `App` at `/` — it will now render OverviewPage; the `apiMocks` do not include `orgUnitProfessors`, which is fine because OverviewPage does not call it. Only the topology page calls it, and that test does not navigate to `/topology`.)

- [ ] **Step 14: Commit**

```bash
git add webui/src/App.vue webui/src/router.ts webui/src/pages/ webui/src/components/layout/ webui/tests/routing.test.ts webui/tests/app-sidebar.test.ts
git commit -m "feat(webui): sidebar shell + 3-route split (overview/data/topology)"
```

---

## Task 3: Backend drill-down endpoint

**Goal:** Add `GET /api/monitor/builds/{build_id}/orgunit/{org_graph_key}/professors` returning a college's professors + AFFILIATED_WITH edges. Pure read-only SQL; ETag/304 comes free from `error_middleware`.

**Files:**
- Modify: `src/dext_monitor/service.py` (add `orgunit_professors`)
- Modify: `src/dext_monitor/server.py` (add handler + route)
- Test: `tests/test_monitor_service.py` (append tests + a richer seed helper)

**Interfaces:**
- Consumes: `CatalogReader.connect()`, `require_supported_schema()`, `_get_build()` (all existing). Reads `graph_export_rows` partitions `node:Professor`, `rel:AFFILIATED_WITH`, `node:OrgUnit`.
- Produces: `MonitorService.orgunit_professors(build_id: str, org_graph_key: str) -> dict[str, Any]` returning `{build_id, orgunit, professors, links}`. HTTP route `GET /api/monitor/builds/{build_id}/orgunit/{org_graph_key}/professors`.

- [ ] **Step 1: Write the failing backend tests (RED)**

Append to `tests/test_monitor_service.py` (after the existing `test_graph_tree_returns_complete_university_orgunit_tree` and its helpers). First add a seed helper that extends the tree catalog with `node:Professor` rows, then four tests:

```python
def _write_professor_catalog(path: Path) -> str:
    """Like ``_write_tree_catalog`` but also seeds ``node:Professor`` rows so
    ``orgunit_professors`` has real professor payloads to return. Professors
    p1, p2 are affiliated with org (college under 大学A); p3 with org2."""
    build_id = _write_tree_catalog(path)
    connection = sqlite3.connect(path)
    try:
        prof_rows = [
            ("node:Professor", "p1", "node", "Professor", None, None,
             '{"graph_key":"p1","name":"张三","title":"教授","title_family":"教授","role_status":"active"}'),
            ("node:Professor", "p2", "node", "Professor", None, None,
             '{"graph_key":"p2","name":"李四","title":"副教授","title_family":"副教授","role_status":"active"}'),
            ("node:Professor", "p3", "node", "Professor", None, None,
             '{"graph_key":"p3","name":"王五","title":"讲师","title_family":"讲师","role_status":"active"}'),
        ]
        for row in prof_rows:
            connection.execute(
                "INSERT INTO graph_export_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'p', 'c')",
                (build_id, *row),
            )
        connection.execute(
            "INSERT INTO graph_export_partitions VALUES (?, ?, 'node', 'Professor', ?, NULL, NULL, 'checksum', 'now')",
            (build_id, "node:Professor", 3),
        )
        connection.commit()
        return build_id
    finally:
        connection.close()


def test_orgunit_professors_returns_professors_and_edges(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.orgunit_professors(build_id, "org")
    assert set(result.keys()) == {"build_id", "orgunit", "professors", "links"}
    assert result["build_id"] == build_id
    assert result["orgunit"]["graph_key"] == "org"
    assert result["orgunit"]["label"] == "学院A1"
    assert result["orgunit"]["kind"] == "college"

    profs = {p["graph_key"]: p for p in result["professors"]}
    assert set(profs) == {"p1", "p2"}  # p3 is affiliated with org2, excluded
    assert profs["p1"]["name"] == "张三"
    assert profs["p1"]["title"] == "教授"

    assert len(result["links"]) == 2
    for link in result["links"]:
        assert link["label"] == "AFFILIATED_WITH"
        assert link["target"] == "org"
        assert link["source"] in profs  # endpoint resolves to a returned professor


def test_orgunit_professors_unknown_org_returns_empty(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.orgunit_professors(build_id, "no-such-org")
    assert result["professors"] == []
    assert result["links"] == []
    assert result["orgunit"]["graph_key"] == "no-such-org"


def test_orgunit_professors_cross_org_professor_excluded(tmp_path: Path) -> None:
    """A professor affiliated with org1 must NOT appear under org2."""
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.orgunit_professors(build_id, "org2")
    profs = {p["graph_key"] for p in result["professors"]}
    assert profs == {"p3"}  # only p3 is affiliated with org2


def test_orgunit_professors_unknown_build_raises(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))
    with pytest.raises(MonitorCatalogError, match="unknown build ID"):
        service.orgunit_professors("does-not-exist", "org")
```

Add `MonitorCatalogError` to the imports at the top of `tests/test_monitor_service.py` if not already imported:

```python
from dext_monitor.catalog_reader import MonitorCatalogError
```

(Check the existing imports first — `MonitorCatalogError` may already be imported via `dext_monitor.catalog_reader`; if so, skip this.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_monitor_service.py -k orgunit_professors -v`
Expected: FAIL — `MonitorService` has no attribute `orgunit_professors` (AttributeError).

- [ ] **Step 3: Implement `orgunit_professors` in `service.py`**

Add this method to the `MonitorService` class in `src/dext_monitor/service.py` (place it immediately after the existing `graph_tree` method, before `_get_build`):

```python
    def orgunit_professors(self, build_id: str, org_graph_key: str) -> dict[str, Any]:
        """Professors affiliated with one OrgUnit + their AFFILIATED_WITH edges.

        ``org_graph_key`` is the OrgUnit ``id`` returned by ``graph_tree``
        (``payload.graph_key``). Reads ``rel:AFFILIATED_WITH`` to find professor
        keys whose affiliation ends at this org, then ``node:Professor`` payloads
        for their names/titles. Empty result for an unknown org is legal.
        """
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            self._get_build(connection, build_id)
            affiliated_rows = list(
                connection.execute(
                    "SELECT start_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:AFFILIATED_WITH' "
                    "AND end_graph_key=?",
                    (build_id, org_graph_key),
                )
            )
            professor_keys = {str(row["start_graph_key"]) for row in affiliated_rows}

            org_row = connection.execute(
                "SELECT payload_json FROM graph_export_rows "
                "WHERE build_id=? AND partition_key='node:OrgUnit' AND row_key=?",
                (build_id, org_graph_key),
            ).fetchone()
            org_payload = json_loads(org_row["payload_json"], {}) if org_row else {}
            org_to_univ = self._org_to_university(connection, build_id)
            orgunit = {
                "graph_key": org_graph_key,
                "label": str(org_payload.get("name") or org_graph_key),
                "kind": str(org_payload.get("kind") or ""),
                "university": org_to_univ.get(org_graph_key, ""),
            }

            professors: list[dict[str, Any]] = []
            if professor_keys:
                placeholders = ",".join("?" for _ in professor_keys)
                prof_rows = connection.execute(
                    f"SELECT payload_json FROM graph_export_rows "
                    f"WHERE build_id=? AND partition_key='node:Professor' "
                    f"AND row_key IN ({placeholders})",
                    (build_id, *professor_keys),
                )
                for row in prof_rows:
                    payload = json_loads(row["payload_json"], {})
                    graph_key = str(payload.get("graph_key") or payload.get("id") or "")
                    professors.append(
                        {
                            "graph_key": graph_key,
                            "name": str(payload.get("name") or graph_key),
                            "title": payload.get("title"),
                            "title_family": payload.get("title_family"),
                            "role_status": str(payload.get("role_status") or ""),
                        }
                    )
            professors.sort(key=lambda p: p["name"])

        links = [
            {"source": p["graph_key"], "target": org_graph_key, "label": "AFFILIATED_WITH"}
            for p in professors
        ]
        return {
            "build_id": build_id,
            "orgunit": orgunit,
            "professors": professors,
            "links": links,
        }
```

- [ ] **Step 4: Add the `_org_to_university` helper**

The method above calls `self._org_to_university(...)`. Add this small helper method to `MonitorService` (next to `orgunit_professors`). It rebuilds the org→university map from `PART_OF` edges (mirroring `graph_tree`'s logic):

```python
    def _org_to_university(self, connection, build_id: str) -> dict[str, str]:
        rows = connection.execute(
            "SELECT start_graph_key, end_graph_key FROM graph_export_rows "
            "WHERE build_id=? AND partition_key='rel:PART_OF'",
            (build_id,),
        )
        return {str(r["start_graph_key"]): str(r["end_graph_key"]) for r in rows}
```

- [ ] **Step 5: Run the service tests (GREEN)**

Run: `uv run pytest tests/test_monitor_service.py -k orgunit_professors -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Register the HTTP route**

In `src/dext_monitor/server.py`, add a handler near `handle_graph_tree`:

```python
async def handle_orgunit_professors(request: web.Request) -> web.Response:
    return json_response(
        {
            "data": service(request).orgunit_professors(
                request.match_info["build_id"],
                request.match_info["org_graph_key"],
            )
        }
    )
```

And in `create_app`'s `app.add_routes([...])` list, add (after the `graph-tree` route):

```python
            web.get(
                f"{API_PREFIX}/builds/{{build_id}}/orgunit/{{org_graph_key}}/professors",
                handle_orgunit_professors,
            ),
```

- [ ] **Step 7: Add an HTTP-level test (route + ETag 304)**

Append to `tests/test_monitor_service.py` (the file already has an async aiohttp test using `TestClient`/`TestServer` — follow that pattern). Find the existing `test_monitor_aiohttp_api` test and add a new one alongside it:

```python
@pytest.mark.asyncio
async def test_monitor_orgunit_professors_endpoint(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    app = create_app(_settings(catalog))
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        url = f"/api/monitor/builds/{build_id}/orgunit/org/professors"
        resp = await client.get(url)
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["orgunit"]["graph_key"] == "org"
        assert {p["graph_key"] for p in body["data"]["professors"]} == {"p1", "p2"}

        etag = resp.headers["ETag"]
        resp2 = await client.get(url, headers={"If-None-Match": etag})
        assert resp2.status == 304
    finally:
        await client.close()
```

> If the existing async test uses a different client lifecycle (e.g. `async with TestClient(...)`), mirror that exact shape instead — read `test_monitor_aiohttp_api` first and copy its setup/teardown.

- [ ] **Step 8: Run it (GREEN)**

Run: `uv run pytest tests/test_monitor_service.py -k orgunit_professors -v`
Expected: PASS (5 tests including the HTTP/ETag one).

- [ ] **Step 9: Commit**

```bash
git add src/dext_monitor/service.py src/dext_monitor/server.py tests/test_monitor_service.py
git commit -m "feat(monitor): add orgunit professors drill-down endpoint"
```

---

## Task 4: Frontend drill-down — type, API client, chart, topology wiring

**Goal:** Wire the topology page's new college dropdown to fetch the new endpoint and render `OrgUnitProfessorChart`. Two-level dropdown with university→college coupling (changing university clears college).

**Files:**
- Modify: `webui/src/types/monitor.ts` (add types)
- Modify: `webui/src/services/api.ts` (add client method)
- Create: `webui/src/components/charts/OrgUnitProfessorChart.vue`
- Modify: `webui/src/pages/TopologyPage.vue` (whole file)
- Modify: `webui/tests/app-findings.test.ts` (add `orgUnitProfessors` mock)
- Test: rewrite `webui/tests/topology-page.test.ts`; create `webui/tests/orgunit-professor-chart.test.ts`.

**Interfaces:**
- Consumes: `monitorApi.orgUnitProfessors(buildId, orgGraphKey)` (added here), `UniversityTopologyResponse` (existing), `chartTheme`/`palette` (Task 1).
- Produces: `OrgUnitProfessorChart` component with prop `{ subgraph: OrgUnitProfessorResponse | null }`; `TopologyPage` props unchanged from Task 2.

- [ ] **Step 1: Add the response types**

In `webui/src/types/monitor.ts`, append after the `UniversityTopologyResponse` interface:

```ts
export interface ProfessorNode {
  graph_key: string
  name: string
  title: string | null
  title_family: string | null
  role_status: string
}

export interface ProfessorLink {
  source: string
  target: string
  label: 'AFFILIATED_WITH'
}

export interface OrgUnitProfessorResponse {
  build_id: string
  orgunit: {
    graph_key: string
    label: string
    kind: string
    university: string
  }
  professors: ProfessorNode[]
  links: ProfessorLink[]
}
```

- [ ] **Step 2: Add the API client method**

In `webui/src/services/api.ts`, add to the `monitorApi` object (after `universityTopology`):

```ts
  orgUnitProfessors: (buildId: string, orgGraphKey: string) =>
    request<OrgUnitProfessorResponse>(
      `/api/monitor/builds/${encodeURIComponent(buildId)}/orgunit/${encodeURIComponent(orgGraphKey)}/professors`
    ),
```

And add `OrgUnitProfessorResponse` to the type import at the top of `api.ts`:

```ts
import type {
  ApiEnvelope,
  BuildDetailResponse,
  BuildsResponse,
  FindingsResponse,
  HealthResponse,
  MetricsResponse,
  OrgUnitProfessorResponse,
  UniversityTopologyResponse
} from '../types/monitor'
```

- [ ] **Step 3: Write the failing chart test (RED)**

Create `webui/tests/orgunit-professor-chart.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

import OrgUnitProfessorChart from '../src/components/charts/OrgUnitProfessorChart.vue'
import type { OrgUnitProfessorResponse } from '../src/types/monitor'

function buildSubgraph(): OrgUnitProfessorResponse {
  return {
    build_id: 'b1',
    orgunit: { graph_key: 'org', label: '学院A1', kind: 'college', university: 'u' },
    professors: [
      { graph_key: 'p1', name: '张三', title: '教授', title_family: '教授', role_status: 'active' },
      { graph_key: 'p2', name: '李四', title: '副教授', title_family: '副教授', role_status: 'active' }
    ],
    links: [
      { source: 'p1', target: 'org', label: 'AFFILIATED_WITH' },
      { source: 'p2', target: 'org', label: 'AFFILIATED_WITH' }
    ]
  }
}

describe('OrgUnitProfessorChart', () => {
  it('renders one college node, N professor nodes, and N edges', () => {
    const wrapper = mount(OrgUnitProfessorChart, {
      props: { subgraph: buildSubgraph() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const series = frame.props('option').series[0]
    // 1 college + 2 professors = 3 nodes
    expect(series.data).toHaveLength(3)
    expect(series.links).toHaveLength(2)
  })

  it('shows EmptyState when there are no professors', () => {
    const wrapper = mount(OrgUnitProfessorChart, {
      props: {
        subgraph: {
          build_id: 'b1',
          orgunit: { graph_key: 'org', label: '空学院', kind: 'college', university: 'u' },
          professors: [],
          links: []
        }
      },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
  })
})
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd webui && npx vitest run tests/orgunit-professor-chart.test.ts`
Expected: FAIL — `OrgUnitProfessorChart` module does not exist.

- [ ] **Step 5: Create `OrgUnitProfessorChart.vue`**

Create `webui/src/components/charts/OrgUnitProfessorChart.vue`:

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { OrgUnitProfessorResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  subgraph: OrgUnitProfessorResponse | null
  minHeight?: number
}>()

const hasProfessors = computed(
  () => !!props.subgraph && props.subgraph.professors.length > 0
)

const option = computed<ChartOption>(() => {
  const sg = props.subgraph
  if (!sg) return {}
  const orgNode = {
    id: sg.orgunit.graph_key,
    name: sg.orgunit.label,
    category: 0,
    symbolSize: 56,
    itemStyle: { color: palette[0] }
  }
  const profNodes = sg.professors.map((p, i) => ({
    id: p.graph_key,
    name: `${p.name}·${p.title_family ?? p.role_status ?? ''}`,
    category: 1,
    symbolSize: 22,
    itemStyle: { color: palette[i % palette.length] },
    tooltip: `${p.name} · ${p.title ?? ''} · ${p.role_status}`
  }))
  return {
    backgroundColor: 'transparent',
    tooltip: {},
    legend: { top: 0, textStyle: { color: chartTheme.text }, data: ['学院', '教师'] },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: [{ name: '学院' }, { name: '教师' }],
        data: [orgNode, ...profNodes],
        links: sg.links.map((l) => ({ source: l.source, target: l.target, name: l.label })),
        force: { repulsion: 140, edgeLength: 70, gravity: 0.12 },
        label: {
          show: true,
          color: chartTheme.label,
          fontSize: 11,
          formatter: (params: { name: string }) =>
            params.name.length > 12 ? `${params.name.slice(0, 12)}…` : params.name
        },
        lineStyle: { color: chartTheme.edge, curveness: 0.12 },
        edgeSymbol: ['none', 'arrow']
      }
    ]
  }
})
</script>

<template>
  <ChartFrame v-if="hasProfessors" :option="option" :min-height="minHeight ?? 460" />
  <EmptyState
    v-else
    title="No professors"
    message="This college has no affiliated professors in the selected build."
  />
</template>
```

- [ ] **Step 6: Run the chart test (GREEN)**

Run: `cd webui && npx vitest run tests/orgunit-professor-chart.test.ts`
Expected: PASS (2 tests).

- [ ] **Step 7: Write the failing topology-page test (RED)**

Replace `webui/tests/topology-page.test.ts` entirely with:

```ts
import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

const apiMocks = vi.hoisted(() => ({
  orgUnitProfessors: vi.fn()
}))
vi.mock('../src/services/api', () => ({ monitorApi: apiMocks }))

import TopologyPage from '../src/pages/TopologyPage.vue'
import OrgUnitProfessorChart from '../src/components/charts/OrgUnitProfessorChart.vue'
import type { UniversityTopologyResponse, OrgUnitProfessorResponse } from '../src/types/monitor'

function buildTopology(): UniversityTopologyResponse {
  return {
    build_id: 'b1',
    universities: [
      { graph_key: 'u', name: '大学A', logical_id: 'univ:a', orgunit_count: 1, professor_count: 2 }
    ],
    nodes: [
      { id: 'u', label: '大学A', category: 'University', professor_count: 2, orgunit_count: 1 },
      { id: 'org', label: '学院A1', category: 'OrgUnit', kind: 'college', professor_count: 2, university: 'u' }
    ],
    links: [{ source: 'org', target: 'u', label: 'PART_OF' }]
  }
}

function buildSubgraph(): OrgUnitProfessorResponse {
  return {
    build_id: 'b1',
    orgunit: { graph_key: 'org', label: '学院A1', kind: 'college', university: 'u' },
    professors: [
      { graph_key: 'p1', name: '张三', title: '教授', title_family: '教授', role_status: 'active' }
    ],
    links: [{ source: 'p1', target: 'org', label: 'AFFILIATED_WITH' }]
  }
}

const baseProps = {
  health: null, builds: null, detail: null, metrics: null, topology: null,
  findings: [], selectedBuildId: 'b1', error: null, paused: false, lastUpdated: null, history: []
}

describe('TopologyPage', () => {
  it('renders the university chart before a college is selected', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frames = wrapper.findAllComponents(stubs.ChartFrame)
    expect(frames.length).toBeGreaterThanOrEqual(1)
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
  })

  it('enables the college dropdown after a university is chosen and drills in', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const selects = wrapper.findAll('select')
    const uniSelect = selects[0]
    const collegeSelect = selects[1]
    // college dropdown disabled until a university is picked
    expect(collegeSelect.attributes('disabled')).toBeDefined()

    await uniSelect.setValue('u')
    await nextTick()
    expect(collegeSelect.attributes('disabled')).toBeUndefined()
    // college options now list this university's colleges
    expect(collegeSelect.findAll('option').some((o) => o.attributes('value') === 'org')).toBe(true)

    await collegeSelect.setValue('org')
    await flushPromises()
    expect(apiMocks.orgUnitProfessors).toHaveBeenCalledWith('b1', 'org')
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)
  })

  it('shows EmptyState when topology has no nodes', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: { build_id: 'b1', universities: [], nodes: [], links: [] } },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
  })
})
```

- [ ] **Step 8: Run it to verify it fails**

Run: `cd webui && npx vitest run tests/topology-page.test.ts`
Expected: FAIL — TopologyPage has no college dropdown / no `OrgUnitProfessorChart`.

- [ ] **Step 9: Rewrite `TopologyPage.vue`**

Replace the entire contents of `webui/src/pages/TopologyPage.vue` with:

```vue
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  HealthResponse,
  MetricsResponse,
  OrgUnitProfessorResponse,
  UniversityTopologyResponse
} from '../types/monitor'
import type { ThroughputSample } from '../composables/useMonitorData'
import { monitorApi } from '../services/api'
import UniversityTopologyChart from '../components/charts/UniversityTopologyChart.vue'
import OrgUnitProfessorChart from '../components/charts/OrgUnitProfessorChart.vue'
import PanelCard from '../components/features/PanelCard.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'

const props = defineProps<{
  health: HealthResponse | null
  builds: BuildsResponse | null
  detail: BuildDetailResponse | null
  metrics: MetricsResponse | null
  topology: UniversityTopologyResponse | null
  findings: Finding[]
  selectedBuildId: string | null
  error: string | null
  paused: boolean
  lastUpdated: Date | null
  history: ThroughputSample[]
}>()

defineEmits<{
  refresh: []
  togglePause: []
  selectBuild: [buildId: string]
}>()

const selectedUniversity = ref<string | null>(null)
const selectedCollege = ref<string | null>(null)
const subgraph = ref<OrgUnitProfessorResponse | null>(null)
const subgraphError = ref<string | null>(null)

// Colleges belonging to the selected university (from graph_tree nodes).
const colleges = computed(() => {
  const all = props.topology
  if (!all || !selectedUniversity.value) return []
  return all.nodes.filter(
    (n) => n.category === 'OrgUnit' && n.university === selectedUniversity.value
  )
})

function onUniversityChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedUniversity.value = value === '__all__' ? null : value
  // Changing university clears the college drill-down (a college belongs to one university).
  selectedCollege.value = null
  subgraph.value = null
  subgraphError.value = null
}

async function onCollegeChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedCollege.value = value === '__none__' ? null : value
  subgraph.value = null
  subgraphError.value = null
  if (!selectedCollege.value || !props.selectedBuildId) return
  try {
    subgraph.value = await monitorApi.orgUnitProfessors(props.selectedBuildId, selectedCollege.value)
  } catch (e) {
    subgraphError.value = e instanceof Error ? e.message : String(e)
  }
}

function backToUniversityView() {
  selectedCollege.value = null
  subgraph.value = null
  subgraphError.value = null
}

// If the build changes out from under us, drop a stale drill-down.
watch(
  () => props.selectedBuildId,
  () => {
    selectedCollege.value = null
    subgraph.value = null
    subgraphError.value = null
  }
)
</script>

<template>
  <main class="page">
    <header class="hero">
      <div>
        <div class="brand-row"><span class="brand-mark">dx</span><span>dext monitor</span></div>
        <h1>University topology</h1>
        <p>Pick a university, then a college, to see that college's teachers and their affiliation edges.</p>
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <PanelCard
      :title="selectedCollege ? 'College subgraph' : 'University topology'"
      :subtitle="selectedCollege ? '学院 → 教师 with AFFILIATED_WITH edges' : '大学 → 学院 with professor counts'"
    >
      <div class="panel-body">
        <div class="controls">
          <select
            v-if="topology && topology.universities.length"
            class="uni-select"
            :value="selectedUniversity ?? '__all__'"
            @change="onUniversityChange"
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

          <select
            v-if="topology && topology.universities.length"
            class="uni-select"
            :disabled="!selectedUniversity"
            :value="selectedCollege ?? '__none__'"
            @change="onCollegeChange"
          >
            <option value="__none__">选择学院</option>
            <option v-for="org in colleges" :key="org.id" :value="org.id">
              {{ org.label }} ·{{ org.professor_count }}
            </option>
          </select>

          <button v-if="selectedCollege" class="back-btn" @click="backToUniversityView">
            返回大学视图
          </button>
        </div>

        <ErrorPanel v-if="subgraphError" :message="subgraphError" />

        <OrgUnitProfessorChart
          v-if="selectedCollege"
          :subgraph="subgraph"
        />
        <EmptyState
          v-else-if="!topology || !topology.nodes.length"
          title="No topology rows"
          message="University→学院 topology is not available for the selected build yet."
        />
        <UniversityTopologyChart
          v-else
          :topology="topology"
          :min-height="560"
        />
      </div>
    </PanelCard>
  </main>
</template>

<style scoped>
.page {
  width: min(1680px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 1.2rem 0 2.5rem;
}
.hero { padding: 1.2rem 0 1.4rem; }
.brand-row {
  display: flex; align-items: center; gap: 0.7rem;
  color: var(--accent); font-size: 0.86rem; font-weight: 900;
  letter-spacing: 0.08em; text-transform: uppercase;
}
.brand-mark {
  display: inline-grid; place-items: center; width: 36px; height: 36px;
  border: 1px solid var(--border-strong); border-radius: 12px;
  background: var(--surface-soft); color: var(--text);
}
h1 {
  max-width: 760px; margin: 0.7rem 0 0;
  font-size: clamp(2rem, 4vw, 3.4rem); line-height: 0.98; letter-spacing: -0.05em;
}
.hero p { max-width: 720px; margin: 0.9rem 0 0; color: var(--muted); font-size: 1rem; }
.panel-body { padding: 1rem; }
.controls {
  display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; margin-bottom: 0.8rem;
}
.uni-select {
  padding: 0.5rem 0.82rem;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
}
.uni-select:focus {
  outline: none;
  box-shadow: 0 0 0 2px rgba(47, 107, 255, 0.35);
  border-color: var(--accent);
}
.uni-select:disabled { opacity: 0.5; cursor: not-allowed; }
.uni-select option { background: var(--surface); color: var(--text); }
.back-btn {
  padding: 0.5rem 0.9rem;
  background: var(--surface-soft);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
}
.back-btn:hover { background: var(--surface); border-color: var(--border-strong); }
@media (max-width: 760px) {
  .page { width: min(100% - 1rem, 1680px); }
  .controls { flex-direction: column; align-items: stretch; }
}
</style>
```

- [ ] **Step 10: Run the topology test (GREEN)**

Run: `cd webui && npx vitest run tests/topology-page.test.ts`
Expected: PASS (3 tests).

- [ ] **Step 11: Update `app-findings.test.ts` mock**

In `webui/tests/app-findings.test.ts`, add `orgUnitProfessors` to the `apiMocks` hoisted object so a stray call (if any) does not throw. In the `vi.hoisted` block:

```ts
const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  builds: vi.fn(),
  buildDetail: vi.fn(),
  metrics: vi.fn(),
  universityTopology: vi.fn(),
  findings: vi.fn(),
  orgUnitProfessors: vi.fn()
}))
```

And in the `beforeEach`, add:

```ts
    apiMocks.orgUnitProfessors.mockResolvedValue({
      build_id: 'build-1',
      orgunit: { graph_key: 'org', label: 'x', kind: 'college', university: 'u' },
      professors: [], links: []
    })
```

- [ ] **Step 12: Run the full suite + type gate**

Run: `cd webui && npm test && npm run build`
Expected: all tests PASS; `vue-tsc` 0 errors.

- [ ] **Step 13: Commit**

```bash
git add webui/src/types/monitor.ts webui/src/services/api.ts webui/src/components/charts/OrgUnitProfessorChart.vue webui/src/pages/TopologyPage.vue webui/tests/orgunit-professor-chart.test.ts webui/tests/topology-page.test.ts webui/tests/app-findings.test.ts
git commit -m "feat(webui): topology college→professor drill-down"
```

---

## Task 5: Full verification

**Goal:** Confirm the whole change hangs together across backend + frontend, and the dev server boots.

**Files:** none (verification only).

- [ ] **Step 1: Backend full suite**

Run: `uv run pytest tests/test_monitor_service.py -v`
Expected: all tests PASS (existing + new `orgunit_professors` ones).

- [ ] **Step 2: Frontend full suite + type gate**

Run: `cd webui && npm test && npm run build`
Expected: all tests PASS; `vue-tsc` 0 errors; `dist/` produced.

- [ ] **Step 3: Smoke the dev server + backend together (manual)**

Start the monitor backend (in one terminal): `uv run dext-monitor` (or whatever the monitor CLI entry is — check `src/dext_monitor/cli.py` / `pyproject.toml [project.scripts]`). In another terminal: `cd webui && npm run dev`. Open `http://127.0.0.1:5173`. Manually verify:
- Left sidebar shows 概览 / 数据与质量 / 拓扑图; active link highlighted per route.
- Light theme throughout; blue accent.
- `/` shows metric cards + build stage + throughput + runs (no findings/source/export panels — those are on `/data`).
- `/data` shows builds/findings/severity/role/source/export/title/checkpoints.
- `/topology`: pick a university → college dropdown enables and lists colleges → pick a college → subgraph renders (college center + professor nodes + AFFILIATED_WITH arrows) → 「返回大学视图」returns to the university cluster.

- [ ] **Step 4: Commit any stray fixes (if Step 3 surfaced any)**

If smoke-testing surfaced fixes, commit them with an appropriate message. If none, skip.

---

## Self-Review Notes

- **Spec coverage:** §1 sidebar+3 routes → Task 2; §2 light palette + chart theme → Task 1; §3 drill-down + backend endpoint → Task 3 (backend) + Task 4 (frontend); §4 component inventory → Tasks 1–4 create every listed file; §5 testing → each task's RED/GREEN steps + Task 5 verification. No spec section unaddressed.
- **Type consistency:** `OrgUnitProfessorResponse` / `ProfessorNode` / `ProfessorLink` defined once in Task 4 Step 1 and used identically in `api.ts`, `OrgUnitProfessorChart.vue`, `TopologyPage.vue`, and both tests. `chartTheme`/`palette` defined in Task 1 and imported verbatim in every chart. `orgunit_professors` service method name matches the test calls and the `handle_orgunit_professors` handler.
- **Invariants guarded:** professor `graph_key` read from `node:Professor` payload (== `AFFILIATED_WITH.start_graph_key`); `encodeURIComponent` on both path segments in `api.ts`; changing university clears college (Task 4 `onUniversityChange`); build change clears drill-down (Task 4 `watch(selectedBuildId)`).
- **No placeholders:** every step has runnable code or an exact command.
