# Monitor Topology Page + Overlap/Dropdown Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monitor topology chart into its own `/topology` route with a full-page canvas, fix panel-card overlap on the dashboard, and restyle the university dropdown for contrast/palette consistency.

**Architecture:** Add `vue-router` (history mode) with two routes (`/` → DashboardPage, `/topology` → new TopologyPage). `App.vue` keeps the existing `useMonitorData` data flow and forwards shared props/emits to the active page via `router-view` v-slot. TopologyPage reuses the dashboard hero markup (RefreshControl) so refresh/pause still work, and gives the topology chart a much larger canvas. Overlap fix targets CSS grid children missing `min-width: 0`. Dropdown restyle aligns with `RefreshControl.vue`'s button vocabulary.

**Tech Stack:** Vue 3 (`<script setup lang="ts">`), `vue-router` 4, Vite, vitest + `@vue/test-utils`, ECharts 5. No backend changes.

## Global Constraints

- **All webui commands run from `webui/`:** `npm install`, `npm test`, `npm run build`, `npm run dev`. The test runner is `vitest` (node:test is the *browserext* project, not webui).
- **Testing policy (CLAUDE.md):** TDD — write failing test, run RED, implement minimally, run GREEN, commit one conventional commit per green step. Commits use `feat(webui): …` / `fix(webui): …` / `refactor(webui): …` / `test(webui): …` / `docs(webui): …`.
- **No backend / HTTP-contract changes.** The `/api/monitor/...` endpoints and `types/monitor.ts` DTOs are unchanged.
- **Style tokens** live in `webui/src/styles/tokens.css` — reuse `--surface-strong`, `--border-strong`, `--text`, `--accent`, `--radius-md`, `--shadow`. Do not hardcode hex where a token exists.
- **Platform:** Windows + Git Bash. `LF will be replaced by CRLF` git warnings are benign (CLAUDE.md).
- **Do not touch** `services/api.ts`, `composables/useMonitorData.ts`, `types/monitor.ts`, or any backend Python.
- **Conventional-commit scope:** `webui` (e.g. `feat(webui): add /topology route`).

---

## File Structure

**Create:**
- `webui/src/router.ts` — `createRouter` with history mode, two routes (`/`, `/topology`) + catch-all redirect to `/`.
- `webui/src/pages/TopologyPage.vue` — full-page topology view: shared hero + single full-width topology PanelCard.
- `webui/tests/topology-page.test.ts` — TopologyPage mount tests (renders chart, larger minHeight, hero emits).
- `webui/tests/routing.test.ts` — route → correct page; dashboard no longer renders topology panel.

**Modify:**
- `webui/package.json` — add `vue-router` dependency.
- `webui/src/main.ts` — install router (`createApp(App).use(router).mount('#app')`).
- `webui/src/App.vue` — add top nav (`<router-link>`s) + `router-view` v-slot forwarding shared props/emits.
- `webui/src/pages/DashboardPage.vue` — remove the topology `<PanelCard>`; fix overlap CSS (`min-width:0` on grid children, panel-body containment).
- `webui/src/components/charts/UniversityTopologyChart.vue` — restyle `.uni-select`.

**Untouched:** backend, `services/api.ts`, `composables/useMonitorData.ts`, `types/monitor.ts`, other chart components.

---

## Task 1: Add `vue-router` dependency and router module

**Files:**
- Modify: `webui/package.json`
- Create: `webui/src/router.ts`
- Test: `webui/tests/routing.test.ts` (created here, but Task 2 makes it pass)

**Interfaces:**
- Produces: `createRouter` instance (default export of `router.ts`), used by `main.ts` (Task 4) and asserted by `routing.test.ts` (Task 2).

- [ ] **Step 1: Add the `vue-router` dependency**

Run:
```bash
cd webui && npm install vue-router@^4
```
Expected: `vue-router` added to `webui/package.json` `dependencies` and to `webui/package-lock.json` (project uses bun.lock; running `npm install` regenerates `package-lock.json`, which is fine — it's already tracked per `git status`).

- [ ] **Step 2: Create `webui/src/router.ts`**

```ts
import { createRouter, createWebHistory } from 'vue-router'
import DashboardPage from './pages/DashboardPage.vue'
import TopologyPage from './pages/TopologyPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardPage },
    { path: '/topology', name: 'topology', component: TopologyPage },
    // Unknown paths fall back to the dashboard (KISS: no separate 404 page).
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

export default router
```

- [ ] **Step 3: Commit**

```bash
git add webui/package.json webui/package-lock.json webui/src/router.ts
git commit -m "feat(webui): add vue-router with / and /topology routes"
```

> Note: `router.ts` imports `TopologyPage.vue` which does not exist yet — `vue-tsc`/`npm run build` will fail until Task 3 lands. That's expected; Task 4 wires `main.ts` and the full build is verified only after Task 5. The commit is safe because we commit code, not a passing build at this step.

---

## Task 2: Write the failing routing test

**Files:**
- Create: `webui/tests/routing.test.ts`

**Interfaces:**
- Consumes: `router` from `../src/router` (Task 1), `DashboardPage` and `TopologyPage` (Task 3 produces `TopologyPage`).
- Produces: a red test proving routing behaves as intended (dashboard has no topology panel; `/topology` renders TopologyPage).

- [ ] **Step 1: Write the failing test**

Create `webui/tests/routing.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

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
import DashboardPage from '../src/pages/DashboardPage.vue'
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

  it('renders DashboardPage at / and does NOT show the topology panel', async () => {
    const wrapper = await mountAt('/')
    expect(wrapper.findComponent(DashboardPage).exists()).toBe(true)
    expect(wrapper.findComponent(TopologyPage).exists()).toBe(false)
    // The topology panel heading must be gone from the dashboard.
    expect(wrapper.text()).not.toContain('University topology')
  })

  it('renders TopologyPage at /topology', async () => {
    const wrapper = await mountAt('/topology')
    expect(wrapper.findComponent(TopologyPage).exists()).toBe(true)
    expect(wrapper.findComponent(DashboardPage).exists()).toBe(false)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd webui && npm test -- routing.test.ts
```
Expected: FAIL — `TopologyPage` does not exist (`Cannot find module '../src/pages/TopologyPage.vue'`), and `App.vue` has no router yet. This is the red state for Task 3 + Task 4.

---

## Task 3: Create `TopologyPage.vue`

**Files:**
- Create: `webui/src/pages/TopologyPage.vue`

**Interfaces:**
- Consumes: the same props/emits as `DashboardPage` (`health`, `builds`, `detail`, `metrics`, `topology`, `findings`, `selectedBuildId`, `error`, `paused`, `lastUpdated`, `history`; emits `refresh`, `togglePause`, `selectBuild`). `UniversityTopologyChart` accepts `topology: UniversityTopologyResponse | null` and an optional `min-height` via its `ChartFrame` — but the chart hard-codes `:min-height="390"` internally, so this task passes a **prop** `minHeight` into `UniversityTopologyChart` (added in Step 3 below) so TopologyPage can request a bigger canvas without touching the dashboard's default.
- Produces: `TopologyPage` default export, imported by `router.ts` (Task 1) and tested by `routing.test.ts` / `topology-page.test.ts`.

- [ ] **Step 1: Write the failing TopologyPage test**

Create `webui/tests/topology-page.test.ts`:

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

import TopologyPage from '../src/pages/TopologyPage.vue'
import type { UniversityTopologyResponse } from '../src/types/monitor'

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

const baseProps = {
  health: null, builds: null, detail: null, metrics: null, topology: null,
  findings: [], selectedBuildId: null, error: null, paused: false, lastUpdated: null, history: []
}

describe('TopologyPage', () => {
  it('renders the chart with the large full-page minHeight', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    // Full-page canvas must be taller than the dashboard's old 390px default.
    expect(frame.props('minHeight')).toBeGreaterThan(390)
  })

  it('emits refresh and toggle-pause from the hero RefreshControl', async () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    // RefreshControl renders two buttons; first is "Refresh".
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('refresh')).toBeTruthy()
    expect(wrapper.emitted('togglePause')).toBeTruthy()
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

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd webui && npm test -- topology-page.test.ts
```
Expected: FAIL — `Cannot find module '../src/pages/TopologyPage.vue'`.

- [ ] **Step 3: Add a `minHeight` prop to `UniversityTopologyChart`**

In `webui/src/components/charts/UniversityTopologyChart.vue`, change the `<script setup>` props (currently only `topology`) and the `<ChartFrame>` binding. The dashboard keeps the default 390; TopologyPage passes a larger value.

Replace this block (the existing `defineProps` + `<ChartFrame>` line):

```vue
const props = defineProps<{
  topology: UniversityTopologyResponse | null
}>()
```
…remains, but add a second prop. New `<script setup>` top:

```ts
const props = defineProps<{
  topology: UniversityTopologyResponse | null
  minHeight?: number
}>()
```

And replace the `<ChartFrame>` line:

```vue
<ChartFrame v-if="topology && topology.nodes.length" :option="option" :min-height="390" />
```
with:

```vue
<ChartFrame
  v-if="topology && topology.nodes.length"
  :option="option"
  :min-height="minHeight ?? 390"
/>
```

(`minHeight` defaults to 390 when unset, so the dashboard — which does not pass it — is unchanged.)

- [ ] **Step 4: Create `webui/src/pages/TopologyPage.vue`**

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
import { shortId } from '../utils/format'
import type { ThroughputSample } from '../composables/useMonitorData'
import UniversityTopologyChart from '../components/charts/UniversityTopologyChart.vue'
import PanelCard from '../components/features/PanelCard.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'
import RefreshControl from '../components/primitives/RefreshControl.vue'
import StatusBadge from '../components/primitives/StatusBadge.vue'

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
  <main class="topology-page">
    <header class="hero">
      <div>
        <div class="brand-row">
          <span class="brand-mark">dx</span>
          <span>dext monitor</span>
        </div>
        <h1>University topology</h1>
        <p>Full-page 大学 → 学院 graph with professor counts. Drag to rearrange, scroll to zoom.</p>
      </div>
      <div class="hero-actions">
        <StatusBadge :status="health?.readable ? 'catalog_readable' : 'catalog_unavailable'" />
        <RefreshControl
          :paused="paused"
          :last-updated="lastUpdated"
          @refresh="$emit('refresh')"
          @toggle-pause="$emit('togglePause')"
        />
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <PanelCard title="University topology" subtitle="大学 → 学院 with professor counts">
      <div class="panel-body">
        <UniversityTopologyChart :topology="topology" :min-height="640" />
      </div>
    </PanelCard>
  </main>
</template>

<style scoped>
.topology-page {
  width: min(1680px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 1.2rem 0 2.5rem;
}

.hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 2rem;
  padding: 1.2rem 0 1.4rem;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  color: var(--accent);
  font-size: 0.86rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.brand-mark {
  display: inline-grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border: 1px solid rgba(62, 230, 181, 0.42);
  border-radius: 12px;
  background: rgba(62, 230, 181, 0.12);
  color: var(--text);
}

h1 {
  max-width: 760px;
  margin: 0.7rem 0 0;
  font-size: clamp(2.25rem, 5vw, 4.4rem);
  line-height: 0.94;
  letter-spacing: -0.07em;
}

.hero p {
  max-width: 720px;
  margin: 0.9rem 0 0;
  color: var(--muted);
  font-size: 1rem;
}

.hero-actions {
  display: grid;
  justify-items: end;
  gap: 0.85rem;
  min-width: 320px;
}

.panel-body {
  padding: 1rem;
}

@media (max-width: 760px) {
  .topology-page {
    width: min(100% - 1rem, 1680px);
  }
  .hero {
    display: grid;
  }
  .hero-actions {
    justify-items: start;
    min-width: 0;
  }
}
</style>
```

> `shortId` is imported to mirror DashboardPage's hero shape; if it is unused the linter will flag it — remove the import if so. (`shortId` is used in DashboardPage's hero hint; here the hero shows no hint, so **drop the `shortId` import** to avoid an unused-import error under `vue-tsc`.) Final `<script setup>` imports must omit `shortId`.

- [ ] **Step 5: Remove the unused `shortId` import**

In the file just written, delete this line:

```ts
import { shortId } from '../utils/format'
```

- [ ] **Step 6: Run the TopologyPage test to verify it passes**

Run:
```bash
cd webui && npm test -- topology-page.test.ts
```
Expected: PASS — chart renders with `minHeight` > 390 (640), hero buttons emit `refresh`/`togglePause`, empty topology shows EmptyState.

- [ ] **Step 7: Run the existing university-topology test to ensure the new prop didn't break it**

Run:
```bash
cd webui && npm test -- university-topology.test.ts
```
Expected: PASS — the dashboard path still uses the default `minHeight` (390) and the existing assertions on nodes/links/labels are unchanged.

- [ ] **Step 8: Commit**

```bash
git add webui/src/pages/TopologyPage.vue webui/src/components/charts/UniversityTopologyChart.vue webui/tests/topology-page.test.ts
git commit -m "feat(webui): add TopologyPage with full-page topology canvas"
```

---

## Task 4: Wire router into `main.ts` and `App.vue`

**Files:**
- Modify: `webui/src/main.ts`
- Modify: `webui/src/App.vue`

**Interfaces:**
- Consumes: `router` from `./router` (Task 1), `DashboardPage` + `TopologyPage` (Task 3).
- Produces: an `App` that renders the correct page for the current route and forwards shared props/emits.

- [ ] **Step 1: Update `webui/src/main.ts`**

Replace its contents with:

```ts
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/base.css'

createApp(App).use(router).mount('#app')
```

- [ ] **Step 2: Update `webui/src/App.vue`**

Replace the `<template>` and add nav styles. The `<script setup>` block is **unchanged** (keep the existing `useMonitorData` + findings watcher). Only the `<template>` and `<style scoped>` change.

New `<template>` (replaces the existing `<template>` block):

```vue
<template>
  <div class="app-shell">
    <nav class="app-nav">
      <router-link to="/" class="nav-link">Dashboard</router-link>
      <router-link to="/topology" class="nav-link">Topology</router-link>
    </nav>
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
```

Add a `<style scoped>` block at the end of `App.vue` (the file currently has no `<style>` block):

```vue
<style scoped>
.app-shell {
  min-height: 100%;
}

.app-nav {
  display: flex;
  gap: 0.4rem;
  max-width: 1680px;
  margin: 0 auto;
  padding: 0.8rem 1rem 0;
}

.nav-link {
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.08);
  color: var(--muted);
  font-size: 0.82rem;
  font-weight: 700;
  text-decoration: none;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.nav-link:hover {
  color: var(--text);
  border-color: var(--border-strong);
}

.nav-link.router-link-active {
  color: var(--accent);
  border-color: rgba(62, 230, 181, 0.42);
  background: rgba(62, 230, 181, 0.12);
}
</style>
```

- [ ] **Step 3: Note on test ordering — do NOT run the routing test yet**

The routing test's `not.toContain('University topology')` assertion depends on Task 5 removing the topology panel from the dashboard. Running `routing.test.ts` now would fail on that assertion. The routing test is run and goes green in **Task 5, Step 5**, after the panel is removed. Skip running it here.

- [ ] **Step 4: Commit**

```bash
git add webui/src/main.ts webui/src/App.vue
git commit -m "feat(webui): install router and render page by route in App"
```

---

## Task 5: Remove the topology panel from `DashboardPage` and fix panel overlap

**Files:**
- Modify: `webui/src/pages/DashboardPage.vue`

**Interfaces:**
- Consumes: nothing new.
- Produces: a dashboard without the topology panel and with non-overlapping panels at 760/1024/1280/1680 widths.

- [ ] **Step 1: Update `app-findings.test.ts` if it asserts the topology panel**

Read `webui/tests/app-findings.test.ts`. It stubs `DashboardPage` (`stubs: { DashboardPage: { template: '<div />' } }`), so it does **not** render the real dashboard — no assertion change needed. Confirm this by reading the file; if no topology-panel assertion exists, skip this step.

- [ ] **Step 2: Remove the topology `<PanelCard>` from `DashboardPage.vue`**

In `webui/src/pages/DashboardPage.vue`, delete this block (lines ~144–148):

```vue
          <PanelCard title="University topology" subtitle="大学 → 学院 with professor counts">
            <div class="panel-body">
              <UniversityTopologyChart :topology="topology" />
            </div>
          </PanelCard>
```

- [ ] **Step 3: Remove the now-unused `UniversityTopologyChart` import from `DashboardPage.vue`**

Delete this line from the `<script setup>` imports:

```ts
import UniversityTopologyChart from '../components/charts/UniversityTopologyChart.vue'
```

- [ ] **Step 4: Fix panel overlap — add `min-width: 0` to grid children and contain chart panels**

In `webui/src/pages/DashboardPage.vue` `<style scoped>`, make these edits:

a) In the `.sidebar, .content` rule (currently `display: grid; gap: 1rem;`), add `min-width: 0`:

```css
.sidebar,
.content {
  display: grid;
  gap: 1rem;
  min-width: 0;
}
```

b) In the `.layout` rule, add `min-width: 0` to children by adding a new rule targeting grid items:

```css
.layout > * {
  min-width: 0;
}
```

c) In the `.split` rule (currently `display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr); gap: 1rem;`), add:

```css
.split > * {
  min-width: 0;
}
```

d) In the `.metric-grid` rule, add:

```css
.metric-grid > * {
  min-width: 0;
}
```

e) Ensure chart panels clip overflow rather than bleed into siblings — add a rule for slotted panel bodies that contain charts. After the existing `.run-grid` rule, add:

```css
:deep(.panel-body) {
  min-width: 0;
  overflow: hidden;
}
```

> `:deep()` is needed because `.panel-body` is slotted content (rendered by `DashboardPage` inside `PanelCard`'s `<slot />`), so scoped styles don't reach it directly. `overflow: hidden` keeps ECharts canvases/tables from overflowing into a sibling panel.

- [ ] **Step 5: Run the routing test — the "no topology panel" assertion now passes**

Run:
```bash
cd webui && npm test -- routing.test.ts
```
Expected: PASS — `/` renders DashboardPage without `University topology` text; `/topology` renders TopologyPage.

- [ ] **Step 6: Run the full webui test suite**

Run:
```bash
cd webui && npm test
```
Expected: all green (existing + new tests).

- [ ] **Step 7: Commit**

```bash
git add webui/src/pages/DashboardPage.vue
git commit -m "refactor(webui): drop topology panel from dashboard, fix panel overlap"
```

---

## Task 6: Restyle the university dropdown

**Files:**
- Modify: `webui/src/components/charts/UniversityTopologyChart.vue`

**Interfaces:**
- Consumes: style tokens from `tokens.css`.
- Produces: a high-contrast, palette-consistent dropdown.

- [ ] **Step 1: Replace the `.uni-select` `<style scoped>` block**

In `webui/src/components/charts/UniversityTopologyChart.vue`, replace the existing `<style scoped>` block:

```css
.uni-select {
  margin-bottom: 0.6rem;
  padding: 0.35rem 0.5rem;
  background: rgba(62, 230, 181, 0.06);
  color: var(--text, #e8f0ff);
  border: 1px solid rgba(146, 164, 189, 0.35);
  border-radius: 6px;
  font-size: 0.84rem;
}
```

with:

```css
.uni-select {
  margin-bottom: 0.6rem;
  padding: 0.5rem 0.82rem;
  background: var(--surface-strong);
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
  box-shadow: 0 0 0 2px rgba(62, 230, 181, 0.4);
  border-color: rgba(62, 230, 181, 0.42);
}

.uni-select option {
  background: var(--surface-strong);
  color: var(--text);
}
```

- [ ] **Step 2: Run the chart test to ensure the dropdown still functions**

Run:
```bash
cd webui && npm test -- university-topology.test.ts topology-page.test.ts
```
Expected: PASS — the `setValue('u2')` interaction in `university-topology.test.ts` still filters the cluster; styling doesn't affect behavior.

- [ ] **Step 3: Commit**

```bash
git add webui/src/components/charts/UniversityTopologyChart.vue
git commit -m "fix(webui): restyle university dropdown for contrast and palette match"
```

---

## Task 7: Typecheck, build, and manual verification

**Files:**
- None modified (verification only).

- [ ] **Step 1: Run the full test suite**

Run:
```bash
cd webui && npm test
```
Expected: all green.

- [ ] **Step 2: Run typecheck + production build**

Run:
```bash
cd webui && npm run build
```
Expected: `vue-tsc --noEmit` reports 0 errors; `vite build` emits `dist/` with no warnings beyond benign CRLF notices.

- [ ] **Step 3: Manual verification — run the dev server and check the three reported issues**

Run (in a separate shell, keep it open):
```bash
cd webui && npm run dev
```
Open the printed URL (default `http://127.0.0.1:5173`). Verify:

1. **Routing:** nav shows "Dashboard" (active at `/`) and "Topology". Click Topology → URL becomes `/topology`, large topology chart fills the page. Click Dashboard → returns to the dashboard, topology panel is gone.
2. **Overlap:** on the dashboard, resize the window across 760 / 1024 / 1280 / 1680 px widths. Panels must not overlap; no horizontal scrollbar appears; charts are clipped to their panels (not bleeding into siblings). Specifically check the Finding severity donut in the sidebar and the Source ingestion / Export partitions panels in the content column.
3. **Dropdown:** on the topology page, the university dropdown has a solid dark background, a clearly visible border, white text, and a green focus ring on click. It visually matches the RefreshControl buttons. Open it — options are dark-backed with legible text.
4. **Topology canvas:** the graph is much larger than before (≥ 640px tall) and not squeezed beside a sidebar.

If any check fails, fix and re-run before proceeding.

- [ ] **Step 4: Final commit if any verification-driven fixes were made**

If Step 3 surfaced a fix, commit it:
```bash
git add -A
git commit -m "fix(webui): <specific fix from manual verification>"
```
If no fixes were needed, no commit — the work is complete.

---

## Done

All three reported issues are resolved:
1. Panel overlap fixed (grid children get `min-width: 0`, chart panels `overflow: hidden`).
2. Topology moved to its own `/topology` route with a full-page canvas.
3. University dropdown restyled to match the app palette with adequate contrast.

No backend, DTO, or HTTP-contract changes. `useMonitorData` polling is unchanged — topology is still fetched alongside detail/metrics on the same interval, just rendered on a different page.
