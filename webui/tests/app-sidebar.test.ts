import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'

import AppSidebar from '../src/components/layout/AppSidebar.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'overview', component: { template: '<div />' } },
      { path: '/data', name: 'data', component: { template: '<div />' } },
      { path: '/quality', name: 'quality', component: { template: '<div />' } },
      { path: '/topology', name: 'topology', component: { template: '<div />' } }
    ]
  })
}

const baseProps = {
  health: null,
  paused: false,
  lastUpdated: null
}

describe('AppSidebar', () => {
  it('renders four nav links pointing at the four routes', async () => {
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: baseProps,
      global: { plugins: [router] }
    })
    await nextTick()
    const links = wrapper.findAll('.nav-link')
    expect(links).toHaveLength(4)
    expect(links[0].attributes('href')).toBe('/')
    expect(links[1].attributes('href')).toBe('/data')
    expect(links[2].attributes('href')).toBe('/quality')
    expect(links[3].attributes('href')).toBe('/topology')
  })

  it('marks the active route with router-link-active', async () => {
    const router = makeRouter()
    router.push('/data')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: baseProps,
      global: { plugins: [router] }
    })
    await nextTick()
    const active = wrapper.findAll('.nav-link.router-link-active')
    expect(active).toHaveLength(1)
    expect(active[0].text()).toBe('数据')
  })

  it('emits refresh and toggle-pause from the footer RefreshControl', async () => {
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: baseProps,
      global: { plugins: [router] }
    })
    await nextTick()
    const buttons = wrapper.findAll('button')
    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    expect(wrapper.emitted('refresh')).toBeTruthy()
    expect(wrapper.emitted('togglePause')).toBeTruthy()
  })
})
