/** Injectable HTTP client for the dext backend. Mirrors userscripts/src/api.ts endpoints
 * but uses fetch (background SW has no GM_xmlhttpRequest). All calls are best-effort:
 * getStatus returns null on error; failJob/skipJob/sendHeartbeat swallow errors (late/stale
 * reports are normal — the backend's 60s job timeout may have already released the slot,
 * and /fail + /skip are idempotent against stale ids anyway; heartbeat failure is reflected
 * by /status reconnect, not the heartbeat POST). */

import type { FetchJob } from './shared/types.js';

export interface CurrentJob {
  id: string;
  url: string;
}

export interface FrontendHealth {
  alive: boolean;
  last_seen_seconds_ago: number | null;
}

export interface StatusPayload {
  current_job: FetchJob | null;
  frontend_health: FrontendHealth;
}

export interface HeartbeatPayload {
  owner_tab_id: string;
  url: string;
  current_job_id: string | null;
  auto_mode: boolean;
  paused: boolean;
  timestamp: number;
}

export interface ApiClient {
  getStatus(): Promise<StatusPayload | null>;
  claimNextJob(): Promise<FetchJob | null>;
  failJob(jobId: string, message: string): Promise<void>;
  skipJob(jobId: string, reason: string): Promise<void>;
  sendHeartbeat(payload: HeartbeatPayload): Promise<void>;
}

type FetchFn = (url: string, init?: { method?: string; headers?: Record<string,string>; body?: string }) => Promise<{
  status: number;
  ok: boolean;
  json: () => Promise<unknown>;
}>;

export function createFetchApi(base: string, fetchFn?: FetchFn): ApiClient {
  const fetch: FetchFn = fetchFn ?? (globalThis.fetch as unknown as FetchFn);
  const headers = { 'Content-Type': 'application/json; charset=utf-8' };

  async function getStatus(): Promise<StatusPayload | null> {
    try {
      const res = await fetch(`${base}/status`, { method: 'GET' });
      if (!res.ok) return null;
      const body = (await res.json()) as StatusPayload;
      return body;
    } catch {
      return null;
    }
  }

  async function claimNextJob(): Promise<FetchJob | null> {
    try {
      const res = await fetch(`${base}/jobs/next`, { method: 'GET' });
      if (res.status === 204) return null;
      if (!res.ok) return null;
      return (await res.json()) as FetchJob;
    } catch {
      return null;
    }
  }

  async function failJob(jobId: string, message: string): Promise<void> {
    try {
      await fetch(`${base}/jobs/${jobId}/fail`, {
        method: 'POST', headers,
        body: JSON.stringify({ message }),
      });
    } catch {
      // stale/late report — backend /fail is idempotent; swallow.
    }
  }

  async function skipJob(jobId: string, reason: string): Promise<void> {
    try {
      await fetch(`${base}/jobs/${jobId}/skip`, {
        method: 'POST', headers,
        body: JSON.stringify({ reason }),
      });
    } catch {
      // swallow — same rationale as failJob.
    }
  }

  async function sendHeartbeat(payload: HeartbeatPayload): Promise<void> {
    try {
      await fetch(`${base}/heartbeat`, {
        method: 'POST', headers,
        body: JSON.stringify(payload),
      });
    } catch {
      // backend down is reflected by /status reconnect; heartbeat is best-effort (mirrors userscript)
    }
  }

  return { getStatus, claimNextJob, failJob, skipJob, sendHeartbeat };
}
