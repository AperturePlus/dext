<script setup lang="ts">
import { computed } from 'vue'
import ChartFrame from './ChartFrame.vue'
import EmptyState from '../primitives/EmptyState.vue'
import type { ChartOption } from './options'

const props = defineProps<{
  counts: Record<string, number>
}>()

const entries = computed(() => Object.entries(props.counts))
const hasData = computed(() => entries.value.some(([, value]) => value > 0))

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
      color: ['#3ee6b5', '#70a7ff', '#f6c85f', '#ff6b7a', '#48d99a', '#a78bfa'],
      label: { color: '#e8f0ff' }
    }
  ]
}))
</script>

<template>
  <div>
    <ChartFrame v-if="hasData" :option="option" :min-height="240" />
    <EmptyState
      v-else
      title="No role distribution"
      message="Role counts are not available for the selected build yet."
    />
  </div>
</template>
