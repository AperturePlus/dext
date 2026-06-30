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
import ExportPartitionChart from '../components/charts/ExportPartitionChart.vue'
import SourceTaskChart from '../components/charts/SourceTaskChart.vue'
import BuildList from '../components/features/BuildList.vue'
import CheckpointTable from '../components/features/CheckpointTable.vue'
import PanelCard from '../components/features/PanelCard.vue'
import SourceTaskTable from '../components/features/SourceTaskTable.vue'
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
        <h1>Data</h1>
        <p>Source ingestion, export partitions and sink checkpoints.</p>
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
          <PanelCard title="Builds" subtitle="Recent catalog builds">
            <div class="panel-body">
              <BuildList
                :builds="builds?.builds ?? []"
                :selected-build-id="selectedBuildId"
                @select="$emit('selectBuild', $event)"
              />
            </div>
          </PanelCard>
        </aside>

        <section class="content">
          <PanelCard title="Source ingestion" subtitle="Rows and observation throughput by university">
            <SourceTaskChart :sources="metrics.observations_by_source" />
            <SourceTaskTable :sources="detail.sources" />
          </PanelCard>

          <PanelCard title="Export partitions" subtitle="Largest node/relationship partitions">
            <div class="panel-body">
              <ExportPartitionChart :partitions="metrics.export_partitions" />
            </div>
          </PanelCard>

          <PanelCard title="Checkpoints" subtitle="Read-only sink progress checkpoints">
            <CheckpointTable :checkpoints="detail.checkpoints" />
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
