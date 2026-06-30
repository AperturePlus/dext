import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import MetricCard from '../src/components/primitives/MetricCard.vue'

describe('MetricCard', () => {
  it('uses a compact wrapping style and tooltip for long status values', () => {
    const wrapper = mount(MetricCard, {
      props: { label: 'Build status', value: 'WRITING_VECTOR', hint: '019f135d...a231' }
    })

    const value = wrapper.find('.metric-value')
    expect(value.text()).toBe('WRITING_VECTOR')
    expect(value.classes()).toContain('metric-value-compact')
    expect(value.attributes('title')).toBe('WRITING_VECTOR')
  })

  it('keeps short numeric values in the default metric style', () => {
    const wrapper = mount(MetricCard, {
      props: { label: 'Sources', value: '8' }
    })

    expect(wrapper.find('.metric-value').classes()).not.toContain('metric-value-compact')
  })
})
