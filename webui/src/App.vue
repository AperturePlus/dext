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
