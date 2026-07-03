import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

const apiMocks = vi.hoisted(() => ({
  orgUnitProfessors: vi.fn(),
  professorTopics: vi.fn()
}))
vi.mock('../src/services/api', () => ({ monitorApi: apiMocks }))

import TopologyPage from '../src/pages/TopologyPage.vue'
import OrgUnitProfessorChart from '../src/components/charts/OrgUnitProfessorChart.vue'
import ProfessorTopicChart from '../src/components/charts/ProfessorTopicChart.vue'
import type {
  UniversityTopologyResponse,
  OrgUnitProfessorResponse,
  ProfessorTopicResponse
} from '../src/types/monitor'

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
      { id: 'org2', label: '学院B1', category: 'OrgUnit', kind: 'college', professor_count: 1, university: 'u2' }
    ],
    links: [
      { source: 'org', target: 'u', label: 'PART_OF' },
      { source: 'org2', target: 'u2', label: 'PART_OF' }
    ]
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

function buildTopicSubgraph(): ProfessorTopicResponse {
  return {
    build_id: 'b1',
    professor: {
      graph_key: 'p1',
      name: '张三',
      title: '教授',
      title_family: 'professor',
      role_status: 'included'
    },
    topics: [
      {
        graph_key: 't1',
        logical_id: 'topic-1',
        canonical_name: '机器学习',
        normalized_name: '机器学习',
        kind: 'method',
        status: 'active',
        taxonomy_version: 'tax-v1'
      }
    ],
    links: [
      {
        source: 'p1',
        target: 't1',
        label: 'PRIMARY_TOPIC',
        evidence_count: 2,
        confidence: 0.8
      }
    ]
  }
}

const baseProps = {
  health: null, builds: null, detail: null, metrics: null, topology: null,
  findings: [], selectedBuildId: 'b1', error: null, paused: false, lastUpdated: null, history: []
}

function chartNodeIds(wrapper: ReturnType<typeof mount>): string[] {
  const frame = wrapper.findComponent(stubs.ChartFrame)
  const option = frame.props('option') as { series: Array<{ data: Array<{ id: string }> }> }
  return option.series[0].data.map((n) => n.id).sort()
}

describe('TopologyPage', () => {
  beforeEach(() => {
    apiMocks.orgUnitProfessors.mockReset()
    apiMocks.professorTopics.mockReset()
  })

  it('renders the university chart before a college is selected', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frames = wrapper.findAllComponents(stubs.ChartFrame)
    expect(frames.length).toBeGreaterThanOrEqual(1)
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
  })

  it('renders only the page-level university selector and the college selector', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const selects = wrapper.findAll('select')
    expect(selects).toHaveLength(2)
    expect(selects[0].find('option[value="__all__"]').exists()).toBe(true)
    expect(selects[1].find('option[value="__none__"]').exists()).toBe(true)
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
    expect(collegeSelect.findAll('option').some((o) => o.attributes('value') === 'org2')).toBe(false)
    expect(chartNodeIds(wrapper)).toEqual(['org', 'u'])

    await collegeSelect.setValue('org')
    await flushPromises()
    expect(apiMocks.orgUnitProfessors).toHaveBeenCalledWith('b1', 'org')
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)
  })

  it('shows a professor dropdown after college load and drills into professor topics', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    apiMocks.professorTopics.mockResolvedValue(buildTopicSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    let selects = wrapper.findAll('select')

    await selects[0].setValue('u')
    await nextTick()
    await selects[1].setValue('org')
    await flushPromises()

    selects = wrapper.findAll('select')
    expect(selects).toHaveLength(3)
    const professorSelect = selects[2]
    expect(professorSelect.find('option[value="p1"]').exists()).toBe(true)
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)

    await professorSelect.setValue('p1')
    await flushPromises()

    expect(apiMocks.professorTopics).toHaveBeenCalledWith('b1', 'p1')
    expect(wrapper.findComponent(ProfessorTopicChart).exists()).toBe(true)
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
    expect(chartNodeIds(wrapper)).toEqual(['p1', 't1'])
  })

  it('selecting all universities clears the college drill-down and restores the full graph', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const [uniSelect, collegeSelect] = wrapper.findAll('select')

    await uniSelect.setValue('u')
    await nextTick()
    await collegeSelect.setValue('org')
    await flushPromises()
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)

    await uniSelect.setValue('__all__')
    await nextTick()
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
    expect(collegeSelect.element.value).toBe('__none__')
    expect(collegeSelect.attributes('disabled')).toBeDefined()
    expect(chartNodeIds(wrapper)).toEqual(['org', 'org2', 'u', 'u2'])
  })

  it('changing builds clears stale university, college, and subgraph state', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const [uniSelect, collegeSelect] = wrapper.findAll('select')

    await uniSelect.setValue('u')
    await nextTick()
    await collegeSelect.setValue('org')
    await flushPromises()
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)

    await wrapper.setProps({ selectedBuildId: 'b2' })
    await nextTick()
    expect(uniSelect.element.value).toBe('__all__')
    expect(collegeSelect.element.value).toBe('__none__')
    expect(collegeSelect.attributes('disabled')).toBeDefined()
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
    expect(chartNodeIds(wrapper)).toEqual(['org', 'org2', 'u', 'u2'])
  })

  it('changing builds clears stale professor and topic state', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    apiMocks.professorTopics.mockResolvedValue(buildTopicSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    let selects = wrapper.findAll('select')

    await selects[0].setValue('u')
    await nextTick()
    await selects[1].setValue('org')
    await flushPromises()
    selects = wrapper.findAll('select')
    await selects[2].setValue('p1')
    await flushPromises()
    expect(wrapper.findComponent(ProfessorTopicChart).exists()).toBe(true)

    await wrapper.setProps({ selectedBuildId: 'b2' })
    await nextTick()

    expect(wrapper.findComponent(ProfessorTopicChart).exists()).toBe(false)
    expect(wrapper.findAll('select')).toHaveLength(2)
    expect(chartNodeIds(wrapper)).toEqual(['org', 'org2', 'u', 'u2'])
  })

  it('shows EmptyState when topology has no nodes', () => {
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: { build_id: 'b1', universities: [], nodes: [], links: [] } },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
  })

  it('shows compacted export empty state and clears stale drilldown', async () => {
    apiMocks.orgUnitProfessors.mockResolvedValue(buildSubgraph())
    const wrapper = mount(TopologyPage, {
      props: { ...baseProps, topology: buildTopology() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const [uniSelect, collegeSelect] = wrapper.findAll('select')
    await uniSelect.setValue('u')
    await nextTick()
    await collegeSelect.setValue('org')
    await flushPromises()
    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(true)

    await wrapper.setProps({
      topology: { build_id: 'b1', universities: [], nodes: [], links: [], export_pruned: true }
    })
    await nextTick()

    expect(wrapper.findComponent(OrgUnitProfessorChart).exists()).toBe(false)
    expect(wrapper.findAll('select')).toHaveLength(0)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
    expect(wrapper.findComponent(stubs.EmptyState).text()).toBe('Graph exports compacted')
  })
})
