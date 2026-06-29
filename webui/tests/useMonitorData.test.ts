import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

// Mock the api module before importing the composable.
const apiMocks = vi.hoisted(() => ({
  health: vi.fn(),
  builds: vi.fn(),
  buildDetail: vi.fn(),
  metrics: vi.fn(),
  graphPreview: vi.fn(),
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

function mockActive() {
  apiMocks.health.mockResolvedValue({ catalog_path: 'p', server_time: 0, readable: true, schema_version: 3 })
  apiMocks.builds.mockResolvedValue({
    catalog_path: 'p', schema_version: 3, builds: [buildRow()], latest_build_id: 'build-1'
  })
  apiMocks.buildDetail.mockResolvedValue({
    catalog_path: 'p', build: buildRow(), sources: [], checkpoints: [],
    export_partitions: [], unresolved_findings: {}, curation: null, graph: null
  })
  apiMocks.metrics.mockResolvedValue({
    build_id: 'build-1', stage: [], source_status_counts: {}, finding_counts: {},
    export_partitions: [], observations_by_source: []
  })
  apiMocks.graphPreview.mockResolvedValue({
    build_id: 'build-1', limit: 300, nodes: [], links: [],
    total_nodes: 0, total_relationships: 0, truncated: false
  })
  apiMocks.findings.mockResolvedValue({ findings: [], limit: 100 })
}

describe('useMonitorData', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockActive()
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('P1-8: a refresh with a selected build runs list + detail concurrently', async () => {
    // On a refresh where the build is ALREADY selected (activeBuildId known),
    // loadAll and loadBuild must be in flight together. We prove this by making
    // builds() resolve last and asserting buildDetail() was called before it
    // resolved.
    let resolveBuilds!: () => void
    apiMocks.builds.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBuilds = () =>
            resolve({
              catalog_path: 'p', schema_version: 3, builds: [buildRow()], latest_build_id: 'build-1'
            })
        })
    )

    const { refresh } = useMonitorData()
    // First refresh: selects build-1 (builds resolves immediately via mockActive
    // would have, but here builds is a pending promise). Wait for the initial
    // load to settle the selected build id, then cancel pending timers.
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()
    // Let the first load complete by resolving builds.
    resolveBuilds()
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()
    vi.clearAllMocks()
    // Re-arm the pending-builds mock for the second refresh.
    apiMocks.builds.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveBuilds = () =>
            resolve({
              catalog_path: 'p', schema_version: 3, builds: [buildRow()], latest_build_id: 'build-1'
            })
        })
    )
    apiMocks.buildDetail.mockResolvedValue({
      catalog_path: 'p', build: buildRow(), sources: [], checkpoints: [],
      export_partitions: [], unresolved_findings: {}, curation: null, graph: null
    })

    void refresh() // second refresh — build-1 already selected
    await vi.advanceTimersByTimeAsync(0)

    // buildDetail was called before builds resolved — concurrency proven.
    expect(apiMocks.buildDetail).toHaveBeenCalled()
    expect(resolveBuilds).toBeDefined()
    resolveBuilds()
    await vi.advanceTimersByTimeAsync(0)
  })

  it('P1-6: a refresh while one is in-flight is a no-op (no double fetch)', async () => {
    let resolveHealth!: () => void
    apiMocks.health.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveHealth = () => resolve({ catalog_path: 'p', server_time: 0, readable: true, schema_version: 3 })
        })
    )

    const { refresh } = useMonitorData()
    await vi.advanceTimersByTimeAsync(0) // initial refresh starts
    const callsAfterFirst = apiMocks.health.mock.calls.length
    expect(callsAfterFirst).toBeGreaterThanOrEqual(1)

    // Fire concurrent refreshes while the first is still pending.
    void refresh()
    void refresh()
    await vi.advanceTimersByTimeAsync(0)

    // Only the in-flight call counts; the two extra refresh() calls were no-ops.
    expect(apiMocks.health.mock.calls.length).toBe(callsAfterFirst)

    resolveHealth()
    await vi.advanceTimersByTimeAsync(0)
  })

  it('P1-4: terminal build slows polling past the active 5s interval', async () => {
    const { refresh } = useMonitorData()
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()

    // Flip the selected build to terminal (is_active=false).
    const terminal = buildRow({ status: 'READY', is_active: false, finished_at: '2026-06-29T01:00:00+00:00' })
    apiMocks.builds.mockResolvedValue({
      catalog_path: 'p', schema_version: 3, builds: [terminal], latest_build_id: 'build-1'
    })
    apiMocks.buildDetail.mockResolvedValue({
      catalog_path: 'p', build: terminal, sources: [], checkpoints: [],
      export_partitions: [], unresolved_findings: {}, curation: null, graph: null
    })
    await refresh()
    await vi.advanceTimersByTimeAsync(0)
    await nextTick()
    await nextTick() // let the pollIntervalMs watcher reschedule
    const terminalBase = apiMocks.health.mock.calls.length

    // 5s (the active interval) must NOT fire another fetch for a terminal build.
    await vi.advanceTimersByTimeAsync(5000)
    await nextTick()
    expect(apiMocks.health.mock.calls.length).toBe(terminalBase)

    // The longer interval (30s) eventually fires again.
    await vi.advanceTimersByTimeAsync(30000)
    await nextTick()
    expect(apiMocks.health.mock.calls.length).toBeGreaterThan(terminalBase)
  })
})
