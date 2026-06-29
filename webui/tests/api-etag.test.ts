import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { monitorApi, MonitorApiError } from '../src/services/api'

describe('api ETag client (P1-5)', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('sends If-None-Match on repeat calls and reuses cached data on 304', async () => {
    const calls: string[] = []
    const payload = { findings: [], limit: 100 }
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>
      calls.push(headers['If-None-Match'] ?? '')
      if (headers['If-None-Match'] === 'W/"abc"') {
        return new Response(null, { status: 304, headers: { ETag: 'W/"abc"' } })
      }
      return new Response(JSON.stringify({ data: payload }), {
        status: 200,
        headers: { 'Content-Type': 'application/json', ETag: 'W/"abc"' }
      })
    }) as unknown as typeof fetch

    const first = await monitorApi.findings('build-1')
    expect(first).toEqual(payload)
    expect(calls[0]).toBe('') // no If-None-Match on first call

    const second = await monitorApi.findings('build-1')
    expect(second).toEqual(payload) // same data, served from 304 cache
    expect(calls[1]).toBe('W/"abc"') // If-None-Match sent on repeat call
  })

  it('still surfaces errors normally', async () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ error: { type: 'MonitorCatalogError', message: 'nope' } }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' }
      })
    ) as unknown as typeof fetch
    await expect(monitorApi.builds()).rejects.toMatchObject({
      status: 404,
      type: 'MonitorCatalogError'
    })
    void MonitorApiError
  })
})
