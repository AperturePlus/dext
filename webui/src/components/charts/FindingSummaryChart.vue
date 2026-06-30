<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
import { chartTheme, palette } from './options'
import type { ChartOption } from './options'

const props = defineProps<{
  counts: Record<string, number>
}>()

const entries = computed(() => Object.entries(props.counts))

const option = computed<ChartOption>(() => ({
  backgroundColor: 'transparent',
  tooltip: { trigger: 'item' },
  legend: {
    bottom: 0,
    textStyle: { color: chartTheme.text }
  },
  series: [
    {
      type: 'pie',
      radius: ['48%', '70%'],
      center: ['50%', '44%'],
      data: entries.value.map(([name, value]) => ({ name, value })),
      color: palette,
      label: { color: chartTheme.label }
    }
  ]
}))
</script>

<template>
  <ChartFrame :option="option" :min-height="240" />
</template>
