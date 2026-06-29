import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const stubs = vi.hoisted(() => ({
  ChartFrame: {
    name: 'ChartFrame',
    props: ['option', 'minHeight'],
    template: '<div class="chart-stub" />'
  },
  EmptyState: {
    name: 'EmptyState',
    props: ['title', 'message'],
    template: '<div class="empty-stub">{{ title }}</div>'
  }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

import ThroughputSparkline from '../src/components/charts/ThroughputSparkline.vue'
import type { ThroughputSample } from '../src/composables/useMonitorData'

function sample(rows: number, obs: number): ThroughputSample {
  return { at: new Date(`2026-06-29T00:0${rows % 10}:00Z`), rowsRead: rows, observations: obs, documents: rows }
}

describe('ThroughputSparkline', () => {
  it('renders ChartFrame with two line series (rows + observations) over the sample history', () => {
    const history = [sample(10, 4), sample(20, 9), sample(35, 15)]
    const wrapper = mount(ThroughputSparkline, {
      props: { history },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const option = frame.props('option') as {
      xAxis: { data: string[] }
      series: Array<{ type: string; name: string; data: number[] }>
    }
    expect(option.series).toHaveLength(2)
    expect(option.series[0].type).toBe('line')
    expect(option.series[0].name).toBe('Rows read')
    expect(option.series[0].data).toEqual([10, 20, 35])
    expect(option.series[1].name).toBe('Observations')
    expect(option.series[1].data).toEqual([4, 9, 15])
    // One x-axis category per sample.
    expect(option.xAxis.data).toHaveLength(3)
  })

  it('shows EmptyState when history is empty', () => {
    const wrapper = mount(ThroughputSparkline, {
      props: { history: [] },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
  })
})
