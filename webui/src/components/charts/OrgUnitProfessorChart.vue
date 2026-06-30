<script setup lang="ts">
import { computed } from 'vue'
import type { OrgUnitProfessorResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  subgraph: OrgUnitProfessorResponse | null
  minHeight?: number
}>()

const hasProfessors = computed(
  () => !!props.subgraph && props.subgraph.professors.length > 0
)

const option = computed<ChartOption>(() => {
  const sg = props.subgraph
  if (!sg) return {}
  const orgNode = {
    id: sg.orgunit.graph_key,
    name: sg.orgunit.label,
    category: 0,
    symbolSize: 56,
    itemStyle: { color: palette[0] }
  }
  const profNodes = sg.professors.map((p, i) => ({
    id: p.graph_key,
    name: `${p.name}·${p.title_family ?? p.role_status ?? ''}`,
    category: 1,
    symbolSize: 22,
    itemStyle: { color: palette[i % palette.length] },
    tooltip: `${p.name} · ${p.title ?? ''} · ${p.role_status}`
  }))
  return {
    backgroundColor: 'transparent',
    tooltip: {},
    legend: { top: 0, textStyle: { color: chartTheme.text }, data: ['学院', '教师'] },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: [{ name: '学院' }, { name: '教师' }],
        data: [orgNode, ...profNodes],
        links: sg.links.map((l) => ({ source: l.source, target: l.target, name: l.label })),
        force: { repulsion: 140, edgeLength: 70, gravity: 0.12 },
        label: {
          show: true,
          color: chartTheme.label,
          fontSize: 11,
          formatter: (params: { name: string }) =>
            params.name.length > 12 ? `${params.name.slice(0, 12)}…` : params.name
        },
        lineStyle: { color: chartTheme.edge, curveness: 0.12 },
        edgeSymbol: ['none', 'arrow']
      }
    ]
  }
})
</script>

<template>
  <ChartFrame v-if="hasProfessors" :option="option" :min-height="minHeight ?? 460" />
  <EmptyState
    v-else
    title="No professors"
    message="This college has no affiliated professors in the selected build."
  />
</template>
