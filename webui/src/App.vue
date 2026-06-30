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
