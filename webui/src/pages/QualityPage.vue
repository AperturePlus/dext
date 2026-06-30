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
import FindingSummaryChart from '../components/charts/FindingSummaryChart.vue'
import RoleDistributionChart from '../components/charts/RoleDistributionChart.vue'
import TitleFamilyChart from '../components/charts/TitleFamilyChart.vue'
import FindingSummary from '../components/features/FindingSummary.vue'
import PanelCard from '../components/features/PanelCard.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'

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
  <main class="page">
    <header class="hero">
      <div>
        <div class="brand-row"><span class="brand-mark">dx</span><span>dext monitor</span></div>
        <h1>Quality</h1>
        <p>Unresolved findings, entity roles and normalized title families.</p>
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <EmptyState
      v-if="builds && builds.builds.length === 0"
      title="No catalog builds"
      message="Run a graph build first, then refresh this monitor."
    />

    <template v-if="detail && metrics">
      <section class="layout">
        <aside class="sidebar">
          <PanelCard title="Findings" subtitle="Unresolved quality signals">
            <FindingSummary :counts="detail.unresolved_findings" :findings="findings" />
          </PanelCard>
          <PanelCard title="Finding severity" subtitle="Open findings only">
            <div class="panel-body">
              <FindingSummaryChart :counts="detail.unresolved_findings" />
            </div>
          </PanelCard>
        </aside>

        <section class="content">
          <PanelCard title="Role distribution" subtitle="Canonical entity roles">
            <div class="panel-body">
              <RoleDistributionChart :counts="metrics.role_counts ?? {}" />
            </div>
          </PanelCard>

          <PanelCard title="Title families" subtitle="Top normalized title families">
            <div class="panel-body">
              <TitleFamilyChart :counts="metrics.title_family_counts ?? {}" />
            </div>
          </PanelCard>
        </section>
      </section>
    </template>
  </main>
</template>

<style scoped>
.page {
  width: min(1680px, calc(100% - 2rem));
  margin: 0 auto;
  padding: 1.2rem 0 2.5rem;
}
.hero { padding: 1.2rem 0 1.4rem; }
.brand-row {
  display: flex; align-items: center; gap: 0.7rem;
  color: var(--accent); font-size: 0.86rem; font-weight: 900;
  letter-spacing: 0.08em; text-transform: uppercase;
}
.brand-mark {
  display: inline-grid; place-items: center; width: 36px; height: 36px;
  border: 1px solid var(--border-strong); border-radius: 12px;
  background: var(--surface-soft); color: var(--text);
}
h1 {
  max-width: 760px; margin: 0.7rem 0 0;
  font-size: clamp(2rem, 4vw, 3.4rem); line-height: 0.98; letter-spacing: -0.05em;
}
.hero p { max-width: 720px; margin: 0.9rem 0 0; color: var(--muted); font-size: 1rem; }
.layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  gap: 1rem; align-items: start;
}
.layout > * { min-width: 0; }
.sidebar, .content { display: grid; gap: 1rem; min-width: 0; }
:deep(.panel-body) { min-width: 0; overflow: hidden; }
@media (max-width: 1180px) {
  .layout { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .page { width: min(100% - 1rem, 1680px); }
}
</style>
