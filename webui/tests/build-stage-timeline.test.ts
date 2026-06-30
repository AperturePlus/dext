import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import BuildStageTimeline from '../src/components/charts/BuildStageTimeline.vue'
import type { StageItem } from '../src/types/monitor'

describe('BuildStageTimeline', () => {
  it('renders long stage names and the current badge inside the active tile', () => {
    const stages: StageItem[] = [
      { name: 'WRITING_GRAPH', state: 'completed' },
      { name: 'WRITING_VECTOR', state: 'current' },
      { name: 'VALIDATING', state: 'pending' }
    ]

    const wrapper = mount(BuildStageTimeline, { props: { stages } })
    const current = wrapper.find('.stage-current')

    expect(wrapper.findAll('li')).toHaveLength(3)
    expect(current.find('.stage-name').text()).toBe('WRITING_VECTOR')
    expect(current.find('.status-badge .status-text').text()).toBe('current')
  })
})
