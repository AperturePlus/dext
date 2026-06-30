# Monitor — topology page, panel-overlap fix, dropdown styling

**Date:** 2026-06-30
**Status:** Draft (pending user review)
**Area:** `webui/` (Vue 3 monitor frontend)

## Problem

Three monitor-UI issues reported against the current `DashboardPage`:

1. **Panel cards overlap.** On certain viewport widths panels render on top of
   each other (visible in the screenshot: the Finding severity donut is
   partially clipped/overlapped at the bottom of the sidebar, and chart panels
   in the content column collide). Root cause unconfirmed at design time —
   likely CSS grid children missing `min-width: 0`, letting ECharts canvases /
   tables blow out column width and trigger overflow.
2. **Topology chart is too small.** `UniversityTopologyChart` lives inside a
   `.content` column PanelCard with `min-height: 390`, squeezed beside the
   sidebar. A force-directed graph needs a much larger canvas to be legible.
3. **University dropdown has low contrast / off palette.** `.uni-select` uses
   `rgba(62,230,181,0.06)` background — nearly invisible on the dark surface —
   with a faint `rgba(146,164,189,0.35)` border and `var(--text)` text. It does
   not match the RefreshControl button styling and is hard to read.

## Goals

- Move topology to its own dedicated route (`/topology`) with a full-page
  canvas, keeping it out of the dashboard layout entirely.
- Fix panel-card overlap on the dashboard (root cause confirmed during
  implementation, not guessed).
- Restyle the university dropdown to match the app's palette and meet contrast.
- No backend, DTO, or HTTP-contract changes. No new data fetching beyond what
  `useMonitorData` already does.

## Non-goals (YAGNI)

- No node search/filter beyond the existing university dropdown.
- No refactor of other chart components.
- No changes to the `/topology` backend endpoint or response shape.
- No redesign of the dashboard's information architecture beyond removing the
  topology panel.

## Design

### 1. Routing

Add `vue-router` with two routes. `App.vue` keeps the existing data-loading
(`useMonitorData` + findings watcher) unchanged and forwards shared props +
emits to whichever page is active via `router-view` v-slot.

- **Router:** `createWebHistory()`, routes:
  - `/` → `DashboardPage` (topology panel **removed**)
  - `/topology` → new `pages/TopologyPage.vue`
  - redirect `/topology/` and unknown → `/` (or a minimal 404 inline; pick the
    redirect-to-home option for simplicity).
- **`main.ts`:** `createApp(App).use(router).mount('#app')`. Add
  `vue-router` to `webui/package.json` dependencies.
- **`App.vue`:** replace `<DashboardPage … />` with
  ```vue
  <router-view v-slot="{ Component }">
    <component :is="Component"
      :health="health" :builds="builds" :detail="detail" :metrics="metrics"
      :topology="topology" :findings="findings" :selected-build-id="selected"
      :error="error" :paused="paused" :last-updated="lastUpdated" :history="history"
      @refresh="refresh" @toggle-pause="togglePause" @select-build="selectBuild"
    />
  </router-view>
  ```
  This is approach ①A: zero data-flow change, emits still bubble up to App.

### 2. App shell / navigation

Rather than a separate `AppShell` component for two routes, add nav links
directly to the existing `DashboardPage` hero is wrong (the hero is per-page).
Instead, add a minimal top nav **in `App.vue`** above `<router-view>`:

- A small `.app-nav` row with two `<router-link>`s: `Dashboard` (`/`) and
  `Topology` (`/topology)`. Active link styled with `--accent` underline /
  color; inactive with `--muted`.
- The brand mark + status badge + refresh control stay in each page's hero
  (unchanged from today's `DashboardPage` hero; `TopologyPage` reuses the same
  hero markup so refresh/pause still works on the topology page).
  - Rationale: refresh/pause are bound to App's `useMonitorData`, and the hero
    is shared visual chrome. Extracting a shared `PageHero` slot is tempting but
    out of scope (YAGNI for two pages); copy the hero markup into
    `TopologyPage` and accept the small duplication.

### 3. TopologyPage — full-page canvas

New file `webui/src/pages/TopologyPage.vue`:

- Same `<header class="hero">` markup as `DashboardPage` (brand, h1 "University
  topology", status badge, RefreshControl) so the shared props/emits work
  identically.
- Body: a single full-width `PanelCard` titled "University topology" containing
  `UniversityTopologyChart`.
- Container width: full dashboard width (`min(1680px, …)`) — no sidebar
  competing for space.
- Chart height: bump `ChartFrame` `min-height` from 390 →
  `clamp(480px, 70vh, 760px)` when rendered on this page. Done by passing a
  larger `minHeight` prop from `TopologyPage` (the chart already accepts
  `:min-height`).
- Optional side stats (universities count, orgunits count, total professors)
  derived from `topology` — render as a small `MetricCard` row above the chart
  only if it's trivial; otherwise skip (YAGNI). **Decision: skip the stats row
  in v1** — the dropdown already shows per-university counts; a top-level
  stats row adds layout surface without clear value.

### 4. Panel-card overlap fix (dashboard)

**Root cause is confirmed during implementation, not assumed here.** The
implementation step first reproduces the overlap (run `npm run dev` at the
problematic width, inspect), then applies the targeted fix from this candidate
set:

- **Primary suspect:** CSS grid children with intrinsic wide content (ECharts
  canvas, `<table>`) blow out the column. Fix: add `min-width: 0` to the direct
  children of `.layout`, `.split`, and `.metric-grid` (grid items default to
  `min-width: auto` and refuse to shrink below content size).
- **Secondary:** PanelCard inner `.panel-body` containing charts should
  `overflow: hidden` (or `min-width: 0`) so the ECharts canvas is clipped to
  the panel rather than overflowing into a sibling.
- **Tertiary:** Audit the `@media (max-width: 1180px)` and `760px` breakpoints
  — the overlap may only manifest at a width where the grid hasn't collapsed
  to a single column yet (e.g. 1181–1400px with a wide sidebar). Add an
  intermediate breakpoint if needed.
- **Quaternary:** the Finding severity donut (`FindingSummaryChart`) clipping
  in the narrow sidebar column — verify its `ChartFrame` has enough height and
  the legend isn't pushing the canvas below the panel border.

The fix must not introduce horizontal scrollbars on the dashboard at any width
≥ 760px. Verify by resizing the browser across 760 / 1024 / 1280 / 1680 widths.

### 5. Dropdown restyle

In `UniversityTopologyChart.vue`, replace `.uni-select` styles:

- `background: var(--surface-strong)` (was `rgba(62,230,181,0.06)`)
- `border: 1px solid var(--border-strong)` (was `rgba(146,164,189,0.35)`)
- `color: var(--text)` (kept)
- `padding` aligned with `RefreshControl` button: `0.5rem 0.82rem`
- `border-radius: 999px` to match button pill shape
- `font-size: 0.82rem; font-weight: 700`
- Focus: `box-shadow: 0 0 0 2px rgba(62,230,181,0.4)` + `outline: none`
- Native `<option>` elements: styling is browser-limited; ensure at minimum the
  option text uses `var(--text)` and the option background is a solid dark
  color (set on the `<select>`, which most browsers inherit for the dropdown
  listbox). Acceptable if not pixel-perfect cross-browser; the priority is the
  closed-select contrast and palette match.

This unifies the dropdown with the RefreshControl button vocabulary already
established in `RefreshControl.vue`.

### 6. Data flow (unchanged)

`useMonitorData` already fetches `topology` alongside `metrics`/`detail`. No
new endpoint, no new polling. `App.vue` continues to pass `topology` to the
active page; `DashboardPage` simply stops rendering it. The findings-watcher
in `App.vue` is untouched.

### 7. Testing

- **Migrate** `tests/university-topology.test.ts`: the chart-level tests
  (dropdown filter, empty state, labels) stay as-is — they test the chart
  component, not the page. No change needed unless `UniversityTopologyChart`'s
  public props change (they don't).
- **Add** `tests/topology-page.test.ts`:
  - mounts `TopologyPage` with a topology prop, asserts the chart renders with
    full-page `min-height`.
  - asserts the hero / RefreshControl emit `refresh`/`toggle-pause`/`select-build`.
- **Add** `tests/routing.test.ts` (or extend `app-findings.test.ts`):
  - with router installed, at `/` → `DashboardPage` renders and **no** topology
    panel is present (assert absence of "University topology" heading).
  - at `/topology` → `TopologyPage` renders.
- **Update** `app-findings.test.ts` only if it asserts the presence of the
  topology panel (currently it stubs `DashboardPage`, so likely no change).
- **Add** a dropdown-style regression test is **not** worthwhile (CSS contrast
  is hard to assert meaningfully in jsdom); rely on visual verification.

### 8. Build & verification

- `cd webui && npm install` (pulls `vue-router`).
- `npm test` — all green (existing + new).
- `npm run build` — `vue-tsc --noEmit` passes, `dist/` rebuilds.
- `npm run dev` — manually verify: dashboard has no overlap at 760/1024/1280/
  1680 widths; topology page shows a large canvas; dropdown is legible and
  matches the button palette.

## Files

**New:**
- `webui/src/pages/TopologyPage.vue`
- `webui/src/router.ts`
- `webui/tests/topology-page.test.ts`
- `webui/tests/routing.test.ts`

**Modified:**
- `webui/src/main.ts` (install router)
- `webui/src/App.vue` (nav + `router-view` v-slot)
- `webui/src/pages/DashboardPage.vue` (remove topology panel; overlap CSS fix)
- `webui/src/components/charts/UniversityTopologyChart.vue` (dropdown restyle)
- `webui/package.json` (add `vue-router` dep)

**Untouched:** backend, `services/api.ts`, `composables/useMonitorData.ts`,
`types/monitor.ts`, all other chart components.

## Open questions

None blocking — all decided above. The only deferred decision is the exact
overlap root cause, which the implementation step confirms empirically before
applying a fix from the candidate set.
