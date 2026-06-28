/** CrawlController — the single logical orchestrator owning all client-side
 *  navigation + backend I/O (spec §2). Slice 1: gated skeleton. Slice 2:
 *  /status reconcile + backoff + heartbeat + tab-validity + alarm. Slice 3
 *  (this): /jobs/next claim, persist-before-navigate dispatch (the SOLE
 *  chrome.tabs.update caller), the amend §2.3 landing recognition (five
 *  conditions), three-signal aggregation by documentId, the amend §3.2 retry
 *  funnel, the amend §2.4 two-level deadline, requestId binding + commit/http/
 *  documentId join, and the six deliver* event handlers (NavController). The
 *  gate constant EXCLUSIVE_CONTROL_ENABLED (injected by esbuild, default false)
 *  makes the official build a runtime no-op: tick/bind/deliver* load state and
 *  return before any network or chrome call. capture / formActions / CS RPC
 *  ledger are slice 4. */

import { createMutex } from './mutex.js';
import type { Mutex } from './mutex.js';
import { createControllerStorage } from './storage.js';
import type { ControllerStorage, StorageArea } from './storage.js';
import { nextRetryAt } from './backoff.js';
import { applyReconcile } from './reconcile.js';
import { createHeartbeat } from './heartbeat.js';
import { evaluateLanding } from './landing.js';
import { funnelDecision } from './funnel.js';
import { navTimeoutKind } from './deadline.js';
import { redirectKind, urlMatches } from '../shared/urlGate.js';
import type { ApiClient } from '../api.js';
import type { ChromeRuntime } from '../chrome.js';
import type { ControllerState, ControllerError, NavOutcome } from '../shared/state.js';
import type {
  BeforeRequestEvent, BeforeRedirectEvent, CommittedEvent,
  NavCompletedEvent, NavErrorEvent, NavController, NavScope, PageReadyEvent,
} from '../shared/navEvents.js';

// Re-export so callers (background.ts, tests) can build a storage + controller
// from a single import entry point.
export { createControllerStorage };
export type { ControllerStorage, StorageArea };

declare const EXCLUSIVE_CONTROL_ENABLED: boolean;

/** The 1-minute chrome.alarms waker that drives controller.tick() — the
 *  reconciliation alarm that REPLACES the Phase-1 watchdog (spec §2.6). */
export const RECONCILIATION_ALARM_NAME = 'dext-reconcile';
export const RECONCILIATION_PERIOD_MINUTES = 1;

export interface CrawlControllerDeps {
  storage?: ControllerStorage;
  area?: StorageArea;
  api?: ApiClient;
  chrome?: Pick<ChromeRuntime, 'getTab' | 'updateTabUrl'>;
}

export interface CrawlController extends NavController {
  tick(now?: number): Promise<void>;
  bind(tabId: number, now?: number): Promise<void>;
  setAutoMode(mode: boolean, now?: number): Promise<void>;
  getState(): Promise<ControllerState>;
}

export function createCrawlController(deps: CrawlControllerDeps = {}): CrawlController {
  const storage: ControllerStorage = deps.storage ?? createControllerStorage(deps.area ?? inMemoryArea());
  const mutex: Mutex = createMutex();
  const heartbeat = deps.api ? createHeartbeat(deps.api) : null;
  let state: ControllerState | null = null;

  async function ensureLoaded(): Promise<ControllerState> {
    if (state) return state;
    state = await storage.load();
    return state;
  }

  async function persist(): Promise<void> {
    if (state) await storage.save(state);
  }

  /** Lock-free scope snapshot for the thin navMonitor (amend §3.2). Reads the
   *  in-memory cache without acquiring the mutex so navMonitor can filter
   *  bound-tab events while the Controller is mid-tick. */
  function getNavScope(): NavScope {
    return { boundTabId: state?.boundTabId ?? null };
  }

  /** Clear the per-attempt correlation slots (amend §1.2) so a retry's signals
   *  are re-evaluated fresh. */
  function clearAttemptSignals(nav: NonNullable<ControllerState['navigation']>): void {
    nav.requestId = undefined;
    nav.commit = undefined;
    nav.http = undefined;
    nav.pageReady = undefined;
    nav.acceptedUrl = undefined;
  }

  function failMessage(e: Extract<ControllerError, { kind: 'nav_error' | 'gateway_5xx' | 'unexpected_status' | 'rate_limited' }>): string {
    switch (e.kind) {
      case 'nav_error': return `nav_error:${e.error}`;
      case 'gateway_5xx': return 'gateway_5xx';
      case 'unexpected_status': return `unexpected_status:${e.statusCode}`;
      case 'rate_limited': return 'rate_limited';
    }
  }

  /** Act on a landing verdict (called under the lock after each signal). */
  async function applyLandingVerdict(s: ControllerState, now: number): Promise<void> {
    const nav = s.navigation;
    const verdict = evaluateLanding(s, now);
    if (verdict.kind === 'waiting') return;
    if (verdict.kind === 'landed') {
      s.navigation!.acceptedUrl = verdict.acceptedUrl;
      s.phase = 'landed';
      s.phaseStartedAt = now;
      return;   // slice 4 dispatches capture here
    }
    if (verdict.kind === 'terminal_skip') {
      const jobId = s.currentJob!.id;
      s.phase = 'error';
      s.phaseStartedAt = now;
      s.lastError = null;            // skip is not a fail
      s.navigation = null;
      if (deps.api) await deps.api.skipJob(jobId, verdict.reason);
      return;
    }
    // error_retry → funnel
    const decision = funnelDecision(verdict.outcome, nav!.attempt, verdict.detail);
    if (decision.kind === 'retry_navigate') {
      clearAttemptSignals(nav!);
      nav!.attempt += 1;
      nav!.issuedAt = now;
      await navigateNow(s, now);     // persist-before-navigate again
      return;
    }
    if (decision.kind === 'skip') {
      const jobId = s.currentJob!.id;
      s.phase = 'error';
      s.phaseStartedAt = now;
      s.lastError = null;
      s.navigation = null;
      if (deps.api) await deps.api.skipJob(jobId, decision.reason);
      return;
    }
    // fail
    const jobId = s.currentJob!.id;
    s.phase = 'error';
    s.phaseStartedAt = now;
    s.lastError = decision.error;
    s.navigation = null;
    if (deps.api) await deps.api.failJob(jobId, failMessage(decision.error));
  }

  /** Persist navigation intent BEFORE calling tabs.update (spec §3.1). */
  async function navigateNow(s: ControllerState, now: number): Promise<void> {
    if (!s.currentJob || s.boundTabId === null || !deps.chrome?.updateTabUrl) return;
    if (s.paused || !s.autoMode) return;
    s.navigation = {
      jobId: s.currentJob.id,
      requestedUrl: s.currentJob.url,
      issuedAt: now,
      attempt: s.navigation?.attempt ?? 1,
      kind: 'navigate',
    };
    s.phase = 'navigating';
    s.phaseStartedAt = now;
    await persist();                                  // persist-before-navigate
    await deps.chrome.updateTabUrl(s.boundTabId, s.currentJob.url);
  }

  // ---- NavController deliver* handlers (each re-acquires the mutex) ----

  async function deliverBeforeRequest(e: BeforeRequestEvent): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const nav = s.navigation;
      if (!nav) return;
      if (e.tabId !== s.boundTabId) return;           // scope: bound tab only
      if (e.frameId !== 0 || e.type !== 'main_frame') return;
      if (s.phase !== 'navigating' && s.phase !== 'acting') return;
      if (s.currentJob?.id !== nav.jobId) return;
      if (e.timeStamp < nav.issuedAt) return;          // late event from before this attempt
      if (nav.requestId) return;                       // already bound this attempt
      if (!urlMatches(e.url, nav.requestedUrl)) return; // amend §2.1 URL match
      nav.requestId = e.requestId;                     // BIND (amend §2.1)
      await persist();
    } finally { release(); }
  }

  async function deliverBeforeRedirect(e: BeforeRedirectEvent): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const nav = s.navigation;
      if (!nav) return;
      if (e.tabId !== s.boundTabId) return;
      if (e.frameId !== 0 || e.type !== 'main_frame') return;
      if (s.phase !== 'navigating' && s.phase !== 'acting') return;
      if (nav.requestId && e.requestId !== nav.requestId) return; // only same requestId's redirect chain
      const kind = redirectKind(e.redirectUrl, nav.requestedUrl);   // NO classifier (amend §3.1)
      if (kind === 'onsite') return;                    // keep waiting for final onCompleted
      const reason = kind === 'wechat' ? 'wechat_redirect' : 'offsite_redirect';
      const jobId = s.currentJob!.id;
      s.phase = 'error';
      s.phaseStartedAt = e.timeStamp;
      s.lastError = null;
      s.navigation = null;
      if (deps.api) await deps.api.skipJob(jobId, reason);
      await persist();
    } finally { release(); }
  }

  async function deliverCommitted(e: CommittedEvent): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const nav = s.navigation;
      if (!nav) return;
      if (e.tabId !== s.boundTabId) return;
      if (e.frameId !== 0) return;
      if (s.phase !== 'navigating' && s.phase !== 'acting') return;
      if (s.currentJob?.id !== nav.jobId) return;
      if (e.timeStamp < nav.issuedAt) return;
      // NOTE: CommittedEvent has no requestId (webNavigation.onCommitted doesn't
      // carry it; amend §2.2). Commit correlation is by bound-tab + main-frame +
      // job + time-window, which the guards above already enforced.
      nav.commit = { documentId: e.documentId, committedUrl: e.url, committedAt: e.timeStamp };
      await applyLandingVerdict(s, e.timeStamp);
      await persist();
    } finally { release(); }
  }

  async function deliverHttpEvent(e: NavCompletedEvent, outcome: NavOutcome): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const nav = s.navigation;
      if (!nav) return;
      if (e.tabId !== s.boundTabId) return;
      if (e.frameId !== 0) return;
      if (s.phase !== 'navigating' && s.phase !== 'acting') return;
      // amend §2.2: only the same requestId's final onCompleted writes http.
      if (nav.requestId && e.requestId !== nav.requestId) return;
      // if the event carries a documentId that differs from commit → discard (amend §8.1 #7)
      if (e.documentId !== undefined && nav.commit && e.documentId !== nav.commit.documentId) return;
      nav.http = { requestId: e.requestId, documentId: e.documentId, statusCode: e.statusCode, outcome };
      await applyLandingVerdict(s, e.timeStamp);
      await persist();
    } finally { release(); }
  }

  async function deliverError(e: NavErrorEvent, outcome: NavOutcome): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const nav = s.navigation;
      if (!nav) return;
      if (e.tabId !== s.boundTabId) return;
      if (e.frameId !== 0) return;
      if (s.phase !== 'navigating' && s.phase !== 'acting') return;
      if (nav.requestId && e.requestId !== nav.requestId) return;
      nav.http = { requestId: e.requestId, outcome, error: e.error };
      await applyLandingVerdict(s, e.timeStamp);
      await persist();
    } finally { release(); }
  }

  async function deliverPageReady(e: PageReadyEvent): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const nav = s.navigation;
      if (!nav) return;
      if (s.phase !== 'navigating' && s.phase !== 'acting') return;
      // PAGE_READY is keyed by documentId; only the commit's documentId matters (amend §2.3 cond 4).
      if (!nav.commit) return;                          // PAGE_READY before commit — can't key it
      if (e.documentId !== nav.commit.documentId) return;   // stale PAGE_READY discarded
      nav.pageReady = { documentId: e.documentId, url: e.url, detection: e.detection };
      await applyLandingVerdict(s, e.timeStamp);
      await persist();
    } finally { release(); }
  }

  return {
    getNavScope,
    deliverBeforeRequest,
    deliverBeforeRedirect,
    deliverCommitted,
    deliverHttpEvent,
    deliverError,
    deliverPageReady,

    async tick(now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;          // gated no-op

        const prev = structuredClone(s);

        // 1. Bound-tab validity rehydration (slice 2).
        if (s.boundTabId !== null && deps.chrome?.getTab) {
          const tab = await deps.chrome.getTab(s.boundTabId);
          if (tab === null) {
            s.boundTabId = null;
            s.boundAt = null;
            s.phase = s.currentJob ? 'assigned' : 'idle';
            s.phaseStartedAt = now;
          }
        }

        // 2. /status reconcile (slice 2), gated by nextBackendRetryAt.
        const backoffExpired = s.nextBackendRetryAt === null || now >= s.nextBackendRetryAt;
        if (backoffExpired && deps.api) {
          const status = await deps.api.getStatus();
          if (status === null) {
            s.connected = false;
            s.backendFailureCount += 1;
            s.nextBackendRetryAt = nextRetryAt(s.backendFailureCount, now);
          } else {
            s.connected = true;
            s.backendFailureCount = 0;
            s.nextBackendRetryAt = null;
            applyReconcile(s, status.current_job, now);
          }
        }

        // 3. Heartbeat (slice 2) — every tick when bound; not gated by backoff/paused.
        if (s.boundTabId !== null && heartbeat) {
          await heartbeat.send(s, now);
        }

        // 4. Claim (slice 3) — idle + bound + auto + !paused + no currentJob.
        //    A bound controller with no job sits in `assigned` (from bind); spec §2.5
        //    holds it there until the user restores auto (setAutoMode(true)
        //    performs the assigned→idle transition). Do NOT normalize here.
        if (s.phase === 'idle' && s.currentJob === null && s.autoMode && !s.paused && s.boundTabId !== null && deps.api) {
          const job = await deps.api.claimNextJob();
          if (job) {
            s.currentJob = job;
            s.phase = 'assigned';
            s.phaseStartedAt = now;
          }
          // 204/null → stay idle; do NOT loop (single in-flight).
        }

        // 5. Navigate (slice 3) — assigned + bound + auto + !paused → persist then tabs.update.
        if (s.phase === 'assigned' && s.currentJob && s.autoMode && !s.paused && s.boundTabId !== null) {
          await navigateNow(s, now);
        }

        // 6. Landing + deadlines (slice 3) — only while navigating.
        if (s.phase === 'navigating' && s.navigation) {
          const verdict = evaluateLanding(s, now);
          if (verdict.kind !== 'waiting') {
            await applyLandingVerdict(s, now);
          } else {
            const dl = navTimeoutKind(s, now);
            if (dl.kind === 'never_landed') {
              // no commit + navigationDeadlineAt expired → funnel (nav_error:timeout).
              s.navigation!.http = { requestId: s.navigation!.requestId ?? '', outcome: 'nav_error', error: 'timeout' };
              await applyLandingVerdict(s, now);
            } else if (dl.kind === 'content_unavailable') {
              // commit present + landingSignalsDeadlineAt expired → NEVER refresh.
              s.phase = 'error';
              s.phaseStartedAt = now;
              s.lastError = {
                kind: 'content_unavailable',
                missing: dl.missing,
                sourceDocumentId: dl.sourceDocumentId,
                since: s.navigation!.commit!.committedAt,
                recoveryAttempts: 0,
                nextRecoveryAt: null,
                recoveryExhausted: true,    // PAGE_READY/HTTP not RPC-recoverable (amend §6.1)
              };
              // keep navigation (landing page retained); do NOT clear.
            }
          }
        }

        // 7. Persist only on actual change (slice 2 invariant).
        await storage.saveIfChanged(prev, s);
      } finally {
        release();
      }
    },

    async bind(tabId: number, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;          // gated no-op
        s.boundTabId = tabId;
        s.boundAt = now;
        s.phase = 'assigned';
        s.phaseStartedAt = now;
        await persist();
      } finally {
        release();
      }
    },

    async setAutoMode(mode: boolean, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        s.autoMode = mode;
        if (mode && s.phase === 'assigned' && s.currentJob === null && s.boundTabId !== null) {
          // spec §2.5: restoring auto triggers the assigned→idle transition so the next tick can claim.
          s.phase = 'idle';
          s.phaseStartedAt = now;
        }
        await persist();
      } finally {
        release();
      }
    },

    async getState(): Promise<ControllerState> {
      const release = await mutex.acquire();
      try {
        return structuredClone(await ensureLoaded());
      } finally {
        release();
      }
    },
  };
}

/** Minimal in-memory StorageArea used when no chrome.storage is present. */
function inMemoryArea(): StorageArea {
  const store = new Map<string, unknown>();
  return {
    async get(keys) {
      const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]);
      const obj: Record<string, unknown> = {};
      for (const k of arr) if (store.has(k)) obj[k] = store.get(k);
      return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
}
