<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, GraphChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
  TitleComponent
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ChartOption } from './options'

echarts.use([
  BarChart,
  GraphChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  TitleComponent,
  CanvasRenderer
])

const props = defineProps<{
  option: ChartOption
  minHeight?: number
}>()

const container = ref<HTMLDivElement | null>(null)
let instance: echarts.ECharts | null = null

function resize() {
  instance?.resize()
}

onMounted(() => {
  if (!container.value) return
  instance = echarts.init(container.value, 'dark')
  instance.setOption(props.option)
  window.addEventListener('resize', resize)
})

watch(
  () => props.option,
  (option) => instance?.setOption(option, true),
  { deep: true }
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  instance?.dispose()
  instance = null
})
</script>

<template>
  <div ref="container" class="chart-frame" :style="{ minHeight: `${minHeight ?? 280}px` }" />
</template>

<style scoped>
.chart-frame {
  width: 100%;
}
</style>
