import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

// Stub the echarts core so we can assert on the instance returned by init.
// vi.hoisted so the stubs exist when vi.mock's factory runs (it is hoisted).
const stubs = vi.hoisted(() => {
  return {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    init: vi.fn()
  }
})

vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: (...args: unknown[]) => {
    stubs.init(...args)
    return { setOption: stubs.setOption, resize: stubs.resize, dispose: stubs.dispose }
  }
}))

import ChartFrame from '../src/components/charts/ChartFrame.vue'
import type { ChartOption } from '../src/components/charts/options'

function makeOption(value: number): ChartOption {
  return {
    backgroundColor: 'transparent',
    series: [{ type: 'bar', data: [value] }]
  } as unknown as ChartOption
}

describe('ChartFrame', () => {
  beforeEach(() => {
    stubs.init.mockClear()
    stubs.setOption.mockClear()
    stubs.resize.mockClear()
    stubs.dispose.mockClear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('P2-18: setOption on mount, then merges (not notMerge=true) on prop change', async () => {
    const wrapper = mount(ChartFrame, {
      props: { option: makeOption(1) }
    })
    await nextTick()

    // Initial setOption on mount.
    expect(stubs.init).toHaveBeenCalledTimes(1)
    expect(stubs.setOption).toHaveBeenCalledTimes(1)

    // Trigger a prop change — must NOT pass `true` (notMerge) which would
    // rebuild the whole chart and flicker on every 5s poll.
    const next = makeOption(2)
    await wrapper.setProps({ option: next })
    await nextTick()

    expect(stubs.setOption).toHaveBeenCalledTimes(2)
    const lastCall = stubs.setOption.mock.calls.at(-1)!
    expect(lastCall[0]).toStrictEqual(next)
    // Assert no second-arg `true` (notMerge) — the merge-mode change.
    expect(lastCall[1]).not.toBe(true)
    if (lastCall[1] !== undefined) {
      expect((lastCall[1] as { notMerge?: boolean }).notMerge).not.toBe(true)
    }
  })
})
