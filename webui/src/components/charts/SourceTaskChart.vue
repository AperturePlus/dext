<script setup lang="ts">
import { computed } from 'vue'
import type { SourceMetric } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import type { ChartOption } from './options'

const props = defineProps<{
  sources: SourceMetric[]
}>()

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  legend: {
    textStyle: { color: '#92a4bd' },
    top: 0
  },
  grid: { left: 24, right: 16, top: 44, bottom: 42, containLabel: true },
  xAxis: {
    type: 'category',
    data: props.sources.map((source) => source.abbr || source.university_name),
    axisLabel: { color: '#92a4bd' },
    axisLine: { lineStyle: { color: 'rgba(136,162,199,0.28)' } }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#92a4bd' },
    splitLine: { lineStyle: { color: 'rgba(136,162,199,0.12)' } }
  },
  series: [
    {
      name: 'Rows',
      type: 'bar',
      data: props.sources.map((source) => source.rows_read),
      itemStyle: { color: '#70a7ff', borderRadius: [6, 6, 0, 0] }
    },
    {
      name: 'Observations',
      type: 'bar',
      data: props.sources.map((source) => source.observations_written),
      itemStyle: { color: '#3ee6b5', borderRadius: [6, 6, 0, 0] }
    }
  ]
}))
</script>

<template>
  <ChartFrame :option="option" :min-height="260" />
</template>
