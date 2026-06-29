<script setup lang="ts">
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  GraphPreviewResponse,
  HealthResponse,
  MetricsResponse
} from '../types/monitor'
import { formatNumber, shortId } from '../utils/format'
import BuildStageTimeline from '../components/charts/BuildStageTimeline.vue'
import ExportPartitionChart from '../components/charts/ExportPartitionChart.vue'
import FindingSummaryChart from '../components/charts/FindingSummaryChart.vue'
import GraphNetworkPreview from '../components/charts/GraphNetworkPreview.vue'
import RoleDistributionChart from '../components/charts/RoleDistributionChart.vue'
import SourceTaskChart from '../components/charts/SourceTaskChart.vue'
import TitleFamilyChart from '../components/charts/TitleFamilyChart.vue'
import BuildList from '../components/features/BuildList.vue'
import CheckpointTable from '../components/features/CheckpointTable.vue'
import FindingSummary from '../components/features/FindingSummary.vue'
import PanelCard from '../components/features/PanelCard.vue'
import RunSummary from '../components/features/RunSummary.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'
import MetricCard from '../components/primitives/MetricCard.vue'
import RefreshControl from '../components/primitives/RefreshControl.vue'
import SourceTaskTable from '../components/features/SourceTaskTable.vue'
import StatusBadge from '../components/primitives/StatusBadge.vue'

defineProps<{
  health: HealthResponse | null
  builds: BuildsResponse | null
  detail: BuildDetailResponse | null
  metrics: MetricsResponse | null
  graph: GraphPreviewResponse | null
  findings: Finding[]
  selectedBuildId: string | null
  error: string | null
  paused: boolean
  lastUpdated: Date | null
}>()

defineEmits<{
  refresh: []
  togglePause: []
  selectBuild: [buildId: string]
}>()
</script>

<template>
  <main class="dashboard">
    <header class="hero">
      <div>
        <div class="brand-row">
          <span class="brand-mark">dx</span>
          <span>dext monitor</span>
        </div>
        <h1>Catalog graph build observability</h1>
        <p>
          Read-only visibility into graph build health, source ingestion, curation output,
          export partitions and preview topology.
        </p>
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
          <PanelCard title="Findings" subtitle="Unresolved quality signals">
            <FindingSummary :counts="detail.unresolved_findings" :findings="findings" />
          </PanelCard>
          <PanelCard title="Finding severity" subtitle="Open findings only">
            <div class="panel-body">
              <FindingSummaryChart :counts="detail.unresolved_findings" />
            </div>
          </PanelCard>
          <PanelCard title="Role distribution" subtitle="Canonical entity roles">
            <div class="panel-body">
              <RoleDistributionChart :counts="metrics.role_counts ?? {}" />
            </div>
          </PanelCard>
        </aside>

        <section class="content">
          <PanelCard title="Build stage" subtitle="Current phase and completed pipeline steps">
            <div class="panel-body">
              <BuildStageTimeline :stages="metrics.stage" />
            </div>
          </PanelCard>

          <PanelCard title="Source ingestion" subtitle="Rows and observation throughput by university">
            <SourceTaskChart :sources="metrics.observations_by_source" />
            <SourceTaskTable :sources="detail.sources" />
          </PanelCard>

          <PanelCard title="Graph preview" subtitle="Limited sample from frozen graph export rows">
            <div class="panel-body">
              <GraphNetworkPreview :graph="graph" />
            </div>
          </PanelCard>

          <section class="split">
            <PanelCard title="Export partitions" subtitle="Largest node/relationship partitions">
              <div class="panel-body">
                <ExportPartitionChart :partitions="metrics.export_partitions" />
              </div>
            </PanelCard>
            <PanelCard title="Title families" subtitle="Top normalized title families">
              <div class="panel-body">
                <TitleFamilyChart :counts="metrics.title_family_counts ?? {}" />
              </div>
            </PanelCard>
          </section>

          <PanelCard title="Runs" subtitle="Curation and graph materialization">
            <div class="run-grid panel-body">
              <RunSummary label="Curation" :run="detail.curation" />
              <RunSummary label="Graph" :run="detail.graph" />
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
.dashboard {
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

.metric-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 0.9rem;
  margin-bottom: 1rem;
}

.layout {
  display: grid;
  grid-template-columns: minmax(280px, 360px) 1fr;
  gap: 1rem;
  align-items: start;
}

.sidebar,
.content {
  display: grid;
  gap: 1rem;
}

.split {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(320px, 0.65fr);
  gap: 1rem;
}

.run-grid {
  display: grid;
  gap: 0.8rem;
}

@media (max-width: 1180px) {
  .metric-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .layout,
  .split {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 760px) {
  .dashboard {
    width: min(100% - 1rem, 1680px);
  }

  .hero {
    display: grid;
  }

  .hero-actions {
    justify-items: start;
    min-width: 0;
  }

  .metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>
