<script setup lang="ts">
import { computed } from 'vue'
import type { ProfessorTopicResponse } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  subgraph: ProfessorTopicResponse | null
  minHeight?: number
}>()

const hasTopics = computed(
  () => !!props.subgraph && props.subgraph.topics.length > 0
)

function formatConfidence(value: number | null): string {
  return value === null ? '-' : value.toFixed(2)
}

function tooltipFormatter(params: unknown): string {
  const item = Array.isArray(params) ? params[0] : params
  if (!item || typeof item !== 'object') return ''
  const data = (item as { data?: unknown }).data
  if (data && typeof data === 'object') {
    const tooltip = (data as { tooltip?: unknown }).tooltip
    if (typeof tooltip === 'string') return tooltip
  }
  const name = (item as { name?: unknown }).name
  return typeof name === 'string' ? name : ''
}

const option = computed<ChartOption>(() => {
  const sg = props.subgraph
  if (!sg) return {}
  const professorNode = {
    id: sg.professor.graph_key,
    name: sg.professor.name,
    category: 0,
    symbolSize: 58,
    itemStyle: { color: palette[0] },
    tooltip:
      sg.professor.name +
      '<br/>职称: ' + (sg.professor.title ?? '-') +
      '<br/>状态: ' + sg.professor.role_status
  }
  const linksByTopic = new Map<string, typeof sg.links>()
  for (const link of sg.links) {
    const links = linksByTopic.get(link.target) ?? []
    links.push(link)
    linksByTopic.set(link.target, links)
  }
  const topicNodes = sg.topics.map((topic, i) => {
    const links = linksByTopic.get(topic.graph_key) ?? []
    const relationText = links
      .map(
        (link) =>
          link.label +
          ' · evidence ' + link.evidence_count +
          ' · confidence ' + formatConfidence(link.confidence)
      )
      .join('<br/>')
    return {
      id: topic.graph_key,
      name: topic.canonical_name,
      category: 1,
      symbolSize: 28,
      itemStyle: { color: palette[(i + 1) % palette.length] },
      tooltip:
        topic.canonical_name +
        '<br/>类型: ' + (topic.kind || '-') +
        '<br/>版本: ' + (topic.taxonomy_version || '-') +
        (relationText ? '<br/>' + relationText : '')
    }
  })
  return {
    backgroundColor: 'transparent',
    tooltip: {
      formatter: tooltipFormatter
    },
    legend: { top: 0, textStyle: { color: chartTheme.text }, data: ['教师', 'Topic'] },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories: [{ name: '教师' }, { name: 'Topic' }],
        data: [professorNode, ...topicNodes],
        links: sg.links.map((link) => ({
          source: link.source,
          target: link.target,
          name: link.label,
          value: link.evidence_count,
          tooltip:
            link.label +
            '<br/>evidence: ' + link.evidence_count +
            '<br/>confidence: ' + formatConfidence(link.confidence)
        })),
        force: { repulsion: 160, edgeLength: 90, gravity: 0.1 },
        label: {
          show: true,
          color: chartTheme.label,
          fontSize: 11,
          formatter: (params: { name: string }) =>
            params.name.length > 14 ? params.name.slice(0, 14) + '…' : params.name
        },
        lineStyle: { color: chartTheme.edge, curveness: 0.14 },
        edgeSymbol: ['none', 'arrow']
      }
    ]
  }
})
</script>

<template>
  <ChartFrame v-if="hasTopics" :option="option" :min-height="minHeight ?? 460" />
  <EmptyState
    v-else
    title="No related topics"
    message="该老师在选中 build 中暂无已导出的 approved Topic 关系。"
  />
</template>
