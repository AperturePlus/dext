<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
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
    textStyle: { color: '#92a4bd' }
  },
  series: [
    {
      type: 'pie',
      radius: ['48%', '70%'],
      center: ['50%', '44%'],
      data: entries.value.map(([name, value]) => ({ name, value })),
      color: ['#ff6b7a', '#f6c85f', '#70a7ff', '#3ee6b5'],
      label: { color: '#e8f0ff' }
    }
  ]
}))
</script>

<template>
  <ChartFrame :option="option" :min-height="240" />
</template>
