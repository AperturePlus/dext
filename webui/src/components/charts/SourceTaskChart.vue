<script setup lang="ts">
import { computed } from 'vue'
import type { SourceMetric } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  sources: SourceMetric[]
}>()

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  legend: {
    textStyle: { color: chartTheme.text },
    top: 0
  },
  grid: { left: 24, right: 16, top: 44, bottom: 42, containLabel: true },
  xAxis: {
    type: 'category',
    data: props.sources.map((source) => source.abbr || source.university_name),
    axisLabel: { color: chartTheme.text },
    axisLine: { lineStyle: { color: chartTheme.axisLine } }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: chartTheme.text },
    splitLine: { lineStyle: { color: chartTheme.splitLine } }
  },
  series: [
    {
      name: 'Rows',
      type: 'bar',
      data: props.sources.map((source) => source.rows_read),
      itemStyle: { color: palette[0], borderRadius: [6, 6, 0, 0] }
    },
    {
      name: 'Observations',
      type: 'bar',
      data: props.sources.map((source) => source.observations_written),
      itemStyle: { color: palette[1], borderRadius: [6, 6, 0, 0] }
    }
  ]
}))
</script>

<template>
  <ChartFrame :option="option" :min-height="260" />
</template>
