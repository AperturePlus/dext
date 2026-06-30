import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises } from '@vue/test-utils'

const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  builds: vi.fn(),
  buildDetail: vi.fn(),
  metrics: vi.fn(),
  universityTopology: vi.fn(),
  findings: vi.fn()
}))

vi.mock('../src/services/api', () => ({
  monitorApi: apiMocks
}))

const { useMonitorData } = await import('../src/composables/useMonitorData')

function buildRow(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 'build-1',
    status: 'WRITING_VECTOR',
    started_at: '2026-06-29T00:00:00+00:00',
    finished_at: null,
    last_error: null,
    summary: {
      source_count: 1,
      rows_read: 0,
      observations_written: 0,
      documents_seen: 0,
      canonical_active: 0,
      unresolved_findings: 0,
      graph_export_rows: 0
    },
    is_active: true,
    stage: [],
    ...overrides
  }
}

function detailWithSummary(summary: Record<string, number>) {
  return {
    catalog_path: 'p',
    build: buildRow({ summary: { ...buildRow().summary, ...summary } }),
    sources: [], checkpoints: [], export_partitions: [],
    unresolved_findings: {}, curation: null, graph: null
  }
}

describe('useMonitorData history (P2-14)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiMocks.health.mockResolvedValue({ catalog_path: 'p', server_time: 0, readable: true, schema_version: 3 })
    apiMocks.builds.mockResolvedValue({
      catalog_path: 'p', schema_version: 3, builds: [buildRow()], latest_build_id: 'build-1'
    })
    apiMocks.buildDetail.mockResolvedValue(detailWithSummary({ rows_read: 10, observations_written: 4, documents_seen: 2 }))
    apiMocks.metrics.mockResolvedValue({
      build_id: 'build-1', stage: [], source_status_counts: {}, finding_counts: {},
      role_counts: {}, title_family_counts: {},
      export_partitions: [], observations_by_source: []
    })
    apiMocks.universityTopology.mockResolvedValue({
      build_id: 'build-1', universities: [], nodes: [], links: []
    })
    apiMocks.findings.mockResolvedValue({ findings: [], limit: 100 })
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('pushes a sample after each refresh and caps at the ring-buffer limit', async () => {
    const { history, refresh } = useMonitorData()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    await nextTick()
    // Initial refresh already pushed one sample.
    expect(history.value.length).toBe(1)
    expect(history.value[0]).toMatchObject({ rowsRead: 10, observations: 4, documents: 2 })

    // 25 more refreshes — cap is 20, so length must never exceed 20.
    for (let i = 0; i < 25; i++) {
      // Vary the numbers so we can confirm the latest sample is the most recent.
      apiMocks.buildDetail.mockResolvedValue(
        detailWithSummary({ rows_read: 100 + i, observations_written: 40 + i, documents_seen: 20 + i })
      )
      await refresh()
      await flushPromises()
      await nextTick()
    }

    expect(history.value.length).toBe(20)
    // The most recent sample reflects the latest refresh (rows_read = 124).
    expect(history.value[history.value.length - 1]).toMatchObject({ rowsRead: 124, observations: 64, documents: 44 })
    // The oldest sample was evicted (it would have been rowsRead=10 or early values).
    expect(history.value[0].rowsRead).not.toBe(10)
  })

  it('does not persist samples across a fresh composable instance (in-memory only)', async () => {
    const first = useMonitorData()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    await nextTick()
    expect(first.history.value.length).toBeGreaterThanOrEqual(1)

    // A second composable instance starts with an empty history (no persistence).
    const second = useMonitorData()
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()
    await nextTick()
    // The second instance's initial refresh produces exactly one sample,
    // proving it did not inherit the first instance's history.
    expect(second.history.value.length).toBe(1)
  })
})
