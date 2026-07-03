import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const stubs = vi.hoisted(() => ({
  ChartFrame: { name: 'ChartFrame', props: ['option', 'minHeight'], template: '<div class="chart-stub" />' },
  EmptyState: { name: 'EmptyState', props: ['title', 'message'], template: '<div class="empty-stub">{{ title }}</div>' }
}))

vi.mock('../src/components/charts/ChartFrame.vue', () => ({ default: stubs.ChartFrame }))
vi.mock('../src/components/primitives/EmptyState.vue', () => ({ default: stubs.EmptyState }))

import ProfessorTopicChart from '../src/components/charts/ProfessorTopicChart.vue'
import type { ProfessorTopicResponse } from '../src/types/monitor'

function buildSubgraph(): ProfessorTopicResponse {
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
      },
      {
        graph_key: 't2',
        logical_id: 'topic-2',
        canonical_name: '机器人',
        normalized_name: '机器人',
        kind: 'application_domain',
        status: 'active',
        taxonomy_version: 'tax-v1'
      }
    ],
    links: [
      { source: 'p1', target: 't1', label: 'PRIMARY_TOPIC', evidence_count: 3, confidence: 0.7 },
      { source: 'p1', target: 't2', label: 'USES_METHOD', evidence_count: 1, confidence: 0.8 }
    ]
  }
}

describe('ProfessorTopicChart', () => {
  it('renders one professor node, topic nodes, and topic links', () => {
    const wrapper = mount(ProfessorTopicChart, {
      props: { subgraph: buildSubgraph() },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    const frame = wrapper.findComponent(stubs.ChartFrame)
    expect(frame.exists()).toBe(true)
    const series = frame.props('option').series[0]
    expect(series.data.map((node: { id: string }) => node.id).sort()).toEqual(['p1', 't1', 't2'])
    expect(series.links.map((link: { name: string }) => link.name).sort()).toEqual([
      'PRIMARY_TOPIC',
      'USES_METHOD'
    ])
  })

  it('shows EmptyState when the professor has no related topics', () => {
    const empty = { ...buildSubgraph(), topics: [], links: [] }
    const wrapper = mount(ProfessorTopicChart, {
      props: { subgraph: empty },
      global: { stubs: { ChartFrame: stubs.ChartFrame, EmptyState: stubs.EmptyState } }
    })
    expect(wrapper.findComponent(stubs.EmptyState).exists()).toBe(true)
    expect(wrapper.findComponent(stubs.ChartFrame).exists()).toBe(false)
  })
})
