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
