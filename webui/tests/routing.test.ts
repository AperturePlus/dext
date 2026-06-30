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
import DataPage from '../src/pages/DataPage.vue'
import QualityPage from '../src/pages/QualityPage.vue'
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

  it('renders DataPage at /data', async () => {
    const wrapper = await mountAt('/data')
    expect(wrapper.findComponent(DataPage).exists()).toBe(true)
  })

  it('renders QualityPage at /quality', async () => {
    const wrapper = await mountAt('/quality')
    expect(wrapper.findComponent(QualityPage).exists()).toBe(true)
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
