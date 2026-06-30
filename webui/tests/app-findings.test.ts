import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  builds: vi.fn(),
  buildDetail: vi.fn(),
  metrics: vi.fn(),
  universityTopology: vi.fn(),
  findings: vi.fn(),
  orgUnitProfessors: vi.fn()
}))

vi.mock('../src/services/api', () => ({
  monitorApi: apiMocks
}))

import App from '../src/App.vue'
import router from '../src/router'

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

function detailWith(unresolved: Record<string, number> = {}) {
  return {
    catalog_path: 'p',
    build: buildRow(),
    sources: [], checkpoints: [], export_partitions: [],
    unresolved_findings: unresolved, curation: null, graph: null
  }
}

describe('App findings fetch (P1-7)', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    router.push('/')
    await router.isReady()
    apiMocks.health.mockResolvedValue({ catalog_path: 'p', server_time: 0, readable: true, schema_version: 3 })
    apiMocks.builds.mockResolvedValue({
      catalog_path: 'p', schema_version: 3, builds: [buildRow()], latest_build_id: 'build-1'
    })
    apiMocks.buildDetail.mockResolvedValue(detailWith({ warning: 1 }))
    apiMocks.metrics.mockResolvedValue({
      build_id: 'build-1', stage: [], source_status_counts: {}, finding_counts: {},
      role_counts: {}, title_family_counts: {},
      export_partitions: [], observations_by_source: []
    })
    apiMocks.universityTopology.mockResolvedValue({
      build_id: 'build-1', universities: [], nodes: [], links: []
    })
    apiMocks.findings.mockResolvedValue({ findings: [{ id: 'f1', build_id: 'build-1', severity: 'warning', code: 'c', entity_id: null, observation_id: null, details_json: {}, resolved: false }], limit: 100 })
    apiMocks.orgUnitProfessors.mockResolvedValue({
      build_id: 'build-1',
      orgunit: { graph_key: 'org', label: 'x', kind: 'college', university: 'u' },
      professors: [], links: []
    })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('fetches findings once on build load, not on every detail refresh', async () => {
    const wrapper = mount(App, { global: { plugins: [router], stubs: { OverviewPage: { template: '<div />' } } } })
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    await nextTick()
    const callsAfterLoad = apiMocks.findings.mock.calls.length
    expect(callsAfterLoad).toBeGreaterThanOrEqual(1)

    // Simulate a polling refresh that returns the SAME build id and SAME
    // unresolved-findings signature — findings must NOT be refetched.
    apiMocks.buildDetail.mockResolvedValue(detailWith({ warning: 1 }))
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    await nextTick()
    expect(apiMocks.findings.mock.calls.length).toBe(callsAfterLoad)

    // Now the unresolved signature changes — findings IS refetched.
    apiMocks.buildDetail.mockResolvedValue(detailWith({ warning: 1, error: 2 }))
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    await nextTick()
    expect(apiMocks.findings.mock.calls.length).toBeGreaterThan(callsAfterLoad)
  })
})
