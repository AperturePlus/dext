import type {
  ApiEnvelope,
  BuildDetailResponse,
  BuildsResponse,
  FindingsResponse,
  HealthResponse,
  MetricsResponse,
  UniversityTopologyResponse
} from '../types/monitor'

export class MonitorApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly type = 'MonitorApiError'
  ) {
    super(message)
  }
}

// ETag cache keyed by request path. The monitor backend tags read-only
// responses with a weak ETag; on a 304 it returns no body, so we reuse the
// last payload. This turns terminal-build polling into zero-byte requests.
const etagByPath = new Map<string, { etag: string; data: unknown }>()

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const cached = etagByPath.get(path)
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (cached) headers['If-None-Match'] = cached.etag
  const response = await fetch(path, { ...init, headers })

  if (response.status === 304 && cached) {
    return cached.data as T
  }

  const envelope = (await response.json()) as ApiEnvelope<T>
  if (!response.ok || envelope.error) {
    throw new MonitorApiError(
      envelope.error?.message ?? response.statusText,
      response.status,
      envelope.error?.type
    )
  }
  if (!envelope.data) {
    throw new MonitorApiError('monitor response did not include data', response.status)
  }
  const etag = response.headers.get('ETag')
  if (etag) {
    etagByPath.set(path, { etag, data: envelope.data })
  }
  return envelope.data
}

export const monitorApi = {
  health: () => request<HealthResponse>('/api/monitor/health'),
  builds: () => request<BuildsResponse>('/api/monitor/builds'),
  buildDetail: (buildId: string) =>
    request<BuildDetailResponse>(`/api/monitor/builds/${encodeURIComponent(buildId)}`),
  metrics: (buildId: string) =>
    request<MetricsResponse>(`/api/monitor/builds/${encodeURIComponent(buildId)}/metrics`),
  universityTopology: (buildId: string) =>
    request<UniversityTopologyResponse>(
      `/api/monitor/builds/${encodeURIComponent(buildId)}/graph-tree`
    ),
  findings: (buildId?: string, severity?: string) => {
    const params = new URLSearchParams()
    if (buildId) params.set('build_id', buildId)
    if (severity) params.set('severity', severity)
    return request<FindingsResponse>(`/api/monitor/findings?${params.toString()}`)
  }
}
