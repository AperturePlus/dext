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
import { buildPanelState } from './panelState.js';
import type { ApiClient } from '../api.js';
import type { ChromeRuntime } from '../chrome.js';
import type { ControllerState, ControllerError, NavOutcome } from '../shared/state.js';
import type { ActionPrepared, ActionResult, CaptureResult } from '../shared/rpc.js';
import type { FetchAction } from '../shared/types.js';
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
  chrome?: Pick<ChromeRuntime, 'getTab' | 'updateTabUrl' | 'sendMessage'>;
}

export interface CrawlController extends NavController {
  tick(now?: number): Promise<void>;
  bind(tabId: number, now?: number): Promise<void>;
  setAutoMode(mode: boolean, now?: number): Promise<void>;
  setPaused(paused: boolean, now?: number): Promise<void>;
  unbind(now?: number): Promise<void>;
  manualSkip(reason: string | undefined, now?: number): Promise<void>;
  manualFail(message: string | undefined, now?: number): Promise<void>;
  overrideUrl(url: string, now?: number): Promise<void>;
  resolveDecision(id: string, action: string, now?: number): Promise<void>;
  manualComplete(now?: number): Promise<void>;
  broadcastPanelState(sender?: { tabId: number | null }, reason?: string): Promise<void>;
  getState(): Promise<ControllerState>;
  deliverCaptureResult(result: CaptureResult, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void>;
  dispatchPrepareAction(action: FetchAction): Promise<void>;
  deliverActionPrepared(prepared: ActionPrepared, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void>;
  deliverActionResult(result: ActionResult, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void>;
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
      // Slice-4 form-action path (amend §5.1–§5.6): only the INITIAL list-page
      // navigation (kind:'navigate') with a form_submit job action dispatches
      // PREPARE_ACTION; a form_action result-document landing falls through to
      // dispatchCapture (amend §5.4 — never re-prepare on the result document).
      if (s.navigation?.kind === 'navigate'
          && s.currentJob?.action
          && s.currentJob.action.kind === 'form_submit') {
        await prepareFormActionDispatch(s, now, s.currentJob.action);
      } else {
        await dispatchCapture(s, now);
      }
      return;
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

  /** Dispatch CAPTURE on a landed page (amend §1.1, §4.1). Persist pendingRpc
   *  delivery='prepared' BEFORE sendMessage; promote to 'received' on transport
   *  receipt. Runs UNDER the mutex (called from applyLandingVerdict's landed
   *  branch or from deliverActionResult's same-document branch) — do NOT
   *  re-acquire. */
  async function dispatchCapture(s: ControllerState, now: number): Promise<void> {
    if (!s.currentJob || s.boundTabId === null || !deps.chrome?.sendMessage) return;
    // sourceDocumentId: prefer commit.documentId (navigate landing); fall back to
    // navigation.action's sourceDocumentId (same-document form_action effect — the
    // form_action navigation has no commit because it never re-navigated, but its
    // source document is the list page that hosted the form).
    const sourceDocumentId =
      s.navigation?.commit?.documentId
      ?? (s.navigation?.kind === 'form_action' ? s.navigation.sourceDocumentId : undefined);
    if (!sourceDocumentId) return;
    const rpcId = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : `cap-${now}-${Math.random().toString(36).slice(2)}`;
    const deadline = now + 30_000;
    s.pendingRpc = {
      id: rpcId, jobId: s.currentJob.id, op: 'capture',
      sourceDocumentId, delivery: 'prepared', issuedAt: now, resultDeadlineAt: deadline,
      recoveryAttempts: 0, nextRecoveryAt: null,
    };
    s.phase = 'capturing';
    s.phaseStartedAt = now;
    await persist();   // persist-before-send (delivery='prepared')
    try {
      const receipt = await deps.chrome.sendMessage(
        s.boundTabId,
        { op: 'CAPTURE', rpcId, jobId: s.currentJob.id },
        { documentId: sourceDocumentId },
      );
      if (receipt && (receipt as { received?: boolean }).received) {
        s.pendingRpc.delivery = 'received';
        await persist();
      }
    } catch {
      // sendMessage reject → amend §4.2: capture target doc gone → content_unavailable.
      s.phase = 'error';
      s.phaseStartedAt = now;
      s.lastError = {
        kind: 'content_unavailable', missing: 'capture_result', sourceDocumentId,
        since: now, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: true,
      };
      s.pendingRpc = null;
      await persist();
    }
  }

  /** Dispatch PREPARE_ACTION for a form_submit job (amend §5.1–§5.2). The prepare
   *  is side-effect-free; on ACTION_PREPARED ok:true we persist a NEW form_action
   *  NavigationState then dispatch PERFORM_ACTION. delivery: prepared→received.
   *  Runs UNDER the mutex (called from applyLandingVerdict's landed branch or the
   *  gated public dispatchPrepareAction wrapper) — do NOT re-acquire. */
  async function prepareFormActionDispatch(s: ControllerState, now: number, action: FetchAction): Promise<void> {
    if (!s.currentJob || s.boundTabId === null || !deps.chrome?.sendMessage) return;
    if (!s.navigation?.commit) return;
    const sourceDocumentId = s.navigation.commit.documentId;
    const rpcId = (typeof crypto !== 'undefined' && crypto.randomUUID)
      ? crypto.randomUUID()
      : `prep-${now}-${Math.random().toString(36).slice(2)}`;
    s.pendingRpc = {
      id: rpcId, jobId: s.currentJob.id, op: 'prepare_action',
      sourceDocumentId, delivery: 'prepared', issuedAt: now, resultDeadlineAt: now + 30_000,
      recoveryAttempts: 0, nextRecoveryAt: null,
    };
    s.phase = 'acting';
    s.phaseStartedAt = now;
    await persist();   // persist-before-send (delivery='prepared')
    try {
      const receipt = await deps.chrome.sendMessage(
        s.boundTabId,
        { op: 'PREPARE_ACTION', rpcId, jobId: s.currentJob.id, action },
        { documentId: sourceDocumentId },
      );
      if (receipt && (receipt as { received?: boolean }).received) {
        s.pendingRpc.delivery = 'received';
        await persist();
      }
    } catch {
      // amend §4.2: prepare reject with no receipt → content_unavailable/action_prepare
      s.phase = 'error';
      s.phaseStartedAt = now;
      s.lastError = {
        kind: 'content_unavailable', missing: 'action_prepare', sourceDocumentId,
        since: now, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: true,
      };
      s.pendingRpc = null;
      await persist();
    }
  }

  /** Handle ACTION_PREPARED from the content script (amend §5.1–§5.2, §4.1).
   *  Re-acquires the mutex like the other deliver* handlers. */
  async function deliverActionPrepared(
    prepared: ActionPrepared,
    sender: { tab?: { id: number }; frameId?: number; documentId?: string },
  ): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const rpc = s.pendingRpc;
      if (!rpc || rpc.op !== 'prepare_action') return;
      // amend §4.1 validation — any mismatch is a late/stale result, no-op.
      if (sender.tab?.id !== s.boundTabId) return;
      if (sender.frameId !== 0) return;
      if (sender.documentId !== rpc.sourceDocumentId) return;
      if (prepared.rpcId !== rpc.id || prepared.jobId !== s.currentJob?.id) return;

      if (!prepared.ok) {
        // retry prepare within budget (amend §6.1), else fail form_action_prepare_failed (amend §5.1).
        if (rpc.recoveryAttempts < 3 && (rpc.nextRecoveryAt === null || Date.now() >= rpc.nextRecoveryAt)) {
          rpc.recoveryAttempts += 1;
          rpc.nextRecoveryAt = Date.now() + 5_000;
          rpc.resultDeadlineAt = Date.now() + 30_000;
          rpc.delivery = 'prepared';
          rpc.issuedAt = Date.now();
          await persist();
          if (s.boundTabId !== null && deps.chrome?.sendMessage && s.currentJob?.action) {
            try {
              const receipt = await deps.chrome.sendMessage(
                s.boundTabId,
                { op: 'PREPARE_ACTION', rpcId: rpc.id, jobId: rpc.jobId, action: s.currentJob.action },
                { documentId: rpc.sourceDocumentId },
              );
              if (receipt && (receipt as { received?: boolean }).received) {
                rpc.delivery = 'received';
                await persist();
              }
            } catch { /* leave for next tick */ }
          }
          return;
        }
        // budget exhausted → fail (no tabs.update, no re-submit)
        const jobId = s.currentJob!.id;
        const msg = `form_action_prepare_failed:${prepared.error ?? 'unknown'}`;
        s.phase = 'error';
        s.phaseStartedAt = Date.now();
        s.lastError = { kind: 'nav_error', error: msg };
        s.navigation = null;
        s.pendingRpc = null;
        if (deps.api) await deps.api.failJob(jobId, msg);
        await persist();
        return;
      }

      // amend §5.2: persist a NEW form_action NavigationState (source doc copied,
      // requestId/commit/http/pageReady/acceptedUrl ALL absent). This REPLACES the
      // old navigate-kind navigation (full assignment, not merge).
      const sourceDocumentId = rpc.sourceDocumentId;
      const performRpcId = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `perf-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const issuedAt = rpc.issuedAt;   // carry the prepare's issuedAt so the tick's test-clock `now` drives the 30s deadline
      s.navigation = {
        jobId: s.currentJob!.id,
        requestedUrl: prepared.targetUrl ?? s.currentJob!.url,
        issuedAt,
        attempt: 1,
        kind: 'form_action',
        sourceDocumentId,
        action: {
          expectedEffect: prepared.expectedEffect ?? 'unknown',
          method: prepared.method ?? 'GET',
          preparationFingerprint: prepared.preparationFingerprint ?? '',
          invocationReported: false,
          effectConfirmed: false,
        },
      };
      s.pendingRpc = {
        id: performRpcId, jobId: s.currentJob!.id, op: 'perform_action',
        sourceDocumentId, delivery: 'prepared', issuedAt: Date.now(), resultDeadlineAt: Date.now() + 30_000,
        recoveryAttempts: 0, nextRecoveryAt: null,
      };
      s.phase = 'acting';
      s.phaseStartedAt = Date.now();
      await persist();   // persist-before-perform
      if (s.boundTabId !== null && deps.chrome?.sendMessage) {
        try {
          const receipt = await deps.chrome.sendMessage(
            s.boundTabId,
            { op: 'PERFORM_ACTION', rpcId: performRpcId, jobId: s.currentJob!.id, action: s.currentJob!.action!, preparationFingerprint: prepared.preparationFingerprint ?? '' },
            { documentId: sourceDocumentId },
          );
          if (receipt && (receipt as { received?: boolean }).received) {
            s.pendingRpc!.delivery = 'received';
            await persist();
          }
        } catch {
          // amend §4.2: perform reject with no receipt/requestId/commit → content_unavailable/action_result
          s.phase = 'error';
          s.phaseStartedAt = Date.now();
          s.lastError = {
            kind: 'content_unavailable', missing: 'action_result', sourceDocumentId,
            since: Date.now(), recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: true,
          };
          s.pendingRpc = null;
          await persist();
        }
      }
    } finally { release(); }
  }

  /** Handle ACTION_RESULT from the content script (amend §5.3–§5.5, §4.1).
   *  Re-acquires the mutex like the other deliver* handlers. */
  async function deliverActionResult(
    result: ActionResult,
    sender: { tab?: { id: number }; frameId?: number; documentId?: string },
  ): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const rpc = s.pendingRpc;
      if (!rpc || rpc.op !== 'perform_action') return;
      // amend §4.1 validation — any mismatch is a late/stale result, no-op.
      if (sender.tab?.id !== s.boundTabId) return;
      if (sender.frameId !== 0) return;
      if (sender.documentId !== rpc.sourceDocumentId) return;
      if (result.rpcId !== rpc.id || result.jobId !== s.currentJob?.id) return;
      const nav = s.navigation;
      if (!nav?.action) return;

      if (!result.ok || !result.invoked) {
        // amend §5.3: fingerprint mismatch or invoke failed → fail (never nav funnel, NO tabs.update).
        const jobId = s.currentJob!.id;
        const detail = result.error ?? (result.invoked ? 'invoke_failed' : 'not_invoked');
        const msg = result.error === 'form_action_prepare_changed'
          ? 'form_action_prepare_changed'
          : `form_action_invoke_failed:${detail}`;
        s.phase = 'error';
        s.phaseStartedAt = Date.now();
        s.lastError = { kind: 'nav_error', error: msg };
        s.navigation = null;
        s.pendingRpc = null;
        if (deps.api) await deps.api.failJob(jobId, msg);
        await persist();
        return;
      }

      nav.action.invocationReported = true;
      if (result.navigationExpected) {
        // Wait the §2.3 five conditions (the new main-frame onBeforeRequest binds
        // a fresh requestId in phase==='acting' — already handled by slice-3
        // deliverBeforeRequest's acting guard). amend §5.4: NEVER create a new
        // perform rpcId once a correlated requestId appears — KEEP pendingRpc so
        // the tick's perform-recovery path does NOT mint a fresh rpcId.
        await persist();
        return;
      }
      if (result.effectApplied) {
        // same-document effect confirmed (amend §5.5) → landed → capture the source document.
        nav.action.effectConfirmed = true;
        s.pendingRpc = null;
        await persist();
        await dispatchCapture(s, Date.now());
        return;
      }
      // neither navigation nor effect — keep acting; recovery budget handles a timeout (amend §5.6)
      await persist();
    } finally { release(); }
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

  /** Handle a CAPTURE_RESULT from the content script (amend §2.3, §4.1, §6.1).
   *  Re-acquires the mutex like the other deliver* handlers. Validates the
   *  sender per amend §4.1; on mismatch → log + no-op (late/stale). On a
   *  second-detection failure routes per amend §2.3; on success completes the
   *  job and returns to idle for the next reconcile to clear currentJob. */
  async function deliverCaptureResult(
    result: CaptureResult,
    sender: { tab?: { id: number }; frameId?: number; documentId?: string },
  ): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const rpc = s.pendingRpc;
      if (!rpc || rpc.op !== 'capture') return;
      // amend §4.1 validation — any mismatch is a late/stale result, no-op.
      if (sender.tab?.id !== s.boundTabId) return;
      if (sender.frameId !== 0) return;
      if (sender.documentId !== rpc.sourceDocumentId) return;
      if (result.rpcId !== rpc.id || result.jobId !== s.currentJob?.id) return;
      // second-detection failure → amend §2.3 routing (clear rpc, exit capturing)
      if (!result.ok) {
        s.pendingRpc = null;
        const detection = result.detection;
        if (detection?.terminalReason === 'not_found'
            || detection?.terminalReason === 'content_removed'
            || detection?.terminalReason === 'empty_page') {
          // terminal skip from capturing (amend §8.1 #18)
          const reason = detection.terminalReason;
          const jobId = s.currentJob!.id;
          s.phase = 'error'; s.phaseStartedAt = Date.now(); s.lastError = null; s.navigation = null;
          if (deps.api) await deps.api.skipJob(jobId, reason);
        } else if (detection?.errorPage && s.navigation?.kind === 'navigate') {
          // gateway funnel: clear signals, bump attempt, re-navigate
          const nav = s.navigation!;
          clearAttemptSignals(nav);
          nav.attempt += 1;
          nav.issuedAt = Date.now();
          await navigateNow(s, Date.now());
        } else {
          // form_action soft-error or unknown → fail form_action_navigation_failed (amend §5.6)
          const jobId = s.currentJob!.id;
          const msg = `form_action_navigation_failed:${result.error ?? 'soft_error'}`;
          s.phase = 'error'; s.phaseStartedAt = Date.now();
          s.lastError = { kind: 'nav_error', error: msg };
          s.navigation = null;
          if (deps.api) await deps.api.failJob(jobId, msg);
        }
        await persist();
        return;
      }
      // success → complete
      const jobId = s.currentJob!.id;
      if (deps.api) {
        await deps.api.completeJob(
          jobId, result.html ?? '', result.url, result.title ?? '', result.paginationStates,
        );
      }
      s.pendingRpc = null;
      s.navigation = null;
      s.phase = 'idle';            // reconcile clears currentJob if backend released it
      s.phaseStartedAt = Date.now();
      s.lastError = null;
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
    deliverCaptureResult,
    deliverActionPrepared,
    deliverActionResult,

    async dispatchPrepareAction(action: FetchAction): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (s.phase !== 'landed') return;   // only a landed page can host a form prepare
        await prepareFormActionDispatch(s, Date.now(), action);
      } finally { release(); }
    },

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

        // 3.5. /decision poll (slice 5) — bound + connected only. Persist on change
        //      via the saveIfChanged umbrella (spec §2.2). On null, clear it.
        if (s.connected && s.boundTabId !== null && deps.api) {
          const dec = await deps.api.getDecision();
          const changed = (dec?.id ?? null) !== (s.pendingDecision?.id ?? null);
          if (changed) {
            s.pendingDecision = dec;
          }
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

        // 6.5. Capture RPC recovery (amend §6.1) — re-send same rpcId past
        //      result deadline OR recovery gap, ≤3 re-sends, ≥5s apart.
        //      NOTE: the recovery condition fires on EITHER the result-deadline
        //      expiring (the FIRST re-send, before nextRecoveryAt is set) OR the
        //      ≥5s recovery gap elapsing (subsequent re-sends, where the deadline
        //      was reset to now+30s on the previous send). The brief's verbatim
        //      `now >= resultDeadlineAt && ...` would block re-sends #2/#3
        //      because each re-send resets resultDeadlineAt=now+30000; the
        //      exhaust test (ticks 40k/45k/50k/55k) drives 10s apart and needs
        //      a re-send on each of the first three ticks.
        if (s.phase === 'capturing' && s.pendingRpc && s.pendingRpc.op === 'capture') {
          const rpc = s.pendingRpc;
          const deadlineExpired = now >= rpc.resultDeadlineAt;
          const gapElapsed = rpc.nextRecoveryAt !== null && now >= rpc.nextRecoveryAt;
          if ((deadlineExpired || gapElapsed) && rpc.recoveryAttempts < 3) {
            rpc.recoveryAttempts += 1;
            rpc.nextRecoveryAt = now + 5_000;
            rpc.resultDeadlineAt = now + 30_000;
            rpc.delivery = 'prepared';
            rpc.issuedAt = now;
            await persist();
            if (s.boundTabId !== null && deps.chrome?.sendMessage) {
              try {
                const receipt = await deps.chrome.sendMessage(
                  s.boundTabId,
                  { op: 'CAPTURE', rpcId: rpc.id, jobId: rpc.jobId },
                  { documentId: rpc.sourceDocumentId },
                );
                if (receipt && (receipt as { received?: boolean }).received) {
                  rpc.delivery = 'received';
                  await persist();
                }
              } catch {
                // target doc gone → content_unavailable
                s.phase = 'error'; s.phaseStartedAt = now;
                s.lastError = {
                  kind: 'content_unavailable', missing: 'capture_result',
                  sourceDocumentId: rpc.sourceDocumentId, since: rpc.issuedAt,
                  recoveryAttempts: rpc.recoveryAttempts, nextRecoveryAt: null,
                  recoveryExhausted: true,
                };
                s.pendingRpc = null;
              }
            }
          } else if (rpc.recoveryAttempts >= 3 && now >= (rpc.nextRecoveryAt ?? 0)) {
            // budget exhausted → content_unavailable/capture_result
            s.phase = 'error'; s.phaseStartedAt = now;
            s.lastError = {
              kind: 'content_unavailable', missing: 'capture_result',
              sourceDocumentId: rpc.sourceDocumentId, since: rpc.issuedAt,
              recoveryAttempts: rpc.recoveryAttempts, nextRecoveryAt: null,
              recoveryExhausted: true,
            };
            s.pendingRpc = null;
          }
        }

        // 6.6. Form-action failure deadline (amend §5.6): acting + form_action
        //      navigation + no correlated requestId/commit/effectConfirmed, and
        //      30s elapsed since the form_action navigation was issued → fail
        //      form_action_navigation_failed:timeout. NO tabs.update, NO re-submit.
        if (s.phase === 'acting' && s.navigation?.kind === 'form_action' && s.navigation.action) {
          const hasSignal =
            !!s.navigation.requestId || !!s.navigation.commit || s.navigation.action.effectConfirmed;
          if (!hasSignal && now >= s.navigation.issuedAt + 30_000) {
            const jobId = s.currentJob!.id;
            s.phase = 'error'; s.phaseStartedAt = now;
            s.lastError = { kind: 'nav_error', error: 'form_action_navigation_failed:timeout' };
            s.navigation = null;
            s.pendingRpc = null;
            if (deps.api) await deps.api.failJob(jobId, 'form_action_navigation_failed:timeout');
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

    async setPaused(paused: boolean, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        s.paused = paused;
        s.phaseStartedAt = now;
        await persist();
      } finally {
        release();
      }
    },

    async unbind(now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        s.boundTabId = null;
        s.boundAt = null;
        // keep currentJob cache; drop to assigned (spec §2.5). Do NOT fail/skip.
        s.phase = s.currentJob ? 'assigned' : 'idle';
        s.phaseStartedAt = now;
        await persist();
      } finally {
        release();
      }
    },

    async manualSkip(reason: string | undefined, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (!s.currentJob) return;
        if (deps.api) await deps.api.skipJob(s.currentJob.id, reason ?? 'manual');
        s.navigation = null; s.pendingRpc = null; s.lastError = null;
        s.phase = 'assigned'; s.phaseStartedAt = now;   // job may still be current; reconcile owns clearing
        await persist();
      } finally {
        release();
      }
    },

    async manualFail(message: string | undefined, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (!s.currentJob) return;
        if (deps.api) await deps.api.failJob(s.currentJob.id, message ?? 'manual');
        s.navigation = null; s.pendingRpc = null;
        s.lastError = { kind: 'nav_error', error: message ?? 'manual' };
        s.phase = 'error'; s.phaseStartedAt = now;
        await persist();
      } finally {
        release();
      }
    },

    async overrideUrl(url: string, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (!s.currentJob || s.boundTabId === null || !deps.api) return;
        const refreshed = await deps.api.overrideJobUrl(s.currentJob.id, url);
        if (refreshed) s.currentJob = refreshed;
        s.navigation = null; s.pendingRpc = null; s.lastError = null;
        s.phase = 'assigned'; s.phaseStartedAt = now;   // next tick navigates the new URL
        await persist();
      } finally {
        release();
      }
    },

    async resolveDecision(id: string, action: string, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (deps.api) await deps.api.resolveDecision(id, action);
        if (s.pendingDecision?.id === id) { s.pendingDecision = null; }
        await persist();
      } finally {
        release();
      }
    },

    async manualComplete(now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        // submit: re-dispatch capture on the current landed/source document.
        if (s.phase !== 'landed' && !(s.phase === 'capturing' && s.pendingRpc)) return;
        s.phase = 'submitting'; s.phaseStartedAt = now;
        await persist();
        await dispatchCapture(s, now);
      } finally {
        release();
      }
    },

    async broadcastPanelState(sender?: { tabId: number | null }, _reason?: string): Promise<void> {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      if (!deps.chrome?.sendMessage) return;
      if (s.boundTabId !== null) {
        const state = buildPanelState(s, { tabId: s.boundTabId });
        try { await deps.chrome.sendMessage(s.boundTabId, { op: 'STATE_CHANGED', state }, { frameId: 0 }); } catch { /* tab gone */ }
      }
      if (sender?.tabId != null && sender.tabId !== s.boundTabId) {
        const state = buildPanelState(s, { tabId: sender.tabId });
        try { await deps.chrome.sendMessage(sender.tabId, { op: 'STATE_CHANGED', state }, { frameId: 0 }); } catch { /* tab gone */ }
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
