import type { ComposeOption } from 'echarts/core'
import type { BarSeriesOption, GraphSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  TitleComponentOption,
  TooltipComponentOption
} from 'echarts/components'

export type ChartOption = ComposeOption<
  | BarSeriesOption
  | GraphSeriesOption
  | LineSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | LegendComponentOption
  | TooltipComponentOption
  | TitleComponentOption
>

/** Shared light-theme ECharts colors. Read these instead of hardcoding hex. */
export const palette = [
  '#2f6bff', '#3ee6b5', '#f6c85f', '#ff6b7a', '#9b8cff', '#4dd0e1', '#f59e6c'
]

export const chartTheme = {
  text: '#5b6b82',
  axisLine: 'rgba(30,58,110,0.22)',
  splitLine: 'rgba(30,58,110,0.08)',
  label: '#1a2333',
  edge: 'rgba(30,58,110,0.30)'
}
