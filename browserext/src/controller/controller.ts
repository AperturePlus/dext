/** CrawlController — the single logical orchestrator owning all client-side
 *  navigation + backend I/O (spec §2). Slice 1 shipped the gated skeleton.
 *  Slice 2 fills the tick body with: /status reconcile (gated by
 *  nextBackendRetryAt backoff), non-RPC rehydration (invalid bound tab → unbind
 *  + keep assigned), exponential backoff on backend failure, and the owner-tab
 *  heartbeat. NO claim / navigate / RPC yet (slices 3–4). The gate constant
 *  EXCLUSIVE_CONTROL_ENABLED (injected by esbuild, default false) makes the
 *  official build a runtime no-op: tick/bind load state and return before any
 *  network or chrome call. */

import { createMutex } from './mutex.js';
import type { Mutex } from './mutex.js';
import { createControllerStorage } from './storage.js';
import type { ControllerStorage, StorageArea } from './storage.js';
import { nextRetryAt } from './backoff.js';
import { applyReconcile } from './reconcile.js';
import { createHeartbeat } from './heartbeat.js';
import type { ApiClient } from '../api.js';
import type { ChromeRuntime } from '../chrome.js';
import type { ControllerState } from '../shared/state.js';

// Re-export so callers (background.ts, tests) can build a storage + controller
// from a single import entry point.
export { createControllerStorage };
export type { ControllerStorage, StorageArea };

declare const EXCLUSIVE_CONTROL_ENABLED: boolean;

/** The 1-minute chrome.alarms waker that drives controller.tick() — the
 *  reconciliation alarm that REPLACES the Phase-1 watchdog (spec §2.6). It does
 *  NOT re-redirect the tab on stale heartbeat; it just reruns the tick path. */
export const RECONCILIATION_ALARM_NAME = 'dext-reconcile';
export const RECONCILIATION_PERIOD_MINUTES = 1;

export interface CrawlControllerDeps {
  storage?: ControllerStorage;
  area?: StorageArea;
  /** Backend HTTP client. Optional: when absent, tick skips /status reconcile
   *  + heartbeat (used by slice-1 skeleton tests; the real build always wires it). */
  api?: ApiClient;
  /** Chrome surface slice. Optional: when absent, tick skips the bound-tab
   *  validity check (assumes a bound tab is still valid). */
  chrome?: Pick<ChromeRuntime, 'getTab'>;
}

export interface CrawlController {
  tick(now?: number): Promise<void>;
  bind(tabId: number, now?: number): Promise<void>;
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

  return {
    async tick(now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;          // gated no-op

        const prev = structuredClone(s);

        // 1. Bound-tab validity rehydration (spec §2.5 invalid-bound-tab row):
        //    if the persisted boundTabId no longer maps to a real tab, unbind but
        //    KEEP currentJob as `assigned` — do NOT auto fail/skip. Wait for a
        //    re-bind or manual action.
        if (s.boundTabId !== null && deps.chrome?.getTab) {
          const tab = await deps.chrome.getTab(s.boundTabId);
          if (tab === null) {
            s.boundTabId = null;
            s.boundAt = null;
            s.phase = s.currentJob ? 'assigned' : 'idle';
            s.phaseStartedAt = now;
          }
        }

        // 2. /status reconcile, gated by nextBackendRetryAt (spec §2.3 step 1).
        //    Backoff gates /status — NOT heartbeat (step 3 below still runs).
        const backoffExpired = s.nextBackendRetryAt === null || now >= s.nextBackendRetryAt;
        if (backoffExpired && deps.api) {
          const status = await deps.api.getStatus();
          if (status === null) {
            // backend unreachable (spec §2.3 step 2): never navigate/refresh.
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

        // 3. Heartbeat — every tick when bound; NOT gated by backoff or paused
        //    (spec §2.4). The api.sendHeartbeat swallows errors. Cadence is set
        //    by whoever drives tick (1-min alarm now; 2s content TICK in slice 4).
        if (s.boundTabId !== null && heartbeat) {
          await heartbeat.send(s, now);
        }

        // 4. Persist only on actual change (spec §2.2 write-frequency invariant).
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

/** Minimal in-memory StorageArea used when no chrome.storage is present
 *  (e.g. ad-hoc import in node without a fake). Tests pass their own area. */
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
