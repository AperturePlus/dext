import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

const apiMocks = vi.hoisted(() => ({
  orgUnitProfessors: vi.fn()
}))
vi.mock('../src/services/api', () => ({ monitorApi: apiMocks }))

import TopologyPage from '../src/pages/TopologyPage.vue'
import OrgUnitProfessorChart from '../src/components/charts/OrgUnitProfessorChart.vue'
import type { UniversityTopologyResponse, OrgUnitProfessorResponse } from '../src/types/monitor'

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

function buildSubgraph(): OrgUnitProfessorResponse {
  return {
    build_id: 'b1',
    orgunit: { graph_key: 'org', label: '学院A1', kind: 'college', university: 'u' },
    professors: [
      { graph_key: 'p1', name: '张三', title: '教授', title_family: '教授', role_status: 'active' }
    ],
    links: [{ source: 'p1', target: 'org', label: 'AFFILIATED_WITH' }]
  }
}

const baseProps = {
  health: null, builds: null, detail: null, metrics: null, topology: null,
  findings: [], selectedBuildId: 'b1', error: null, paused: false, lastUpdated: null, history: []
}

describe('TopologyPage', () => {
  it('renders the university chart before a college is selected', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frames = wrapper.findAllComponents(stubs.ChartFrame)
    expect(frames.length).toBeGreaterThanOrEqual(1)
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
  })

  it('enables the college dropdown after a university is chosen and drills in', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const selects = wrapper.findAll('select')
    const uniSelect = selects[0]
    const collegeSelect = selects[1]
    // college dropdown disabled until a university is picked
    expect(collegeSelect.attributes('disabled')).toBeDefined()

    await uniSelect.setValue('u')
    await nextTick()
    expect(collegeSelect.attributes('disabled')).toBeUndefined()
    // college options now list this university's colleges
    expect(collegeSelect.findAll('option').some((o) => o.attributes('value') === 'org')).toBe(true)

    await collegeSelect.setValue('org')
    await flushPromises()
    expect(apiMocks.orgUnitProfessors).toHaveBeenCalledWith('b1', 'org')
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)
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
