<script setup lang="ts">
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
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
