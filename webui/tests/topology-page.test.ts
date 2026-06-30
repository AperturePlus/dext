import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

import TopologyPage from '../src/pages/TopologyPage.vue'
import type { UniversityTopologyResponse } from '../src/types/monitor'

function buildTopology(): UniversityTopologyResponse {
  return {
    build_id: 'b1',
    universities: [
      { graph_key: 'u', name: '大学A', logical_id: 'univ:a', orgunit_count: 1, professor_count: 2 }
    ],
    nodes: [
      { id: 'u', label: '大学A', category: 'University', professor_count: 2, orgunit_count: 1 },
      { id: 'org', label: '学院A1', category: 'OrgUnit', kind: 'college', professor_count: 2, university: 'u' }
    ],
    links: [{ source: 'org', target: 'u', label: 'PART_OF' }]
  }
}

const baseProps = {
  health: null, builds: null, detail: null, metrics: null, topology: null,
  findings: [], selectedBuildId: null, error: null, paused: false, lastUpdated: null, history: []
}

describe('TopologyPage', () => {
  it('renders the chart with the large full-page minHeight', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    // Full-page canvas must be taller than the dashboard's old 390px default.
    expect(frame.props('minHeight')).toBeGreaterThan(390)
  })

  it('emits refresh and toggle-pause from the hero RefreshControl', async () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    // RefreshControl renders two buttons; first is "Refresh".
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('refresh')).toBeTruthy()
    expect(wrapper.emitted('togglePause')).toBeTruthy()
  })

  it('shows EmptyState when topology has no nodes', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: { build_id: 'b1', universities: [], nodes: [], links: [] } },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
  })
})
