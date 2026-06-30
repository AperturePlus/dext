<script setup lang="ts">
import { computed, ref } from 'vue'
import type { UniversityTopologyResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  topology: UniversityTopologyResponse | null
  minHeight?: number
}>()

// null = show all universities. A university graph_key filters to its cluster.
const selectedUniversity = ref<string | null>(null)

const KIND_CATEGORIES = ['University', 'college', 'institute', 'department', 'hospital']

function categoryIndex(node: { category: string; kind?: string }): number {
  if (node.category === 'University') return 0
  const idx = KIND_CATEGORIES.indexOf(node.kind || '')
  return idx > 0 ? idx : 1 // unknown kinds fall back to 'college' bucket
}

const filtered = computed(() => {
  const all = props.topology
  if (!all) return { nodes: [], links: [] }
  if (!selectedUniversity.value) return { nodes: all.nodes, links: all.links }
  const keep = new Set<string>([selectedUniversity.value])
  for (const n of all.nodes) {
    if (n.university === selectedUniversity.value) keep.add(n.id)
  }
  return {
    nodes: all.nodes.filter((n) => keep.has(n.id)),
    links: all.links.filter((l) => keep.has(l.source) && keep.has(l.target))
  }
})

function symbolSize(node: { category: string; professor_count: number }): number {
  if (node.category === 'University') return 44
  return 12 + Math.min(Math.max(Math.floor(node.professor_count / 30), 0), 24)
}

function nodeName(node: { category: string; label: string; professor_count: number }): string {
  if (node.category === 'University') return node.label
  return `${node.label} ·${node.professor_count}`
}

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: {},
  legend: { top: 0, textStyle: { color: chartTheme.text } },
  series: [
    {
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      categories: KIND_CATEGORIES.map((name, i) => ({
        name,
        // Color lives on the CATEGORY, not on a series-level itemStyle callback:
        // ECharts resolves a force-graph node's fill from its category's itemStyle.color,
        // and falls back to the series default (#000 black) when none is set.
        itemStyle: { color: palette[i % palette.length] }
      })),
      data: filtered.value.nodes.map((node) => ({
        id: node.id,
        name: nodeName(node),
        category: categoryIndex(node),
        symbolSize: symbolSize(node)
      })),
      links: filtered.value.links.map((link) => ({
        source: link.source,
        target: link.target,
        name: link.label
      })),
      force: { repulsion: 120, edgeLength: 60, gravity: 0.1 },
      label: {
        show: true,
        color: chartTheme.label,
        fontSize: 10,
        formatter: (params: { name: string }) =>
          params.name.length > 14 ? `${params.name.slice(0, 14)}…` : params.name
      },
      lineStyle: { color: chartTheme.edge, curveness: 0.18 }
    }
  ]
}))

function onSelect(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  selectedUniversity.value = value === '__all__' ? null : value
}
</script>

<template>
  <div>
    <select
      v-if="topology && topology.universities.length"
      class="uni-select"
      :value="selectedUniversity ?? '__all__'"
      @change="onSelect"
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
    <ChartFrame
      v-if="topology && topology.nodes.length"
      :option="option"
      :min-height="minHeight ?? 390"
    />
    <EmptyState
      v-else
      title="No topology rows"
      message="University→学院 topology is not available for the selected build yet."
    />
  </div>
</template>

<style scoped>
.uni-select {
  margin-bottom: 0.6rem;
  padding: 0.5rem 0.82rem;
  background: var(--surface-strong);
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

.uni-select option {
  background: var(--surface-strong);
  color: var(--text);
}
</style>
