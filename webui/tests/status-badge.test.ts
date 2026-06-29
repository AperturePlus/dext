import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import StatusBadge from '../src/components/primitives/StatusBadge.vue'

describe('StatusBadge', () => {
  it('renders the status text and class', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'FAILED' } })
    expect(wrapper.text()).toContain('FAILED')
    expect(wrapper.classes()).toContain('status-failed')
  })

  it('uses the success palette when the catalog is readable', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'catalog_readable' } })
    expect(wrapper.classes()).toContain('status-catalog_readable')
  })

  it('uses the danger palette when the catalog is unavailable', () => {
    const wrapper = mount(StatusBadge, { props: { status: 'catalog_unavailable' } })
    expect(wrapper.classes()).toContain('status-catalog_unavailable')
  })
})
