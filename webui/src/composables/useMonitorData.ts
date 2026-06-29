import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { monitorApi } from '../services/api'
import type {
  BuildDetailResponse,
  BuildsResponse,
  GraphPreviewResponse,
  HealthResponse,
  MetricsResponse
} from '../types/monitor'

/** Active-build polling interval (build still running). */
const ACTIVE_POLL_MS = 5000
/** Terminal-build polling interval (READY/FAILED/etc. — data is frozen). */
const TERMINAL_POLL_MS = 30000

export function useMonitorData() {
  const health = ref<HealthResponse | null>(null)
  const builds = ref<BuildsResponse | null>(null)
  const detail = ref<BuildDetailResponse | null>(null)
  const metrics = ref<MetricsResponse | null>(null)
  const graph = ref<GraphPreviewResponse | null>(null)
  const selectedBuildId = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const paused = ref(false)
  const lastUpdated = ref<Date | null>(null)
  let timer: number | undefined
  let inflight = false

  const latestBuild = computed(() => builds.value?.builds[0] ?? null)
  const activeBuildId = computed(() => selectedBuildId.value ?? builds.value?.latest_build_id ?? null)
  /** True when the selected build is no longer progressing — polling slows. */
  const isActiveBuild = computed(() => detail.value?.build.is_active ?? true)
  const pollIntervalMs = computed(() => (isActiveBuild.value ? ACTIVE_POLL_MS : TERMINAL_POLL_MS))

  async function loadAll() {
    const [nextHealth, nextBuilds] = await Promise.all([monitorApi.health(), monitorApi.builds()])
    health.value = nextHealth
    builds.value = nextBuilds
    if (!selectedBuildId.value) {
      selectedBuildId.value = nextBuilds.latest_build_id
    }
    error.value = null
    lastUpdated.value = new Date()
  }

  async function loadBuild(buildId: string | null) {
    if (!buildId) {
      detail.value = null
      metrics.value = null
      graph.value = null
      return
    }
    const [nextDetail, nextMetrics, nextGraph] = await Promise.all([
      monitorApi.buildDetail(buildId),
      monitorApi.metrics(buildId),
      monitorApi.graphPreview(buildId)
    ])
    detail.value = nextDetail
    metrics.value = nextMetrics
    graph.value = nextGraph
    // Do not clear `error` here: loadAll owns the global error surface; a failed
    // detail fetch for one build should not erase a list-level error, and vice
    // versa. Each fetch surfaces its own error.
  }

  async function refresh() {
    // In-flight guard: concurrent refresh calls (manual + timer overlap, or a
    // slow network backing up) coalesce into the single in-flight call.
    if (inflight) return
    inflight = true
    loading.value = true
    try {
      await Promise.all([loadAll(), loadBuild(activeBuildId.value)])
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : String(exc)
    } finally {
      inflight = false
      loading.value = false
    }
  }

  function selectBuild(buildId: string) {
    selectedBuildId.value = buildId
  }

  function togglePause() {
    paused.value = !paused.value
  }

  function scheduleNext() {
    // Recursive setTimeout (not setInterval) so the interval can adapt to the
    // build's terminal state and a slow previous refresh never stacks calls.
    timer = window.setTimeout(async () => {
      if (!paused.value) {
        await refresh()
      }
      scheduleNext()
    }, pollIntervalMs.value)
  }

  // When the build transitions between active↔terminal, reschedule so the new
  // interval takes effect immediately rather than after the pending timer.
  watch(pollIntervalMs, () => {
    if (timer) window.clearTimeout(timer)
    scheduleNext()
  })

  // Re-fetch the selected build when it changes. Not immediate: the initial
  // refresh() below handles the first load.
  watch(activeBuildId, (buildId) => void loadBuild(buildId), { immediate: false })
  void refresh()
  scheduleNext()
  onBeforeUnmount(() => {
    if (timer) window.clearTimeout(timer)
  })

  return {
    health,
    builds,
    latestBuild,
    selectedBuildId,
    activeBuildId,
    detail,
    metrics,
    graph,
    loading,
    error,
    paused,
    lastUpdated,
    pollIntervalMs,
    refresh,
    selectBuild,
    togglePause
  }
}
