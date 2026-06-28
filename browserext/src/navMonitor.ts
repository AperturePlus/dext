/** Orchestrator: webRequest events → status.classify → api.fail/skip + chrome.redirect.
 * Split rule (spec §2.1): nav_error (about:neterror, userscript does NOT inject) →
 * self-redirect via chrome.updateTabUrl; gateway/not_found (http page, userscript
 * DOES inject and retries) → count + report only, no redirect.
 * Scope guard (spec §4.4): only act on navigations whose URL matches current_job.url —
 * the owner browsing an unrelated broken page must not skip/fail the in-flight job. */

import type { ApiClient } from './api.js';
import { classifyNavigation } from './status.js';
import type { ChromeRuntime, NavCompletedEvent, NavErrorEvent } from './chrome.js';
import type { Storage } from './storage.js';

export const MAX_ABNORMAL_NAVS = 3;

export interface NavMonitorDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export interface NavMonitor {
  start(): void;
  handleCompleted(e: NavCompletedEvent): Promise<void>;
  handleError(e: NavErrorEvent): Promise<void>;
}

/** Loose same-URL check (ignores http/https + trailing slash), mirroring the userscript's
 * urlMatches intent without importing its full implementation. Good enough to decide
 * "this navigation is for the in-flight job" vs "the owner browsed elsewhere". */
function sameUrl(a: string, b: string): boolean {
  try {
    const u1 = new URL(a);
    const u2 = new URL(b);
    return u1.hostname === u2.hostname
      && u1.pathname.replace(/\/+$/, '') === u2.pathname.replace(/\/+$/, '')
      && u1.search === u2.search;
  } catch {
    return false;
  }
}

export function createNavMonitor(deps: NavMonitorDeps): NavMonitor {
  const { chrome, api, storage } = deps;

  async function resolveJobIfMatched(eventUrl: string): Promise<{ id: string; url: string } | null> {
    const status = await api.getStatus();
    const job = status?.current_job;
    if (!job) return null;
    if (!sameUrl(eventUrl, job.url)) return null; // spec §4.4 scope guard
    return job;
  }

  async function reportAndMark(jobId: string, op: 'fail' | 'skip', payload: string): Promise<void> {
    if (await storage.wasVerdictSent(jobId)) return;
    if (op === 'fail') await api.failJob(jobId, payload);
    else await api.skipJob(jobId, payload);
    await storage.markVerdictSent(jobId);
  }

  async function handleCompleted(e: NavCompletedEvent): Promise<void> {
    const outcome = classifyNavigation({ kind: 'completed', statusCode: e.statusCode });
    if (outcome === 'ok' || outcome === 'rate_limited') return;
    const job = await resolveJobIfMatched(e.url);
    if (!job) return;
    if (outcome === 'not_found') {
      await reportAndMark(job.id, 'skip', 'not_found');
      return;
    }
    // outcome === 'gateway' → count + (on 3rd) fail. NO redirect (userscript handles http 5xx).
    const count = await storage.bumpCount(job.id);
    if (count >= MAX_ABNORMAL_NAVS) {
      await reportAndMark(job.id, 'fail', 'gateway_5xx');
    }
  }

  async function handleError(e: NavErrorEvent): Promise<void> {
    const outcome = classifyNavigation({ kind: 'error', error: e.error });
    if (outcome !== 'nav_error') return;
    const job = await resolveJobIfMatched(e.url);
    if (!job) return;
    const count = await storage.bumpCount(job.id);
    if (count >= MAX_ABNORMAL_NAVS) {
      await reportAndMark(job.id, 'fail', `nav_error:${e.error}`);
      return;
    }
    // below threshold: self-redirect via the real owner tab (ignore background tabs).
    if (await storage.shouldRedirect(job.id)) {
      const ownerTabId = await chrome.findOwnerTab();
      if (ownerTabId === null) return;
      await chrome.updateTabUrl(ownerTabId, job.url);
      await storage.recordRedirect(job.id);
    }
  }

  return {
    start() {
      chrome.onNavCompleted((e) => handleCompleted(e));
      chrome.onNavError((e) => handleError(e));
    },
    handleCompleted,
    handleError,
  };
}
