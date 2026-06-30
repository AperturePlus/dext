<script setup lang="ts">
import { computed } from 'vue'
import type { ExportPartition } from '../../types/monitor'
import ChartFrame from './ChartFrame.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  partitions: ExportPartition[]
}>()

const sorted = computed(() =>
  [...props.partitions].sort((left, right) => right.row_count - left.row_count).slice(0, 12)
)

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  grid: { left: 24, right: 16, top: 18, bottom: 20, containLabel: true },
  xAxis: {
    type: 'value',
    axisLabel: { color: chartTheme.text },
    splitLine: { lineStyle: { color: chartTheme.splitLine } }
  },
  yAxis: {
    type: 'category',
    data: sorted.value.map((item) => item.partition_key),
    axisLabel: { color: chartTheme.text },
    axisLine: { lineStyle: { color: chartTheme.axisLine } }
  },
  series: [
    {
      name: 'Rows',
      type: 'bar',
      data: sorted.value.map((item) => item.row_count),
      itemStyle: {
        color: (params: { dataIndex: number }) =>
          sorted.value[params.dataIndex]?.row_kind === 'node' ? palette[1] : palette[0],
        borderRadius: [0, 7, 7, 0]
      }
    }
  ]
}))
</script>

<template>
  <ChartFrame :option="option" :min-height="320" />
</template>
