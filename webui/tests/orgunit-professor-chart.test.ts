import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

import OrgUnitProfessorChart from '../src/components/charts/OrgUnitProfessorChart.vue'
import type { OrgUnitProfessorResponse } from '../src/types/monitor'

function buildSubgraph(): OrgUnitProfessorResponse {
  return {
    build_id: 'b1',
    orgunit: { graph_key: 'org', label: '学院A1', kind: 'college', university: 'u' },
    professors: [
      { graph_key: 'p1', name: '张三', title: '教授', title_family: '教授', role_status: 'active' },
      { graph_key: 'p2', name: '李四', title: '副教授', title_family: '副教授', role_status: 'active' }
    ],
    links: [
      { source: 'p1', target: 'org', label: 'AFFILIATED_WITH' },
      { source: 'p2', target: 'org', label: 'AFFILIATED_WITH' }
    ]
  }
}

describe('OrgUnitProfessorChart', () => {
  it('renders one college node, N professor nodes, and N edges', () => {
    const wrapper = mount(OrgUnitProfessorChart, {
      props: { subgraph: buildSubgraph() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const series = frame.props('option').series[0]
    // 1 college + 2 professors = 3 nodes
    expect(series.data).toHaveLength(3)
    expect(series.links).toHaveLength(2)
  })

  it('shows EmptyState when there are no professors', () => {
    const wrapper = mount(OrgUnitProfessorChart, {
      props: {
        subgraph: {
          build_id: 'b1',
          orgunit: { graph_key: 'org', label: '空学院', kind: 'college', university: 'u' },
          professors: [],
          links: []
        }
      },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
  })
})
