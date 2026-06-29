<script setup lang="ts">
import { computed } from 'vue'
import type { GraphPreviewResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import type { ChartOption } from './options'

const props = defineProps<{
  graph: GraphPreviewResponse | null
}>()

const categories = computed(() => {
  const names = Array.from(new Set(props.graph?.nodes.map((node) => node.category) ?? []))
  return names.map((name) => ({ name }))
})

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: {},
  legend: {
    top: 0,
    textStyle: { color: '#92a4bd' }
  },
  series: [
    {
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      categories: categories.value,
      data:
        props.graph?.nodes.map((node) => ({
          id: node.id,
          name: node.label,
          category: categories.value.findIndex((category) => category.name === node.category),
          symbolSize: node.category === 'University' ? 44 : node.category === 'Professor' ? 20 : 30
        })) ?? [],
      links:
        props.graph?.links.map((link) => ({
          source: link.source,
          target: link.target,
          name: link.label
        })) ?? [],
      force: {
        repulsion: 160,
        edgeLength: 78
      },
      label: {
        show: true,
        color: '#e8f0ff',
        fontSize: 10,
        formatter: (params: { name: string }) =>
          params.name.length > 12 ? `${params.name.slice(0, 12)}…` : params.name
      },
      lineStyle: {
        color: 'rgba(146,164,189,0.55)',
        curveness: 0.18
      },
      itemStyle: {
        color: '#3ee6b5'
      }
    }
  ]
}))
</script>

<template>
  <div>
    <ChartFrame v-if="graph && graph.nodes.length" :option="option" :min-height="390" />
    <EmptyState
      v-else
      title="No graph preview rows"
      message="Graph export rows are not available for the selected build yet."
    />
    <p v-if="graph?.truncated" class="preview-note">
      Preview is truncated: {{ graph.nodes.length }} / {{ graph.total_nodes }} nodes and
      {{ graph.links.length }} / {{ graph.total_relationships }} relationships are shown.
    </p>
  </div>
</template>

<style scoped>
.preview-note {
  margin: 0.7rem 0 0;
  color: var(--subtle);
  font-size: 0.84rem;
}
</style>
