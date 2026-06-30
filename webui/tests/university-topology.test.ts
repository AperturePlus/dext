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

import UniversityTopologyChart from '../src/components/charts/UniversityTopologyChart.vue'
import type { UniversityTopologyResponse } from '../src/types/monitor'

function buildTopology(): UniversityTopologyResponse {
  return {
    build_id: 'b1',
    universities: [
      { graph_key: 'u', name: '大学A', logical_id: 'univ:a', orgunit_count: 1, professor_count: 2 },
      { graph_key: 'u2', name: '大学B', logical_id: 'univ:b', orgunit_count: 1, professor_count: 1 }
    ],
    nodes: [
      { id: 'u', label: '大学A', category: 'University', professor_count: 2, orgunit_count: 1 },
      { id: 'u2', label: '大学B', category: 'University', professor_count: 1, orgunit_count: 1 },
      { id: 'org', label: '学院A1', category: 'OrgUnit', kind: 'college', professor_count: 2, university: 'u' },
      { id: 'org2', label: '研究所B1', category: 'OrgUnit', kind: 'institute', professor_count: 1, university: 'u2' }
    ],
    links: [
      { source: 'org', target: 'u', label: 'PART_OF' },
      { source: 'org2', target: 'u2', label: 'PART_OF' }
    ]
  }
}

describe('UniversityTopologyChart', () => {
  it('renders all universities + orgunits + PART_OF links when no university is selected', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const series = (frame.props('option') as { series: Array<{ data: Array<{ id: string; name: string }>; links: Array<{ source: string; target: string }> }> }).series[0]
    expect(series.data.map((n) => n.id).sort()).toEqual(['org', 'org2', 'u', 'u2'])
    expect(series.links).toHaveLength(2)
  })

  it('filters to the selected university cluster', async () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    // select 大学B (graph_key 'u2') via the dropdown
    await wrapper.find('select').setValue('u2')
    const series = (wrapper.findComponent(stubs.ChartFrame).props('option') as { series: Array<{ data: Array<{ id: string }>; links: Array<{ source: string }> }> }).series[0]
    expect(series.data.map((n) => n.id).sort()).toEqual(['org2', 'u2'])
    expect(series.links).toHaveLength(1)
    expect(series.links[0].source).toBe('org2')
  })

  it('shows EmptyState when topology is null', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: null },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
  })

  it('labels OrgUnit nodes with name + professor count', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const series = (wrapper.findComponent(stubs.ChartFrame).props('option') as { series: Array<{ data: Array<{ id: string; name: string }> }> }).series[0]
    const org = series.data.find((n) => n.id === 'org')
    expect(org?.name).toBe('学院A1 ·2')
    const uni = series.data.find((n) => n.id === 'u')
    expect(uni?.name).toBe('大学A')
  })

  // Regression: nodes used to render black because color was declared as a series-level
  // itemStyle.color callback while the categories[] carried no color. ECharts resolves a
  // force-graph node's color from its CATEGORY's itemStyle.color; with none set it falls back
  // to the series default (#000). The fix is to put itemStyle.color on each category entry.
  it('declares a color on every category so nodes are not left to the black default', () => {
    const wrapper = mount(UniversityTopologyChart, {
      props: { topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const series = (wrapper.findComponent(stubs.ChartFrame).props('option') as {
      series: Array<{ categories?: Array<{ name?: string; itemStyle?: { color?: string } }> }>
    }).series[0]
    const categories = series.categories ?? []
    expect(categories.length).toBeGreaterThan(0)
    for (const cat of categories) {
      expect(cat.itemStyle?.color, `category "${cat.name}" has no itemStyle.color`).toBeTruthy()
    }
  })
})
