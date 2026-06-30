<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import type { ThroughputSample } from '../../composables/useMonitorData'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  history: ThroughputSample[]
}>()

const hasData = computed(() => props.history.length > 0)
const labels = computed(() => props.history.map((sample) => sample.at.toLocaleTimeString()))

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  legend: {
    textStyle: { color: chartTheme.text },
    top: 0
  },
  grid: { left: 24, right: 16, top: 36, bottom: 28, containLabel: true },
  xAxis: {
    type: 'category',
    boundaryGap: false,
    data: labels.value,
    axisLabel: { color: chartTheme.text, fontSize: 10 },
    axisLine: { lineStyle: { color: chartTheme.axisLine } }
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: chartTheme.text },
    splitLine: { lineStyle: { color: chartTheme.splitLine } }
  },
  series: [
    {
      name: 'Rows read',
      type: 'line',
      data: props.history.map((sample) => sample.rowsRead),
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      showSymbol: false,
      lineStyle: { color: palette[0], width: 2 },
      itemStyle: { color: palette[0] }
    },
    {
      name: 'Observations',
      type: 'line',
      data: props.history.map((sample) => sample.observations),
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      showSymbol: false,
      lineStyle: { color: palette[1], width: 2 },
      itemStyle: { color: palette[1] }
    }
  ]
}))
</script>

<template>
  <div>
    <ChartFrame v-if="hasData" :option="option" :min-height="180" />
    <EmptyState
      v-else
      title="No throughput history yet"
      message="Samples accumulate as the monitor polls the selected build."
    />
  </div>
</template>
