<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type {
  BuildDetailResponse,
  BuildsResponse,
  Finding,
  HealthResponse,
  MetricsResponse,
  OrgUnitProfessorResponse,
  ProfessorTopicResponse,
  UniversityTopologyResponse
} from '../types/monitor'
import type { ThroughputSample } from '../composables/useMonitorData'
import { monitorApi } from '../services/api'
import UniversityTopologyChart from '../components/charts/UniversityTopologyChart.vue'
import OrgUnitProfessorChart from '../components/charts/OrgUnitProfessorChart.vue'
import ProfessorTopicChart from '../components/charts/ProfessorTopicChart.vue'
import PanelCard from '../components/features/PanelCard.vue'
import EmptyState from '../components/primitives/EmptyState.vue'
import ErrorPanel from '../components/primitives/ErrorPanel.vue'

const props = defineProps<{
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

const selectedUniversity = ref<string | null>(null)
const selectedCollege = ref<string | null>(null)
const selectedProfessor = ref<string | null>(null)
const subgraph = ref<OrgUnitProfessorResponse | null>(null)
const subgraphError = ref<string | null>(null)
const topicSubgraph = ref<ProfessorTopicResponse | null>(null)
const topicSubgraphError = ref<string | null>(null)

// Colleges belonging to the selected university (from graph_tree nodes).
const colleges = computed(() => {
  const all = props.topology
  if (!all || !selectedUniversity.value) return []
  return all.nodes.filter(
    (n) => n.category === 'OrgUnit' && n.university === selectedUniversity.value
  )
})

const professors = computed(() => subgraph.value?.professors ?? [])

const panelTitle = computed(() => {
  if (selectedProfessor.value) return 'Professor topics'
  if (selectedCollege.value) return 'College subgraph'
  return 'University topology'
})

const panelSubtitle = computed(() => {
  if (selectedProfessor.value) return '教师 → Topic with approved research-topic edges'
  if (selectedCollege.value) return '学院 → 教师 with AFFILIATED_WITH edges'
  return '大学 → 学院 with professor counts'
})

function resetProfessorDrilldown() {
  selectedProfessor.value = null
  topicSubgraph.value = null
  topicSubgraphError.value = null
}

function onUniversityChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedUniversity.value = value === '__all__' ? null : value
  // Changing university clears the college drill-down (a college belongs to one university).
  selectedCollege.value = null
  subgraph.value = null
  subgraphError.value = null
  resetProfessorDrilldown()
}

async function onCollegeChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedCollege.value = value === '__none__' ? null : value
  subgraph.value = null
  subgraphError.value = null
  resetProfessorDrilldown()
  if (!selectedCollege.value || !props.selectedBuildId) return
  try {
    subgraph.value = await monitorApi.orgUnitProfessors(props.selectedBuildId, selectedCollege.value)
  } catch (e) {
    subgraphError.value = e instanceof Error ? e.message : String(e)
  }
}

async function onProfessorChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedProfessor.value = value === '__none__' ? null : value
  topicSubgraph.value = null
  topicSubgraphError.value = null
  if (!selectedProfessor.value || !props.selectedBuildId) return
  try {
    topicSubgraph.value = await monitorApi.professorTopics(props.selectedBuildId, selectedProfessor.value)
  } catch (e) {
    topicSubgraphError.value = e instanceof Error ? e.message : String(e)
  }
}

function backToUniversityView() {
  selectedCollege.value = null
  subgraph.value = null
  subgraphError.value = null
  resetProfessorDrilldown()
}

// If the build changes out from under us, drop a stale drill-down.
watch(
  () => props.selectedBuildId,
  () => {
    selectedUniversity.value = null
    selectedCollege.value = null
    subgraph.value = null
    subgraphError.value = null
    resetProfessorDrilldown()
  }
)

watch(
  () => props.topology?.export_pruned,
  (exportPruned) => {
    if (!exportPruned) return
    selectedUniversity.value = null
    selectedCollege.value = null
    subgraph.value = null
    subgraphError.value = null
    resetProfessorDrilldown()
  }
)
</script>

<template>
  <main class="page">
    <header class="hero">
      <div>
        <div class="brand-row"><span class="brand-mark">dx</span><span>dext monitor</span></div>
        <h1>University topology</h1>
        <p>Pick a university, then a college, to see that college's teachers and their affiliation edges.</p>
      </div>
    </header>

    <ErrorPanel v-if="error" :message="error" />

    <PanelCard
      :title="panelTitle"
      :subtitle="panelSubtitle"
    >
      <div class="panel-body">
        <div class="controls">
          <select
            v-if="topology && !topology.export_pruned && topology.universities.length"
            class="uni-select"
            :value="selectedUniversity ?? '__all__'"
            @change="onUniversityChange"
          >
            <option value="__all__">全部大学</option>
            <option
              v-for="uni in topology.universities"
              :key="uni.graph_key"
              :value="uni.graph_key"
            >
              {{ uni.name }} ({{ uni.orgunit_count }}学院 / {{ uni.professor_count }}教授)
            </option>
          </select>

          <select
            v-if="topology && !topology.export_pruned && topology.universities.length"
            class="uni-select"
            :disabled="!selectedUniversity"
            :value="selectedCollege ?? '__none__'"
            @change="onCollegeChange"
          >
            <option value="__none__">选择学院</option>
            <option v-for="org in colleges" :key="org.id" :value="org.id">
              {{ org.label }} ·{{ org.professor_count }}
            </option>
          </select>

          <select
            v-if="selectedCollege && subgraph"
            class="uni-select"
            :disabled="!professors.length"
            :value="selectedProfessor ?? '__none__'"
            @change="onProfessorChange"
          >
            <option value="__none__">选择老师</option>
            <option v-for="professor in professors" :key="professor.graph_key" :value="professor.graph_key">
              {{ professor.name }}{{ professor.title ? ' · ' + professor.title : '' }}
            </option>
          </select>

          <button v-if="selectedCollege" class="back-btn" @click="backToUniversityView">
            返回大学视图
          </button>
        </div>

        <ErrorPanel v-if="subgraphError" :message="subgraphError" />
        <ErrorPanel v-if="topicSubgraphError" :message="topicSubgraphError" />

        <ProfessorTopicChart
          v-if="selectedProfessor"
          :subgraph="topicSubgraph"
        />
        <OrgUnitProfessorChart
          v-else-if="selectedCollege"
          :subgraph="subgraph"
        />
        <EmptyState
          v-else-if="topology?.export_pruned"
          title="Graph exports compacted"
          message="Resume this build to rebuild its cached topology exports."
        />
        <EmptyState
          v-else-if="!topology || !topology.nodes.length"
          title="No topology rows"
          message="University→学院 topology is not available for the selected build yet."
        />
        <UniversityTopologyChart
          v-else
          :topology="topology"
          :selected-university="selectedUniversity"
          :min-height="560"
        />
      </div>
    </PanelCard>
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
.panel-body { padding: 1rem; }
.controls {
  display: flex; flex-wrap: wrap; gap: 0.6rem; align-items: center; margin-bottom: 0.8rem;
}
.uni-select {
  padding: 0.5rem 0.82rem;
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
}
.uni-select:focus {
  outline: none;
  box-shadow: 0 0 0 2px rgba(47, 107, 255, 0.35);
  border-color: var(--accent);
}
.uni-select:disabled { opacity: 0.5; cursor: not-allowed; }
.uni-select option { background: var(--surface); color: var(--text); }
.back-btn {
  padding: 0.5rem 0.9rem;
  background: var(--surface-soft);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 700;
  cursor: pointer;
}
.back-btn:hover { background: var(--surface); border-color: var(--border-strong); }
@media (max-width: 760px) {
  .page { width: min(100% - 1rem, 1680px); }
  .controls { flex-direction: column; align-items: stretch; }
}
</style>
