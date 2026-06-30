<script setup lang="ts">
import { computed } from 'vue'
import type { UniversityTopologyResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  topology: UniversityTopologyResponse | null
  selectedUniversity: string | null
  minHeight?: number
}>()

const KIND_CATEGORIES = ['University', 'college', 'institute', 'department', 'hospital']

function categoryIndex(node: { category: string; kind?: string }): number {
  if (node.category === 'University') return 0
  const idx = KIND_CATEGORIES.indexOf(node.kind || '')
  return idx > 0 ? idx : 1 // unknown kinds fall back to 'college' bucket
}

const filtered = computed(() => {
  const all = props.topology
  if (!all) return { nodes: [], links: [] }
  if (!props.selectedUniversity) return { nodes: all.nodes, links: all.links }
  const keep = new Set<string>([props.selectedUniversity])
  for (const n of all.nodes) {
    if (n.university === props.selectedUniversity) keep.add(n.id)
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
</script>

<template>
  <div>
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
