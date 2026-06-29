import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Stub ChartFrame so we don't need a real echarts runtime in jsdom, and we
// can assert on the option prop the chart passes through. vi.hoisted so the
// stubs exist when vi.mock's hoisted factory runs.
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

import RoleDistributionChart from '../src/components/charts/RoleDistributionChart.vue'
import TitleFamilyChart from '../src/components/charts/TitleFamilyChart.vue'

describe('RoleDistributionChart', () => {
  it('renders ChartFrame with a pie series whose data mirrors role_counts', () => {
    const counts = { professor: 120, associate_professor: 40, lecturer: 8 }
    const wrapper = mount(RoleDistributionChart, {
      props: { counts },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const series = (frame.props('option') as { series: Array<{ type: string; data: { name: string; value: number }[] }> }).series
    expect(series[0].type).toBe('pie')
    expect(series[0].data).toEqual(
      expect.arrayContaining([
        { name: 'professor', value: 120 },
        { name: 'associate_professor', value: 40 },
        { name: 'lecturer', value: 8 }
      ])
    )
  })

  it('shows EmptyState when counts is empty', () => {
    const wrapper = mount(RoleDistributionChart, {
      props: { counts: {} },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
  })
})

describe('TitleFamilyChart', () => {
  it('renders ChartFrame with a bar series whose data mirrors title_family_counts', () => {
    const counts = { '教授': 100, '副教授': 50, '讲师': 10 }
    const wrapper = mount(TitleFamilyChart, {
      props: { counts },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const option = frame.props('option') as {
      yAxis: { data: string[] }
      series: Array<{ type: string; data: number[] }>
    }
    expect(option.series[0].type).toBe('bar')
    // y-axis category labels are the title-family names.
    expect(option.yAxis.data).toEqual(expect.arrayContaining(['教授', '副教授', '讲师']))
    // series data values are the counts (order matches yAxis.data).
    expect(option.series[0].data).toHaveLength(3)
    expect(option.series[0].data).toEqual(expect.arrayContaining([100, 50, 10]))
  })

  it('shows EmptyState when counts is empty', () => {
    const wrapper = mount(TitleFamilyChart, {
      props: { counts: {} },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
  })
})
