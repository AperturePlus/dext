<script setup lang="ts">
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'
import { formatNumber, shortId } from '../utils/format'
import type { ThroughputSample } from '../composables/useMonitorData'
import BuildStageTimeline from '../components/charts/BuildStageTimeline.vue'
import ThroughputSparkline from '../components/charts/ThroughputSparkline.vue'
import PanelCard from '../components/features/PanelCard.vue'
import RunSummary from '../components/features/RunSummary.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'
import MetricCard from '../components/primitives/MetricCard.vue'

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
        <h1>Overview</h1>
        <p>Build health, pipeline stage, throughput and run progress at a glance.</p>
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <EmptyState
      v-if="builds && builds.builds.length === 0"
      title="No catalog builds"
      message="Run a graph build first, then refresh this monitor."
    />

    <template v-if="detail && metrics">
      <section class="metric-grid">
        <MetricCard label="Build status" :value="detail.build.status" tone="accent" :hint="shortId(detail.build.id)" />
        <MetricCard label="Sources" :value="formatNumber(detail.build.summary.source_count)" />
        <MetricCard label="Canonical professors" :value="formatNumber(detail.build.summary.canonical_active)" tone="accent" />
        <MetricCard
          label="Unresolved findings"
          :value="formatNumber(detail.build.summary.unresolved_findings)"
          :tone="detail.build.summary.unresolved_findings ? 'warning' : 'default'"
        />
        <MetricCard label="Rows read" :value="formatNumber(detail.build.summary.rows_read)" />
        <MetricCard label="Graph export rows" :value="formatNumber(detail.build.summary.graph_export_rows)" tone="accent" />
      </section>

      <section class="content">
        <PanelCard title="Build stage" subtitle="Current phase and completed pipeline steps">
          <div class="panel-body">
            <BuildStageTimeline :stages="metrics.stage" />
          </div>
        </PanelCard>

        <PanelCard title="Throughput" subtitle="Rows read and observations written over recent polls">
          <div class="panel-body">
            <ThroughputSparkline :history="history" />
          </div>
        </PanelCard>

        <PanelCard title="Runs" subtitle="Curation and graph materialization">
          <div class="run-grid panel-body">
            <RunSummary label="Curation" :run="detail.curation" />
            <RunSummary label="Graph" :run="detail.graph" />
          </div>
        </PanelCard>
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
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
  gap: 0.9rem; margin-bottom: 1rem;
}
.content { display: grid; gap: 1rem; }
.run-grid { display: grid; gap: 0.8rem; }
:deep(.panel-body) { min-width: 0; overflow: hidden; }
@media (max-width: 760px) {
  .page { width: min(100% - 1rem, 1680px); }
  .metric-grid { grid-template-columns: 1fr; }
}
</style>
