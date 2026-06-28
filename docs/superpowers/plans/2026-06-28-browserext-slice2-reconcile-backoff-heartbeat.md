# Browserext exclusive control — Slice 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land slice 2 of the Phase-2 exclusive-control design: the Controller's `/status` reconciliation (gated by `nextBackendRetryAt` backoff), the non-RPC rehydration rows, exponential backoff on backend failure, the owner-tab heartbeat (now owned by the extension since the userscript stands down under the marker), and the 1-minute `chrome.alarms` reconciliation waker that drives the same tick path — **replacing and deleting the Phase-1 `watchdog.ts`**. No `/jobs/next` claim yet (slice 3). The whole slice stays gated OFF (`EXCLUSIVE_CONTROL_ENABLED = false` in the official build), so the runtime is still a no-op and all Phase-1 tests stay green.

**Architecture:** Three new pure/small controller modules — `backoff.ts` (exponential-backoff math), `reconcile.ts` (pure `/status.current_job` → state mutation, including stale-navigation discard and the non-RPC rehydration transitions), and `heartbeat.ts` (POST `/heartbeat` with a deterministic stable `ownerTabId` derived from persisted binding fields). The existing `controller.ts` wires them into the tick body behind the gate, persists only on change (`saveIfChanged`), and adds a bound-tab-validity rehydration step using a new `chrome.getTab`. `chrome.ts` renames `registerWatchdogAlarm`→`registerAlarm` and gains `getTab`. `api.ts` gains `sendHeartbeat` + a `HeartbeatPayload` type and widens `StatusPayload.current_job` to the full `FetchJob` (the backend already sends it — type accuracy, not a contract change). `background.ts` drops the watchdog and wires the reconciliation alarm to `controller.tick()`. `watchdog.ts` + its test are deleted.

**Tech Stack:** TypeScript 5.7, MV3 Chrome/Edge (≥110), esbuild, node:test, `chrome.storage.local`/`chrome.alarms`/`chrome.tabs`, WebWorker lib (background config).

## Global Constraints

Copied verbatim from the spec/amend + slice-1 plan so every task implicitly inherits them:

- **Gate default false.** Slice 1–5 official builds default `EXCLUSIVE_CONTROL_ENABLED = false`; the Controller's tick body short-circuits on `if (!EXCLUSIVE_CONTROL_ENABLED) return;` — no `/status`, no heartbeat, no navigation. Only unit tests (harness injects `true`) or an explicit test build enable it. (spec §6.1)
- **`/status.current_job` is the sole job-state truth.** The Controller's `currentJob` is a client cache only; reconcile discards stale `navigation`/`pendingRpc` only when the job id changes (spec §2.3 step 1, amend §9).
- **Backend unreachable never navigates/refreshes.** On `/status` failure: `connected=false`, bump `backendFailureCount`, set `nextBackendRetryAt`, keep current phase + page, release lock. (spec §2.3 step 2, §1.3)
- **Backoff gates `/status`, not heartbeat.** The tick skips `/status` while `now < nextBackendRetryAt`; heartbeat still fires every tick when bound. (spec §2.3 step 1, §2.4)
- **`paused` does not stop `/status` reconcile or heartbeat.** It stops auto side-effects (claim/navigate/capture/retry) — none of which exist in slice 2, so `paused` does not alter slice-2 tick behavior. (spec §2.4)
- **2s TICK does not unconditionally write storage.** The tick captures `prev = structuredClone(state)` before mutating and calls `saveIfChanged(prev, state)` at the end — no write when nothing changed. (spec §2.2, §6.3 storage-write risk)
- **HTTP contract is FIXED.** No new/changed endpoints, no DB schema change, no second job state machine. `POST /heartbeat` already exists (backend `handle_heartbeat`); the extension now calls it instead of the userscript. (CLAUDE.md, spec §1.3, amend §9)
- **Single in-flight + one bound tab.** `boundTabId: number | null`; at most one. Invalid bound tab → unbind (`boundTabId=null`), keep `currentJob` cache as `assigned`, do NOT auto fail/skip. (spec §1.2, §2.5 invalid-bound-tab row)
- **`ownerTabId` for heartbeat is deterministic + stable across SW restarts.** `dext-ext-${boundTabId}-${boundAt}` — both fields persisted in `ControllerState`; no extra storage key, no crypto. The backend uses `owner_tab_id` only as a label (`is_alive` is time-based, `health.py`). (spec §2.5 rehydration across restarts)
- **Reconciliation alarm drives the same tick path.** A 1-minute `chrome.alarms` (`dext-reconcile`) calls `controller.tick()`. It is NOT a separate watchdog module and never re-redirects the tab on stale heartbeat — that behavior is removed. (spec §2.6, §1.4)
- **`watchdog.ts` is deleted in this slice** (replaced by the reconciliation alarm). `navMonitor.ts` is NOT deleted (it is refactored to a thin adapter in slice 3 per amend §3.2). `status.ts` is NOT changed this slice (its classifier rewrite is slice 3). (spec §1.4, §6.2 slice-2 row)
- **Phase-1 tests that are not touched by this slice stay green.** `navMonitor`/`status`/`api`(existing cases)/`chrome`/`storage` behavior is unchanged except: `chrome.ts` renames one method + adds one method (fakes updated to match), `api.ts` widens a type + adds a method (existing assertions still hold), `background.ts` drops watchdog (its test updated). (spec §5.5, amend §8.1)
- **One conventional commit per green step.** `feat(browserext)`/`test(browserext)`/`chore(browserext)`/`docs(browserext)`. (CLAUDE.md)
- **browserext tests run with `cd browserext && npm test` (node:test, no pytest).** Relative imports in `src/*.ts` carry `.js`. The harness injects `EXCLUSIVE_CONTROL_ENABLED = true` so gated-ON paths are exercised. (CLAUDE.md)
- **`dist/` is gitignored.**

---

## File Structure

New/modified/deleted files in this slice. Decomposition: backoff math, reconcile logic, and heartbeat are three separable pure-ish responsibilities the controller composes — keep them in separate small files so each is unit-testable in isolation.

```
browserext/
  src/api.ts                          # MODIFY: add sendHeartbeat + HeartbeatPayload; widen StatusPayload.current_job to FetchJob
  src/chrome.ts                       # MODIFY: rename registerWatchdogAlarm → registerAlarm; add getTab(tabId)
  src/controller/backoff.ts           # CREATE: pure computeBackoffMs / nextRetryAt
  src/controller/reconcile.ts         # CREATE: pure applyReconcile(state, currentJob, now)
  src/controller/heartbeat.ts         # CREATE: createHeartbeat(api) + ownerTabIdFor(state)
  src/controller/controller.ts        # MODIFY: wire reconcile+backoff+heartbeat+tab-validity into tick; saveIfChanged; re-export alarm constants
  src/background.ts                   # MODIFY: drop watchdog; wire reconciliation alarm → controller.tick(); pass api+chrome to controller
  src/watchdog.ts                     # DELETE
  tests/controller/backoff.test.mjs   # CREATE
  tests/controller/reconcile.test.mjs # CREATE
  tests/controller/heartbeat.test.mjs # CREATE
  tests/controller/controller.test.mjs# MODIFY: add reconcile/backoff/heartbeat/rehydration tick tests
  tests/chrome.test.mjs               # MODIFY: rename fake registerWatchdogAlarm → registerAlarm
  tests/navMonitor.test.mjs           # MODIFY: rename fake registerWatchdogAlarm → registerAlarm (one line)
  tests/background.test.mjs           # MODIFY: drop watchdog assertions; assert alarm wires to controller.tick
  tests/watchdog.test.mjs             # DELETE
  tests/api.test.mjs                  # MODIFY: add sendHeartbeat case (widened current_job cases already green)
docs/superpowers/specs/...            # no change this slice
```

Boundary notes:
- `src/controller/backoff.ts` + `reconcile.ts` are pure (no chrome, no IO, no fetch) — import only `shared/state` + `shared/types` types.
- `src/controller/heartbeat.ts` imports only `ApiClient` (type) + `shared/state` (type) + `HeartbeatPayload` (type from `api.ts`) — no chrome, no fetch directly; it calls `api.sendHeartbeat`.
- `src/controller/controller.ts` may import `ApiClient` + `ChromeRuntime` types and the three new modules. It runs in the SW.
- `api.ts` (Phase-1 location) now imports `FetchJob` from `./shared/types.js` (type-only, acyclic, no DOM/worker globals).
- `chrome.ts` `registerAlarm` keeps the module-level `registeredAlarms` dedupe Set; only the method name changes.

---

## Task 1: Extend ApiClient — heartbeat + full-job /status

**Files:**
- Modify: `browserext/src/api.ts`
- Modify: `browserext/tests/api.test.mjs`

**Interfaces:**
- Consumes: `FetchJob` from `./shared/types.js` (exists from slice 1).
- Produces: `HeartbeatPayload` interface; `ApiClient.sendHeartbeat(payload: HeartbeatPayload): Promise<void>` (swallows errors, mirrors userscript `sendHeartbeat`); `StatusPayload.current_job: FetchJob | null` (widened from the old `CurrentJob` subset — the backend already serializes the full job via `serialize_job`). The old `CurrentJob` type is kept as an alias for backward reference but no longer used in `StatusPayload`.

- [ ] **Step 1: Write the failing test for sendHeartbeat**

Add to `browserext/tests/api.test.mjs` (after the last existing test):
```js
test('sendHeartbeat POSTs the heartbeat payload', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let captured = null;
    const fetchFn = async (url, init) => {
      captured = { url, method: init.method, body: JSON.parse(init.body) };
      return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.sendHeartbeat({
      owner_tab_id: 'dext-ext-7-1000',
      url: 'https://x.edu.cn/p',
      current_job_id: 'job-1',
      auto_mode: true,
      paused: false,
      timestamp: 12345,
    });
    assert.equal(captured.url, 'http://127.0.0.1:21520/api/heartbeat');
    assert.equal(captured.method, 'POST');
    assert.deepEqual(captured.body, {
      owner_tab_id: 'dext-ext-7-1000',
      url: 'https://x.edu.cn/p',
      current_job_id: 'job-1',
      auto_mode: true,
      paused: false,
      timestamp: 12345,
    });
  } finally {
    await cleanup();
  }
});

test('sendHeartbeat swallows errors (backend down is reflected by /status, not heartbeat)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNREFUSED'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.sendHeartbeat({
      owner_tab_id: 'dext-ext-7-1000', url: '', current_job_id: null,
      auto_mode: false, paused: false, timestamp: 1,
    });
  } finally {
    await cleanup();
  }
});

test('getStatus returns the full FetchJob shape for current_job (widened type)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fullJob = {
      id: 'abc', url: 'https://x.edu.cn/p', status: 'assigned',
      context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
      created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
    };
    const fetchFn = fakeFetch({
      'GET /api/status': { status: 200, body: {
        queue: { pending: 0, assigned: 1, completed: 0, failed: 0, skipped: 0 },
        current_job: fullJob,
        frontend_health: { alive: true, last_seen_seconds_ago: 1 },
      } },
    });
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s.current_job.id, 'abc');
    assert.equal(s.current_job.url, 'https://x.edu.cn/p');
    assert.equal(s.current_job.status, 'assigned');
    assert.equal(s.current_job.timeout_seconds, 60);
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='sendHeartbeat|widened type' 2>&1 | tail -20`
Expected: FAIL — `api.sendHeartbeat is not a function` and the widened-type test errors on missing fields (current type is the subset `CurrentJob`, so `.status`/`.timeout_seconds` may still read through at runtime but the test asserts the full shape is returned; the decisive failure is `sendHeartbeat is not a function`).

- [ ] **Step 3: Modify api.ts**

Edit `browserext/src/api.ts`:

1. Add the type-only import at the top (after the file doc comment, before `export interface CurrentJob`):
```ts
import type { FetchJob } from './shared/types.js';
```

2. Keep `CurrentJob` as a backward-compatible alias but widen `StatusPayload.current_job`. Replace the `StatusPayload` interface:
```ts
export interface StatusPayload {
  current_job: FetchJob | null;
  frontend_health: FrontendHealth;
}
```
(`CurrentJob` stays defined as `{ id: string; url: string }` — navMonitor still reads `job.id`/`job.url` off the full `FetchJob`, structurally compatible.)

3. Add `HeartbeatPayload` and `sendHeartbeat` to the `ApiClient` interface and impl. Replace the `ApiClient` interface:
```ts
export interface ApiClient {
  getStatus(): Promise<StatusPayload | null>;
  failJob(jobId: string, message: string): Promise<void>;
  skipJob(jobId: string, reason: string): Promise<void>;
  sendHeartbeat(payload: HeartbeatPayload): Promise<void>;
}

export interface HeartbeatPayload {
  owner_tab_id: string;
  url: string;
  current_job_id: string | null;
  auto_mode: boolean;
  paused: boolean;
  timestamp: number;
}
```

4. Add the impl inside `createFetchApi`, after `skipJob` and before the `return`:
```ts
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
```

5. Update the final `return` to include `sendHeartbeat`:
```ts
  return { getStatus, failJob, skipJob, sendHeartbeat };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='sendHeartbeat|widened type' 2>&1 | tail -20`
Expected: PASS — all three new tests green.

- [ ] **Step 5: Run the full suite + typecheck to confirm no regression**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; tests `pass 56` (53 baseline + 3 new) `fail 0`. The existing api/navMonitor tests still pass (they read `.id`/`.url` which the widened `FetchJob` still carries).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/api.ts browserext/tests/api.test.mjs
git commit -m "feat(browserext): add sendHeartbeat + widen /status.current_job to full FetchJob"
```

---

## Task 2: Pure backoff math

**Files:**
- Create: `browserext/src/controller/backoff.ts`
- Create: `browserext/tests/controller/backoff.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces: `computeBackoffMs(failureCount: number): number` (pure, deterministic; `min(2000 * 2^(count-1), 60000)`, 0 for count ≤ 0); `nextRetryAt(failureCount: number, now: number): number` (`now + computeBackoffMs(failureCount)`).

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/backoff.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('computeBackoffMs: exponential 2s base, 60s cap, 0 for non-positive', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/backoff.ts', 'backoff.ts');
  try {
    assert.equal(mod.computeBackoffMs(0), 0);
    assert.equal(mod.computeBackoffMs(-1), 0);
    assert.equal(mod.computeBackoffMs(1), 2000);
    assert.equal(mod.computeBackoffMs(2), 4000);
    assert.equal(mod.computeBackoffMs(3), 8000);
    assert.equal(mod.computeBackoffMs(4), 16000);
    assert.equal(mod.computeBackoffMs(5), 32000);
    assert.equal(mod.computeBackoffMs(6), 60000);   // 64s capped to 60s
    assert.equal(mod.computeBackoffMs(7), 60000);   // stays capped
    assert.equal(mod.computeBackoffMs(100), 60000);
  } finally {
    await cleanup();
  }
});

test('nextRetryAt = now + computeBackoffMs(count)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/backoff.ts', 'backoff.ts');
  try {
    assert.equal(mod.nextRetryAt(0, 1000), 1000);          // no backoff → retry immediately (now)
    assert.equal(mod.nextRetryAt(1, 1000), 3000);          // 1000 + 2000
    assert.equal(mod.nextRetryAt(3, 1000), 9000);          // 1000 + 8000
    assert.equal(mod.nextRetryAt(6, 1000), 61000);         // 1000 + 60000 (capped)
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='computeBackoffMs|nextRetryAt' 2>&1 | tail -15`
Expected: FAIL — `../src/controller/backoff.ts` does not exist (ENOENT on entry).

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/backoff.ts`:
```ts
/** Exponential backoff for /status retries across SW restarts (spec §2.3 step 2).
 *  Pure + deterministic so it is unit-testable without a clock. delay(count) =
 *  min(BASE * 2^(count-1), CAP); count <= 0 → 0 (no backoff, retry immediately).
 *  Count is the number of consecutive backend failures; the controller bumps it
 *  before computing the next gate, so the first failure (count=1) waits BASE. */

const BASE_MS = 2000;
const CAP_MS = 60_000;

export function computeBackoffMs(failureCount: number): number {
  if (failureCount <= 0) return 0;
  return Math.min(BASE_MS * 2 ** (failureCount - 1), CAP_MS);
}

export function nextRetryAt(failureCount: number, now: number): number {
  return now + computeBackoffMs(failureCount);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='computeBackoffMs|nextRetryAt' 2>&1 | tail -15`
Expected: PASS — both tests green.

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0 (new module under `src/controller/**`, included by the background config, imports nothing).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/backoff.ts browserext/tests/controller/backoff.test.mjs
git commit -m "feat(browserext): pure exponential backoff for /status retries"
```

---

## Task 3: Pure /status reconcile (non-RPC rehydration)

**Files:**
- Create: `browserext/src/controller/reconcile.ts`
- Create: `browserext/tests/controller/reconcile.test.mjs`

**Interfaces:**
- Consumes: `ControllerState` from `../shared/state.js` (type), `FetchJob` from `../shared/types.js` (type).
- Produces: `applyReconcile(state: ControllerState, currentJob: FetchJob | null, now: number): void` — mutates `state` in place to mirror `/status.current_job`:
  - `currentJob === null` (backend released the slot): if `state.currentJob !== null` → clear `currentJob`/`navigation`/`pendingRpc`/`lastError`, `phase='idle'`, `phaseStartedAt=now`. (amend §6.3 `submitting`/released-row + spec §2.5)
  - `currentJob !== null` and (`state.currentJob === null` OR id differs): new/different job → set `currentJob`, **discard stale `navigation`/`pendingRpc`/`lastError`** (spec §2.3 step 1), `phase='assigned'`, `phaseStartedAt=now`. (Binding may be null — `assigned` keeps the cache waiting for a bind; §2.5 assigned row.)
  - `currentJob !== null` and same id: **no mutation** — keep in-flight `navigation`/`pendingRpc` (do NOT clobber signals from the current attempt; §2.3 step 1 only discards *stale* ones).

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/reconcile.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function freshState(overrides = {}) {
  return {
    boundTabId: null, boundAt: null, connected: false, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 0, navigation: null, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
    ...overrides,
  };
}

function job(id, url = `https://x.edu.cn/${id}`) {
  return {
    id, url, status: 'assigned',
    context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
    created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
  };
}

test('backend releases job (current_job null) → clear cache to idle', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ currentJob: job('job-1'), phase: 'assigned', phaseStartedAt: 100 });
    mod.applyReconcile(s, null, 5000);
    assert.equal(s.currentJob, null);
    assert.equal(s.phase, 'idle');
    assert.equal(s.phaseStartedAt, 5000);
    assert.equal(s.navigation, null);
    assert.equal(s.pendingRpc, null);
    assert.equal(s.lastError, null);
  } finally {
    await cleanup();
  }
});

test('backend releases job when already idle + no cache → no-op', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ phase: 'idle', phaseStartedAt: 100 });
    const before = JSON.stringify(s);
    mod.applyReconcile(s, null, 5000);
    assert.equal(JSON.stringify(s), before, 'no mutation when nothing to clear');
  } finally {
    await cleanup();
  }
});

test('new job id → cache job, discard stale nav/pendingRpc, phase=assigned', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    // simulate stale signals from a previous job that the controller had reached (slice 3/4 will set these)
    const staleNav = { jobId: 'old', requestedUrl: 'https://x.edu.cn/old', issuedAt: 10, attempt: 2, kind: 'navigate' };
    const s = freshState({
      currentJob: job('old'), phase: 'navigating', phaseStartedAt: 10,
      navigation: staleNav, pendingRpc: { id: 'rpc-old', jobId: 'old', op: 'capture', sourceDocumentId: 'D1', delivery: 'received', issuedAt: 11, resultDeadlineAt: 41000 },
      lastError: { kind: 'nav_error', error: 'ERR_X' },
    });
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'assigned');
    assert.equal(s.phaseStartedAt, 5000);
    assert.equal(s.navigation, null, 'stale navigation discarded on job change');
    assert.equal(s.pendingRpc, null, 'stale pendingRpc discarded on job change');
    assert.equal(s.lastError, null, 'stale error discarded on job change');
  } finally {
    await cleanup();
  }
});

test('first job from empty state → cache + assigned (boundTabId null keeps assigned, waiting for bind)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ phase: 'idle' });
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'assigned');
  } finally {
    await cleanup();
  }
});

test('same job id → keep in-flight navigation/pendingRpc (do NOT clobber current attempt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const nav = { jobId: 'job-1', requestedUrl: 'https://x.edu.cn/job-1', issuedAt: 10, attempt: 1, kind: 'navigate' };
    const rpc = { id: 'rpc-1', jobId: 'job-1', op: 'capture', sourceDocumentId: 'D1', delivery: 'received', issuedAt: 11, resultDeadlineAt: 41000 };
    const s = freshState({
      currentJob: job('job-1'), phase: 'capturing', phaseStartedAt: 10,
      navigation: nav, pendingRpc: rpc,
    });
    const before = JSON.stringify(s);
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(JSON.stringify(s), before, 'same job id → no mutation; in-flight signals preserved');
  } finally {
    await cleanup();
  }
});

test('does not touch boundTabId / autoMode / paused / connected / backoff fields', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ boundTabId: 7, boundAt: 1000, autoMode: true, paused: false, connected: true, backendFailureCount: 3, nextBackendRetryAt: 9999 });
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(s.boundTabId, 7);
    assert.equal(s.boundAt, 1000);
    assert.equal(s.autoMode, true);
    assert.equal(s.paused, false);
    assert.equal(s.connected, true, 'reconcile does not set connected (controller owns that)');
    assert.equal(s.backendFailureCount, 3, 'reconcile does not touch backoff (controller owns that)');
    assert.equal(s.nextBackendRetryAt, 9999);
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='backend releases|new job id|same job id|first job|does not touch' 2>&1 | tail -20`
Expected: FAIL — `../src/controller/reconcile.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/reconcile.ts`:
```ts
/** Pure /status.current_job → ControllerState reconciliation (spec §2.3 step 1,
 *  §2.5 rehydration, amend §6.3 non-RPC rows). Mutates `state` in place.
 *
 *  - Backend released the slot (currentJob null): if we held a cached job, clear
 *    it + any stale navigation/pendingRpc/lastError and go idle.
 *  - New/different job id: cache the job, DISCARD stale navigation/pendingRpc/
 *    lastError (a previous attempt's signals must not leak onto a new job), and
 *    go `assigned` (have a job but not navigating yet — slice 3 claims; an
 *    unbound controller stays assigned waiting for a bind).
 *  - Same job id: NO mutation — preserve in-flight navigation/pendingRpc so a
 *    reconcile mid-attempt does not clobber current signals.
 *
 *  This function owns ONLY the currentJob/navigation/pendingRpc/lastError/phase
 *  fields. `connected`, `backendFailureCount`, `nextBackendRetryAt`,
 *  `boundTabId`, `autoMode`, `paused` are the controller's (or bind's) concern. */

import type { ControllerState } from '../shared/state.js';
import type { FetchJob } from '../shared/types.js';

export function applyReconcile(state: ControllerState, currentJob: FetchJob | null, now: number): void {
  if (currentJob === null) {
    if (state.currentJob !== null) {
      state.currentJob = null;
      state.navigation = null;
      state.pendingRpc = null;
      state.lastError = null;
      state.phase = 'idle';
      state.phaseStartedAt = now;
    }
    return;
  }
  if (state.currentJob === null || state.currentJob.id !== currentJob.id) {
    state.currentJob = currentJob;
    state.navigation = null;
    state.pendingRpc = null;
    state.lastError = null;
    state.phase = 'assigned';
    state.phaseStartedAt = now;
  }
  // same job id → leave in-flight signals untouched
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='backend releases|new job id|same job id|first job|does not touch' 2>&1 | tail -20`
Expected: PASS — all six tests green.

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/reconcile.ts browserext/tests/controller/reconcile.test.mjs
git commit -m "feat(browserext): pure /status reconcile + non-RPC rehydration transitions"
```

---

## Task 4: Heartbeat module (deterministic stable ownerTabId)

**Files:**
- Create: `browserext/src/controller/heartbeat.ts`
- Create: `browserext/tests/controller/heartbeat.test.mjs`

**Interfaces:**
- Consumes: `ApiClient` (type, for `sendHeartbeat`) from `../api.js`; `HeartbeatPayload` (type) from `../api.js`; `ControllerState` (type) from `../shared/state.js`.
- Produces:
  - `ownerTabIdFor(state: ControllerState): string | null` — pure; returns `dext-ext-${boundTabId}-${boundAt}` when bound, `null` when unbound (either field null). Stable across SW restarts (both fields are persisted).
  - `createHeartbeat(api: { sendHeartbeat(p: HeartbeatPayload): Promise<void> }): Heartbeat` where `Heartbeat.send(state, now)` is a no-op when unbound, else POSTs via `api.sendHeartbeat` with `owner_tab_id=ownerTabIdFor(state)`, `url=state.currentJob?.url ?? ''`, `current_job_id=state.currentJob?.id ?? null`, `auto_mode=state.autoMode`, `paused=state.paused`, `timestamp=now`.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/heartbeat.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function state(overrides = {}) {
  return {
    boundTabId: null, boundAt: null, connected: false, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 0, navigation: null, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
    ...overrides,
  };
}

test('ownerTabIdFor: null when unbound; deterministic dext-ext-<tab>-<boundAt> when bound', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    assert.equal(mod.ownerTabIdFor(state()), null);
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: 7, boundAt: null })), null);
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: null, boundAt: 1000 })), null);
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: 7, boundAt: 1000 })), 'dext-ext-7-1000');
    // stable: same persisted fields → same id (rehydration across SW restarts)
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: 7, boundAt: 1000 })), 'dext-ext-7-1000');
  } finally {
    await cleanup();
  }
});

test('send is a no-op when unbound (no owner to heartbeat)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    const calls = [];
    const hb = mod.createHeartbeat({ async sendHeartbeat(p) { calls.push(p); } });
    await hb.send(state({ currentJob: { id: 'job-1', url: 'https://x.edu.cn/p' } }), 5000);
    assert.equal(calls.length, 0);
  } finally {
    await cleanup();
  }
});

test('send posts the full payload with stable ownerTabId when bound', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    const calls = [];
    const hb = mod.createHeartbeat({ async sendHeartbeat(p) { calls.push(p); } });
    await hb.send(state({
      boundTabId: 42, boundAt: 1000, autoMode: true, paused: false,
      currentJob: { id: 'job-1', url: 'https://x.edu.cn/p' },
    }), 7777);
    assert.deepEqual(calls, [{
      owner_tab_id: 'dext-ext-42-1000',
      url: 'https://x.edu.cn/p',
      current_job_id: 'job-1',
      auto_mode: true,
      paused: false,
      timestamp: 7777,
    }]);
  } finally {
    await cleanup();
  }
});

test('send uses empty url + null current_job_id when no currentJob (bound but idle)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    const calls = [];
    const hb = mod.createHeartbeat({ async sendHeartbeat(p) { calls.push(p); } });
    await hb.send(state({ boundTabId: 42, boundAt: 1000, autoMode: false, paused: true }), 9999);
    assert.deepEqual(calls, [{
      owner_tab_id: 'dext-ext-42-1000',
      url: '',
      current_job_id: null,
      auto_mode: false,
      paused: true,
      timestamp: 9999,
    }]);
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='ownerTabIdFor|send is a no-op|send posts|send uses empty' 2>&1 | tail -20`
Expected: FAIL — `../src/controller/heartbeat.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/heartbeat.ts`:
```ts
/** Owner-tab heartbeat, now owned by the extension (the userscript stands down
 *  under the data-dext-extension-controller marker, so it no longer heartbeats;
 *  spec §4.6 "content script does not call backend" + §2.4 "paused does not stop
 *  heartbeat"). POSTs /heartbeat every tick when bound.
 *
 *  `ownerTabId` is DETERMINISTIC — `dext-ext-${boundTabId}-${boundAt}` — so it is
 *  stable across MV3 SW restarts (both fields are persisted in ControllerState).
 *  The backend uses owner_tab_id only as a label (is_alive is time-based,
 *  health.py), so a deterministic string is correct and avoids a separate
 *  storage key + crypto. A re-bind changes boundAt → new owner id (new session). */

import type { ApiClient, HeartbeatPayload } from '../api.js';
import type { ControllerState } from '../shared/state.js';

export function ownerTabIdFor(state: ControllerState): string | null {
  if (state.boundTabId === null || state.boundAt === null) return null;
  return `dext-ext-${state.boundTabId}-${state.boundAt}`;
}

export interface Heartbeat {
  send(state: ControllerState, now: number): Promise<void>;
}

export function createHeartbeat(api: Pick<ApiClient, 'sendHeartbeat'>): Heartbeat {
  return {
    async send(state, now) {
      const ownerTabId = ownerTabIdFor(state);
      if (ownerTabId === null) return;            // unbound → no owner to heartbeat
      const payload: HeartbeatPayload = {
        owner_tab_id: ownerTabId,
        url: state.currentJob?.url ?? '',
        current_job_id: state.currentJob?.id ?? null,
        auto_mode: state.autoMode,
        paused: state.paused,
        timestamp: now,
      };
      await api.sendHeartbeat(payload);           // api.sendHeartbeat swallows errors
    },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='ownerTabIdFor|send is a no-op|send posts|send uses empty' 2>&1 | tail -20`
Expected: PASS — all four tests green.

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/heartbeat.ts browserext/tests/controller/heartbeat.test.mjs
git commit -m "feat(browserext): owner-tab heartbeat with deterministic stable ownerTabId"
```

---

## Task 5: ChromeRuntime — rename registerAlarm + add getTab

**Files:**
- Modify: `browserext/src/chrome.ts`
- Modify: `browserext/tests/chrome.test.mjs`
- Modify: `browserext/tests/navMonitor.test.mjs` (one-line fake rename)

**Interfaces:**
- Consumes: nothing new.
- Produces: `ChromeRuntime.registerAlarm(name, periodMinutes, cb)` (renamed from `registerWatchdogAlarm` — same dedupe + `chrome.alarms.create`/`onAlarm` impl); `ChromeRuntime.getTab(tabId): Promise<{ id: number; url?: string } | null>` (returns the tab if it exists, `null` if gone — used by the controller's invalid-bound-tab rehydration, spec §2.5).

- [ ] **Step 1: Update the chrome test fake (rename + new method)**

Edit `browserext/tests/chrome.test.mjs` — in `fakeRuntime()`, rename `registerWatchdogAlarm` → `registerAlarm` and add `getTab`:
```js
function fakeRuntime() {
  const completedCbs = [];
  const errorCbs = [];
  let alarmCb = null;
  let lastUpdated = null;
  let tabs = [{ id: 1, url: 'https://www.x.edu.cn/faculty' }];
  return {
    onNavCompleted(cb) { completedCbs.push(cb); },
    onNavError(cb) { errorCbs.push(cb); },
    async updateTabUrl(tabId, url) { lastUpdated = { tabId, url }; },
    async findOwnerTab() {
      const t = tabs.find((t) => /edu\.cn$|github\.io$/.test(new URL(t.url).hostname)) ?? tabs[0] ?? null;
      return t ? t.id : null;
    },
    registerAlarm(_name, _period, cb) { alarmCb = cb; },
    async getTab(tabId) { const t = tabs.find((t) => t.id === tabId) ?? null; return t ? { id: t.id, url: t.url } : null; },
    // test helpers
    fireCompleted(e) { for (const cb of completedCbs) cb(e); },
    fireError(e) { for (const cb of errorCbs) cb(e); },
    fireAlarm() { if (alarmCb) alarmCb(); },
    lastUpdated,
    _tabs: tabs,
  };
}
```

(The existing test `'ChromeRuntime interface is satisfiable by a fake and forwards callbacks'` still passes — it only asserts `createRealChromeRuntime` is a function.)

Add a new test asserting `getTab` on the real module's export surface (the real impl binds chrome.tabs.get, not unit-testable, but the export must exist):
```js
test('createRealChromeRuntime exposes registerAlarm + getTab (rename from watchdog + new)', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    const rt = mod.createRealChromeRuntime();
    assert.equal(typeof rt.registerAlarm, 'function');
    assert.equal(typeof rt.getTab, 'function');
    assert.equal(typeof rt.findOwnerTab, 'function');
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Update the navMonitor test fake (one-line rename)**

Edit `browserext/tests/navMonitor.test.mjs` — in `fakeChrome()`, change `registerWatchdogAlarm() {},` to `registerAlarm() {},` (line ~38). navMonitor does not call it, but the fake stays aligned with the renamed interface.

- [ ] **Step 3: Run tests to verify they fail (rename not yet applied to chrome.ts)**

Run: `cd browserext && npm test -- --test-name-pattern='registerAlarm + getTab' 2>&1 | tail -15`
Expected: FAIL — `rt.registerAlarm` is `undefined` (chrome.ts still exports `registerWatchdogAlarm`).

- [ ] **Step 4: Modify chrome.ts — rename + add getTab**

Edit `browserext/src/chrome.ts`:

1. Update the `ChromeRuntime` interface — replace `registerWatchdogAlarm` with `registerAlarm` and add `getTab`:
```ts
export interface ChromeRuntime {
  onNavCompleted(cb: (e: NavCompletedEvent) => void): void;
  onNavError(cb: (e: NavErrorEvent) => void): void;
  updateTabUrl(tabId: number, url: string): Promise<void>;
  findOwnerTab(): Promise<number | null>;
  getTab(tabId: number): Promise<{ id: number; url?: string } | null>;
  registerAlarm(name: string, periodMinutes: number, cb: () => void): void;
}
```

2. In `createRealChromeRuntime`, add `getTab` (after `findOwnerTab`) and rename the alarm method:
```ts
    async getTab(tabId) {
      try {
        const t = await chrome.tabs.get(tabId);
        if (!t) return null;
        return { id: t.id ?? tabId, url: t.url };
      } catch {
        return null;   // tab gone (TypeError "No tab with id") → invalid bound tab
      }
    },
    registerAlarm(name, periodMinutes, cb) {
      if (registeredAlarms.has(name)) return;
      registeredAlarms.add(name);
      chrome.alarms.create(name, { periodInMinutes: periodMinutes });
      chrome.alarms.onAlarm.addListener((alarm) => {
        if (alarm.name === name) cb();
      });
    },
```

(Remove the old `registerWatchdogAlarm(...)` method entirely.)

- [ ] **Step 5: Run the chrome + navMonitor tests to verify they pass**

Run: `cd browserext && npm test -- --test-name-pattern='registerAlarm + getTab|forwards callbacks' 2>&1 | tail -15 && npm test -- --test-name-pattern='404|502|nav_error|current_job|verdict_sent|2xx|429|UNRELATED' 2>&1 | tail -8`
Expected: chrome tests PASS; navMonitor tests PASS (the fake rename keeps the fake interface-aligned; navMonitor itself is unchanged).

- [ ] **Step 6: Run the full suite + typecheck (watchdog.test still references registerWatchdogAlarm — it must still pass because watchdog.ts is not yet deleted and its fake is independent)**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; tests `pass 62` (53 + 3 api + 2 backoff + 6 reconcile + 4 heartbeat − 0 removed yet + 1 new chrome test = 62; watchdog.test still passes with its own fake) `fail 0`. If the count differs, check which suite failed before proceeding — do NOT proceed to Task 6 with a red suite.

> Note: `watchdog.ts` still imports `ChromeRuntime` and uses `registerWatchdogAlarm` only via the `chrome` dep passed to it (its own fake provides the method). The `ChromeRuntime` interface rename does NOT break `watchdog.ts` because `watchdog.ts` calls `chrome.registerWatchdogAlarm(...)` on the injected dep — and the watchdog test's fake still provides `registerWatchdogAlarm`. `watchdog.ts` will be deleted in Task 7. **Do not** update watchdog's fake — it is deleted alongside the module.

- [ ] **Step 7: Commit**

```bash
git add browserext/src/chrome.ts browserext/tests/chrome.test.mjs browserext/tests/navMonitor.test.mjs
git commit -m "feat(browserext): rename registerAlarm + add getTab for bound-tab rehydration"
```

---

## Task 6: Wire reconcile + backoff + heartbeat + tab-validity into the Controller tick

**Files:**
- Modify: `browserext/src/controller/controller.ts`
- Modify: `browserext/tests/controller/controller.test.mjs`

**Interfaces:**
- Consumes: `computeBackoffMs`/`nextRetryAt` (Task 2), `applyReconcile` (Task 3), `createHeartbeat` (Task 4), `ApiClient` (Task 1), `ChromeRuntime.getTab` (Task 5).
- Produces: `CrawlControllerDeps` gains optional `api?: ApiClient` and `chrome?: Pick<ChromeRuntime, 'getTab'>`; the `tick(now)` body (gated) now: (1) bound-tab-validity rehydration via `chrome.getTab`, (2) `/status` reconcile gated by `nextBackendRetryAt` with backoff on failure, (3) heartbeat every tick when bound, (4) `saveIfChanged(prev, state)` so no write when nothing changed. Also re-exports `RECONCILIATION_ALARM_NAME`/`RECONCILIATION_PERIOD_MINUTES` for Task 7. `bind` is unchanged (still gated, sets assigned). Missing `api`/`chrome` → the corresponding tick steps are skipped (so slice-1's no-api `tick` test stays a true no-op).

- [ ] **Step 1: Write the failing tests (extend the existing controller test file)**

Append to `browserext/tests/controller/controller.test.mjs` (keep the existing three slice-1 tests — they stay green because missing api/chrome makes tick a no-op):

```js
function fakeArea() {
  const store = new Map();
  return {
    async get(keys) {
      const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]);
      const obj = {}; for (const k of arr) if (store.has(k)) obj[k] = store.get(k); return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
}
// NOTE: the existing slice-1 tests already define fakeArea at the top of the file;
// if so, do NOT re-declare it — only add the helpers below and the new tests.

function fakeApi(statusResponse) {
  const calls = { getStatus: 0, sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async failJob() {}, async skipJob() {},
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}

function fakeChrome(getTabResult) {
  return {
    async getTab() { return getTabResult; },
  };
}

function job(id, url = `https://x.edu.cn/${id}`) {
  return {
    id, url, status: 'assigned',
    context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
    created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
  };
}

test('tick: /status returns a job → connected true, currentJob cached, phase assigned, backoff cleared, heartbeat sent', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/job-1' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    // bind first so heartbeat fires (ownerTabId needs boundTabId)
    await c.bind(42, 1000);
    await c.tick(5000);
    const s = await c.getState();
    assert.equal(s.connected, true);
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'assigned');
    assert.equal(s.backendFailureCount, 0);
    assert.equal(s.nextBackendRetryAt, null);
    assert.equal(api.calls.getStatus, 1);
    assert.equal(api.calls.sendHeartbeat.length, 1);
    assert.equal(api.calls.sendHeartbeat[0].owner_tab_id, 'dext-ext-42-1000');
    assert.equal(api.calls.sendHeartbeat[0].current_job_id, 'job-1');
    assert.equal(api.calls.sendHeartbeat[0].timestamp, 5000);
  } finally { await cleanup(); }
});

test('tick: /status fails (backend down) → connected false, backoff set, phase/page unchanged, NO navigation', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi(null);
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    const s = await c.getState();
    assert.equal(s.connected, false);
    assert.equal(s.backendFailureCount, 1);
    assert.equal(s.nextBackendRetryAt, 7000);  // 5000 + 2000 (count=1)
    assert.equal(s.phase, 'assigned', 'phase preserved (never navigate/refresh on backend down)');
    assert.equal(s.boundTabId, 42, 'binding preserved across backend failure');
    // heartbeat still fires (not gated by backoff) — bound + tick
    assert.equal(api.calls.sendHeartbeat.length, 1);
  } finally { await cleanup(); }
});

test('tick: second tick within backoff window skips /status (gated by nextBackendRetryAt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi(null);
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);     // sets nextBackendRetryAt=7000
    await c.tick(6000);     // 6000 < 7000 → skip /status
    assert.equal(api.calls.getStatus, 1, 'second tick within backoff did not call /status');
  } finally { await cleanup(); }
});

test('tick: after backoff expires, /status is called again and success resets backoff', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    // first call down, then up: use a statusResponse that flips
    const responses = [
      null,
      { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } },
    ];
    const api = {
      calls: { getStatus: 0 },
      async getStatus() { return responses[api.calls.getStatus++] ?? responses[responses.length - 1]; },
      async failJob() {}, async skipJob() {}, async sendHeartbeat() {},
    };
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);     // down → nextBackendRetryAt=7000, count=1
    await c.tick(7000);     // 7000 >= 7000 → /status up → reset
    const s = await c.getState();
    assert.equal(api.calls.getStatus, 2);
    assert.equal(s.connected, true);
    assert.equal(s.backendFailureCount, 0);
    assert.equal(s.nextBackendRetryAt, null);
  } finally { await cleanup(); }
});

test('tick: bound tab gone (getTab null) → unbind, keep currentJob as assigned, no fail/skip', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    // seed: bound + a cached job (assigned), then the tab disappears
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome(null);   // tab gone
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    // seed a cached job by one successful tick with a present tab, then make tab vanish
    await c.tick(2000);
    // now flip getTab to null and tick again
    const chr2 = fakeChrome(null);
    // rebuild controller over the SAME area so it rehydrates the bound+assigned state
    const c2 = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr2 });
    await c2.tick(5000);
    const s = await c2.getState();
    assert.equal(s.boundTabId, null, 'invalid bound tab → unbind');
    assert.equal(s.boundAt, null);
    assert.equal(s.currentJob.id, 'job-1', 'currentJob cache kept as assigned');
    assert.equal(s.phase, 'assigned', 'kept assigned; NOT auto fail/skip, NOT idle');
    assert.equal(api.calls.sendHeartbeat.filter(p => p.owner_tab_id === 'dext-ext-42-1000').length, 0,
      'no heartbeat with the now-invalid owner after unbind');
  } finally { await cleanup(); }
});

test('tick: no heartbeat when unbound (no api.sendHeartbeat calls)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome(null);
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.tick(5000);   // never bound
    assert.equal(api.calls.sendHeartbeat.length, 0);
    const s = await c.getState();
    // unbound + backend has a job → assigned (waiting for bind), currentJob cached
    assert.equal(s.phase, 'assigned');
    assert.equal(s.currentJob.id, 'job-1');
  } finally { await cleanup(); }
});

test('tick: persists only on change (saveIfChanged) — second identical tick writes nothing', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    let setCount = 0;
    const wrappedArea = {
      async get(keys) { return area.get(keys); },
      async set(obj) { setCount += 1; return area.set(obj); },
      async remove(keys) { return area.remove(keys); },
    };
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/job-1' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(wrappedArea), api, chrome: chr });
    await c.bind(42, 1000);   // bind writes once
    const afterBind = setCount;
    await c.tick(5000);       // changes state (connected, currentJob) → writes
    const afterFirstTick = setCount;
    assert.ok(afterFirstTick > afterBind, 'first tick changed state → wrote');
    await c.tick(7000);       // same job id, already connected, bound tab present → NO change → NO write
    assert.equal(setCount, afterFirstTick, 'second identical tick wrote nothing (saveIfChanged)');
  } finally { await cleanup(); }
});

test('reconciliation alarm constants are exported', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    assert.equal(mod.RECONCILIATION_ALARM_NAME, 'dext-reconcile');
    assert.equal(mod.RECONCILIATION_PERIOD_MINUTES, 1);
  } finally { await cleanup(); }
});
```

> If `fakeArea` is already declared at the top of the file from slice 1, do not re-declare it — remove the duplicate from the block above and rely on the existing one. The `job`/`fakeApi`/`fakeChrome` helpers are new.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd browserext && npm test -- --test-name-pattern='/status returns a job|backend down|within backoff|after backoff|bound tab gone|no heartbeat when unbound|persists only on change|alarm constants' 2>&1 | tail -25`
Expected: FAIL — the new tick behavior is not implemented (`api.calls.getStatus` stays 0; `RECONCILIATION_ALARM_NAME` undefined). The three existing slice-1 tests still PASS (they don't provide api/chrome → tick stays a no-op).

- [ ] **Step 3: Modify controller.ts**

Replace `browserext/src/controller/controller.ts` with:
```ts
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
import { computeBackoffMs, nextRetryAt } from './backoff.js';
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
        void computeBackoffMs;   // re-exported math is exercised in backoff.test; keep import live
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
```

- [ ] **Step 4: Run the controller tests to verify they pass**

Run: `cd browserext && npm test -- --test-name-pattern='bind sets boundTabId|tick with no currentJob|initial state is unbound|/status returns a job|backend down|within backoff|after backoff|bound tab gone|no heartbeat when unbound|persists only on change|alarm constants' 2>&1 | tail -25`
Expected: PASS — all three slice-1 tests + all eight new tests green.

- [ ] **Step 5: Typecheck + full suite**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; tests `pass 70` (62 + 8 new controller tests) `fail 0`. watchdog.test still passes (module not yet deleted).

> If `void computeBackoffMs;` triggers a lint complaint, remove the line — `computeBackoffMs` is imported and used by `nextRetryAt`'s presence is enough; but since `computeBackoffMs` is not directly called in controller.ts, prefer to drop it from the import to avoid an unused-import: change the import to `import { nextRetryAt } from './backoff.js';` and delete the `void computeBackoffMs;` line. Do this if typecheck/esbuild warns.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/controller.ts browserext/tests/controller/controller.test.mjs
git commit -m "feat(browserext): wire /status reconcile + backoff + heartbeat + tab-validity into tick"
```

---

## Task 7: Delete watchdog + rewire background (reconciliation alarm → controller.tick)

**Files:**
- Delete: `browserext/src/watchdog.ts`
- Delete: `browserext/tests/watchdog.test.mjs`
- Modify: `browserext/src/background.ts`
- Modify: `browserext/tests/background.test.mjs`

**Interfaces:**
- Consumes: `createCrawlController`, `RECONCILIATION_ALARM_NAME`/`RECONCILIATION_PERIOD_MINUTES` (Task 6), `ChromeRuntime.registerAlarm` (Task 5).
- Produces: `wireBackground` returns `{ navMonitor, controller }` (no `watchdog`); it constructs the controller with `api` + `chrome`, starts `navMonitor`, and registers the reconciliation alarm driving `controller.tick()`. No watchdog constructed/started.

- [ ] **Step 1: Update the background test to expect the new wiring (no watchdog; alarm → tick)**

Replace `browserext/tests/background.test.mjs` with:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('wireBackground constructs navMonitor + controller (no watchdog), starts navMonitor, wires reconciliation alarm → controller.tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  // wireBackground reads the global chrome.storage.local directly to build the
  // Controller's persistence area; provide a minimal in-memory fake for the test.
  const store = new Map();
  globalThis.chrome = globalThis.chrome ?? {};
  globalThis.chrome.storage = globalThis.chrome.storage ?? {};
  const prevLocal = globalThis.chrome.storage.local;
  globalThis.chrome.storage.local = {
    async get(keys) {
      const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]);
      const obj = {}; for (const k of arr) if (store.has(k)) obj[k] = store.get(k); return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
  try {
    let alarmReg = null;
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; },
      async getTab() { return null; },
      registerAlarm(name, period, cb) { alarmReg = { name, period, cb }; },
    };
    const fakeApi = { async getStatus() { return null; }, async failJob() {}, async skipJob() {}, async sendHeartbeat() {} };
    const fakeStorage = {
      async bumpCount() { return 0; }, async getCount() { return 0; },
      async markVerdictSent() {}, async wasVerdictSent() { return false; },
      async recordRedirect() {}, async shouldRedirect() { return true; }, async clear() {},
    };
    const { navMonitor, controller } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi, storage: fakeStorage });
    // watchdog is gone from the return shape
    assert.equal('watchdog' in { navMonitor, controller }, false, 'no watchdog in wireBackground return');
    assert.ok(navMonitor, 'navMonitor constructed');
    assert.ok(controller, 'controller constructed');
    assert.equal(typeof controller.tick, 'function');
    // reconciliation alarm registered with the controller's constants, driving tick
    assert.ok(alarmReg, 'reconciliation alarm registered');
    assert.equal(alarmReg.name, 'dext-reconcile');
    assert.equal(alarmReg.period, 1);
    // firing the alarm drives controller.tick (gate is ON in the harness); tick
    // with no bound tab + backend-down api is a safe no-op that must not throw.
    await assert.doesNotReject(async () => { await alarmReg.cb(); });
    // navMonitor.start was called (it registers webRequest listeners; fake no-ops)
    assert.equal(typeof navMonitor.handleCompleted, 'function');
  } finally {
    if (prevLocal === undefined) delete globalThis.chrome.storage.local;
    else globalThis.chrome.storage.local = prevLocal;
    await cleanup();
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='wireBackground constructs navMonitor' 2>&1 | tail -20`
Expected: FAIL — `wireBackground` still returns `{ navMonitor, watchdog, controller }` and does not register the alarm.

- [ ] **Step 3: Modify background.ts**

Replace `browserext/src/background.ts` with:
```ts
/** MV3 background service worker entry. Composition root only — no logic.
 *  Constructs the real chrome.* runtime, the fetch HTTP client, the chrome.storage
 *  storage, then wires navMonitor (Phase-1 thin adapter; refactored in slice 3)
 *  and the gated CrawlController, and registers the 1-minute reconciliation alarm
 *  that drives controller.tick() — REPLACING the Phase-1 watchdog (spec §2.6).
 *  The controller is gated OFF (EXCLUSIVE_CONTROL_ENABLED=false) in official
 *  slice 1–5 builds, so its tick is a runtime no-op; the alarm firing a no-op
 *  tick is harmless. Phase-1 navMonitor stays wired and working throughout. */

import { createRealChromeRuntime } from './chrome.js';
import type { ChromeRuntime } from './chrome.js';
import { createFetchApi } from './api.js';
import type { ApiClient } from './api.js';
import { createChromeStorage } from './storage.js';
import type { Storage } from './storage.js';
import { createNavMonitor } from './navMonitor.js';
import type { NavMonitor } from './navMonitor.js';
import { createCrawlController, createControllerStorage } from './controller/controller.js';
import type { CrawlController, StorageArea } from './controller/controller.js';
import {
  RECONCILIATION_ALARM_NAME,
  RECONCILIATION_PERIOD_MINUTES,
} from './controller/controller.js';

const API_BASE = 'http://127.0.0.1:21520/api';

export interface WireDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export function wireBackground(deps: WireDeps): {
  navMonitor: NavMonitor;
  controller: CrawlController;
} {
  const navMonitor = createNavMonitor(deps);
  // Controller rehydrates from chrome.storage.local across SW restarts. Gated
  // OFF in official slice 1–5 builds — tick/bind are no-ops. Slice 2 wires the
  // reconciliation alarm (below) to tick; the alarm firing a no-op tick is
  // harmless. api + chrome let the gated-ON tick do /status reconcile + heartbeat.
  const controller = createCrawlController({
    storage: createControllerStorage(chrome.storage.local as unknown as StorageArea),
    api: deps.api,
    chrome: deps.chrome,
  });
  navMonitor.start();
  // Reconciliation alarm (spec §2.6): 1-minute waker driving the same tick path.
  // Replaces the watchdog; never re-redirects the tab on stale heartbeat.
  deps.chrome.registerAlarm(RECONCILIATION_ALARM_NAME, RECONCILIATION_PERIOD_MINUTES, () => {
    void controller.tick();
  });
  return { navMonitor, controller };
}

// Self-invoke on SW startup with real implementations, but skip in node tests where
// chrome is undefined. The typeof check keeps both tsc and the test harness happy.
if (typeof chrome !== 'undefined' && chrome.storage?.local) {
  wireBackground({
    chrome: createRealChromeRuntime(),
    api: createFetchApi(API_BASE),
    storage: createChromeStorage(chrome.storage.local),
  });
}
```

- [ ] **Step 4: Delete watchdog.ts + its test**

Run:
```bash
cd browserext && git rm src/watchdog.ts tests/watchdog.test.mjs
```
Expected: both files removed from the index and working tree.

- [ ] **Step 5: Run the background test + full suite + typecheck**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -10`
Expected: typecheck exit 0; the watchdog tests are gone (file deleted); `wireBackground` test PASSES; the full suite is `pass 70` (62 + 8 controller − 8 watchdog tests deleted + 1 new background test − 1 old background test = 62; recompute by running — the key assertion is `fail 0`). If any test other than the deleted watchdog tests fails, STOP and fix before committing.

> Precise count math: baseline 53 + Task1 +3 = 56 + Task2 +2 = 58 + Task3 +6 = 64 + Task4 +4 = 68 + Task5 +1 new chrome test = 69 + Task6 +8 controller tests = 77 + Task7 +1 new background −1 old background −8 watchdog (deleted) = 69. The exact number is not load-bearing — `fail 0` is. Run it and confirm no failures.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/background.ts browserext/tests/background.test.mjs
git commit -m "feat(browserext): reconciliation alarm replaces watchdog; delete watchdog.ts + test"
```

(The `git rm` in Step 4 already staged the deletions; this commit captures both the rewiring and the deletion. If `git status` shows the deletions unstaged, re-run `git rm` or `git add` them before committing.)

---

## Task 8: Slice-2 acceptance — full suite green, gate off, Phase-1 intact, Python unaffected

**Files:**
- No new files; verification gate. Optionally update `browserext/README.md` if it documents the watchdog.

- [ ] **Step 1: Clean build + full test suite + typecheck**

Run: `cd browserext && rm -rf dist && npm install && npm run typecheck && npm run build && npm test 2>&1 | tail -12`
Expected:
- `npm install` succeeds.
- typecheck exit 0 (both configs).
- build emits `dist/background.js` + `dist/content.js`.
- all tests pass, `fail 0`. The watchdog tests are absent; navMonitor/status/api(chrome/storage) Phase-1 tests are green; the new backoff/reconcile/heartbeat/controller tests are green.

- [ ] **Step 2: Confirm the default build is still gated off (a true no-op)**

Run: `cd browserext && node -e "const fs=require('fs');const bg=fs.readFileSync('dist/background.js','utf8');console.log('if(true)return:', /if \(true\) return/.test(bg));console.log('if(false){:', /if \(false\) \{/.test(bg));const c=fs.readFileSync('dist/content.js','utf8');console.log('content if(false){:', /if \(false\) \{/.test(c));"`
Expected: `if(true)return: true` (controller early-returns when gate off), `if(false){: true` (controller tick body guarded by the gate), `content if(false){: true` (content body skipped). The reconciliation alarm fires `controller.tick()` which early-returns — a harmless no-op.

- [ ] **Step 3: Confirm Phase-1 navMonitor/status behavior is unchanged (regression)**

Run: `cd browserext && npm test 2>&1 | grep -E "tests|pass|fail"`
Expected: `fail 0`. Specifically the navMonitor (10 tests), status (3 tests), chrome, storage, api suites pass.

- [ ] **Step 4: Confirm watchdog is fully gone**

Run: `cd browserext && git ls-files | grep -i watchdog || echo "no watchdog files tracked"`
Expected: `no watchdog files tracked`. Also `grep -rn "watchdog\|Watchdog\|registerWatchdogAlarm" browserext/src browserext/tests` should return nothing (the only remaining occurrence would be in `manifest.json` description or README if present — check and clean those too).

Run: `cd browserext && grep -rni "watchdog\|registerWatchdogAlarm" src tests ../userscripts 2>/dev/null | grep -v node_modules || echo "clean"`
Expected: `clean` (no stale references). If the manifest description or README mentions watchdog, update the wording to "reconciliation alarm" and commit as a docs change.

- [ ] **Step 5: Confirm Python suite is unaffected**

Run: `uv run pytest -q` (from repo root)
Expected: PASS (slice 2 touches only `browserext/`; no Python changed). If any Python test fails, it is pre-existing and unrelated — note it but do not fix in this slice.

- [ ] **Step 6: Update memory with the slice-2 landing fact**

Update the existing `browserext-slice1-landed` memory (or add a `browserext-slice2-landed` memory) recording: slice 2 landed (gated off) — `/status` reconcile + non-RPC rehydration + backoff + heartbeat + reconciliation alarm; watchdog deleted; no claim yet (slice 3). Note the deterministic `ownerTabId = dext-ext-${boundTabId}-${boundAt}` decision and that heartbeat cadence is 1-min now (alarm-driven) until slice 4 wires the 2s content TICK. This orients future sessions without re-deriving from git.

- [ ] **Step 7: Final commit (if any docs/memory artifacts were staged)**

```bash
# Only if README/manifest wording changed:
git add browserext/README.md browserext/manifest.json
git commit -m "docs(browserext): note slice-2 reconcile/heartbeat/alarm landed (gate off), watchdog removed"
```

---

## Self-Review (completed by plan author)

**1. Spec coverage (slice-2 row of spec §6.2 + amend §8.2):**
- `GET /status` 对账 (gated by `nextBackendRetryAt`) → Task 6 tick step 2 + Task 3 `applyReconcile`. ✓
- Rehydration table (amend §6.3) — non-RPC rows: backend-released→idle (Task 3), new-job→discard-stale-nav+assigned (Task 3), invalid-bound-tab→unbind+keep-assigned (Task 6 step 1), same-job→keep-in-flight (Task 3). The acting/capturing/pending-RPC rows are slice 4 (RPC); navigating/claiming are slice 3 — explicitly out of scope, documented in Task 3. ✓
- Backoff → Task 2 + Task 6 (failure bumps count + sets `nextBackendRetryAt`; success resets; backoff gates `/status` not heartbeat). ✓
- Heartbeat → Task 4 (deterministic stable ownerTabId) + Task 1 (api.sendHeartbeat) + Task 6 (fires every tick when bound, not gated by backoff/paused). ✓
- Reconciliation alarm (replaces watchdog) → Task 6 (constants) + Task 7 (alarm → controller.tick). ✓
- **Delete watchdog.ts + test** → Task 7 Step 4. ✓
- 暂不 claim (`/jobs/next` is slice 3) → Task 3 sets `phase='assigned'` on new job but never claims; Task 6 tick has no `/jobs/next` call. ✓
- 门控关闭 → Task 8 Step 2 verifies gate-off no-op; every new tick step is after `if (!EXCLUSIVE_CONTROL_ENABLED) return;`. ✓
- Phase-1 navMonitor/status untouched → Task 5 only renames the alarm method + adds `getTab` (navMonitor unchanged); `status.ts` not modified this slice. ✓ (amend §8.1: status/navMonitor rewrites are slice 3.)

**amend §8.2 slice-2 row says:** "slice 3 owns the classifier rewrite + requestId/documentId join + two-level deadline + navMonitor thin-adapter rewrite." This plan does NOT touch those — correct, they are slice 3. The slice-2 row in §6.2 lists only reconcile/rehydration/backoff/heartbeat/alarm/watchdog-delete — all covered.

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Task 6 Step 5 has a conditional lint note (drop unused `computeBackoffMs` import if warned) with the exact replacement — a real instruction, not a placeholder. All code blocks contain complete code. The test-count math in Task 7 Step 5 is hedged with "the exact number is not load-bearing — `fail 0` is" — that's a verification instruction, not a placeholder.

**3. Type consistency:**
- `ApiClient.sendHeartbeat(payload: HeartbeatPayload)` — Task 1 defines `HeartbeatPayload` and the method; Task 4 `createHeartbeat(api: Pick<ApiClient, 'sendHeartbeat'>)` consumes it; Task 6 controller builds `heartbeat = createHeartbeat(deps.api)`. ✓
- `StatusPayload.current_job: FetchJob | null` — Task 1 widens; Task 3 `applyReconcile(state, currentJob: FetchJob | null, now)` consumes `FetchJob | null`; Task 6 passes `status.current_job`. ✓
- `ChromeRuntime.getTab(tabId): Promise<{id:number; url?:string} | null>` — Task 5 defines; Task 6 `deps.chrome?: Pick<ChromeRuntime, 'getTab'>` consumes; Task 7 `wireBackground` passes `deps.chrome` (full `ChromeRuntime`, structurally compatible). ✓
- `ChromeRuntime.registerAlarm` — Task 5 defines (renamed from `registerWatchdogAlarm`); Task 7 `deps.chrome.registerAlarm(...)` uses it. ✓
- `computeBackoffMs`/`nextRetryAt` — Task 2 defines; Task 6 imports `nextRetryAt` (and `computeBackoffMs` if not dropped per the lint note). ✓
- `applyReconcile(state, currentJob, now)` — Task 3 signature matches Task 6 usage. ✓
- `ownerTabIdFor`/`createHeartbeat`/`Heartbeat.send(state, now)` — Task 4 signatures match Task 6 usage (`heartbeat.send(s, now)`). ✓
- `RECONCILIATION_ALARM_NAME`/`RECONCILIATION_PERIOD_MINUTES` — Task 6 defines + exports; Task 7 imports + uses. ✓
- `ControllerState` shape — unchanged from slice 1 (amend §1); slice 2 does not add fields (the `ownerTabId` is *derived*, not stored). ✓
- `CrawlControllerDeps` — Task 6 adds optional `api`/`chrome`; Task 7 `wireBackground` passes `api: deps.api, chrome: deps.chrome`. ✓

**4. Phase-1 regression check:**
- `navMonitor.ts` — unchanged; its test fake renames `registerWatchdogAlarm`→`registerAlarm` (one line, fake-only, navMonitor never calls it). navMonitor tests stay green. ✓
- `status.ts` — untouched. ✓
- `api.ts` — widens `current_job` (existing assertions read `.id`/`.url`, still present on `FetchJob`) + adds `sendHeartbeat` (additive). Existing api tests green. ✓
- `chrome.ts` — renames one method + adds one; `chrome.test.mjs` fake updated. ✓
- `storage.ts` (Phase-1 per-job) — untouched. ✓
- `background.ts` — drops watchdog, adds alarm→tick; `background.test.mjs` rewritten to match. ✓
- `watchdog.ts` + test — deleted. ✓

**5. Gate-off invariant:** The official build (`EXCLUSIVE_CONTROL_ENABLED=false`) — controller `tick` hits `if (!EXCLUSIVE_CONTROL_ENABLED) return;` after `ensureLoaded`, before any `api.getStatus`/`chrome.getTab`/`heartbeat.send`. The reconciliation alarm still fires `controller.tick()` every minute, which early-returns. No marker, no heartbeat, no `/status`, no navigation. True no-op. Task 8 Step 2 verifies the folded `if (true) return` / `if (false) {` in `dist/`. ✓

No blocking issues found. Plan is internally consistent, scoped to slice 2, and preserves the gate-off + Phase-1-intact invariants.
