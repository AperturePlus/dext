<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import type { ChartOption } from './options'

const props = defineProps<{
  counts: Record<string, number>
}>()

const entries = computed(() =>
  Object.entries(props.counts)
    .filter(([, value]) => value > 0)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 12)
)
const hasData = computed(() => entries.value.length > 0)

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  grid: { left: 24, right: 16, top: 18, bottom: 20, containLabel: true },
  xAxis: {
    type: 'value',
    axisLabel: { color: '#92a4bd' },
    splitLine: { lineStyle: { color: 'rgba(136,162,199,0.12)' } }
  },
  yAxis: {
    type: 'category',
    data: entries.value.map(([name]) => name),
    axisLabel: { color: '#92a4bd' },
    axisLine: { lineStyle: { color: 'rgba(136,162,199,0.28)' } }
  },
  series: [
    {
      name: 'Count',
      type: 'bar',
      data: entries.value.map(([, value]) => value),
      itemStyle: {
        color: '#70a7ff',
        borderRadius: [0, 7, 7, 0]
      }
    }
  ]
}))
</script>

<template>
  <div>
    <ChartFrame v-if="hasData" :option="option" :min-height="280" />
    <EmptyState
      v-else
      title="No title-family distribution"
      message="Title family counts are not available for the selected build yet."
    />
  </div>
</template>
