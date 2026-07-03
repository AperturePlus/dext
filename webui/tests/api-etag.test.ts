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

  it('requests org-unit professor subgraphs through the query endpoint', async () => {
    let requested = ''
    const payload = {
      build_id: 'b1',
      orgunit: { graph_key: 'build:org', label: '学院', kind: 'college', university: 'u' },
      professors: [],
      links: []
    }
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      requested = String(input)
      return new Response(JSON.stringify({ data: payload }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    }) as unknown as typeof fetch

    await expect(monitorApi.orgUnitProfessors('b1', 'build:org')).resolves.toEqual(payload)
    expect(requested).toBe('/api/monitor/builds/b1/orgunit-professors?org_graph_key=build%3Aorg')
  })

  it('requests professor-topic subgraphs through the query endpoint', async () => {
    let requested = ''
    const payload = {
      build_id: 'b1',
      professor: {
        graph_key: 'build:prof',
        name: '张三',
        title: '教授',
        title_family: 'professor',
        role_status: 'included'
      },
      topics: [],
      links: []
    }
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      requested = String(input)
      return new Response(JSON.stringify({ data: payload }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    }) as unknown as typeof fetch

    await expect(monitorApi.professorTopics('b1', 'build:prof')).resolves.toEqual(payload)
    expect(requested).toBe('/api/monitor/builds/b1/professor-topics?professor_graph_key=build%3Aprof')
  })
})
