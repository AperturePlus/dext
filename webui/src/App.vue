<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import DashboardPage from './pages/DashboardPage.vue'
import { monitorApi } from './services/api'
import { useMonitorData } from './composables/useMonitorData'
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

// Findings are refetched only when the build id or the set of unresolved
// severity buckets changes — not on every 5s detail refresh. The previous
// watch on `detail` fired every poll, hammering /findings for frozen data.
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
  <DashboardPage
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
</template>
