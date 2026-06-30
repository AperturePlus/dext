# Browserext exclusive control — Slice 5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land slice 5 of the Phase-2 exclusive-control design: the **panel UI** (inlined CSS in a Shadow DOM), the **`STATE_CHANGED` / `PanelState` sync** model (amend §7), **all-tab `REGISTER`/`TICK` transport receipts** carrying a sender-computed `PanelState`, the **non-preemptible bind/unbind UI** (`boundTabId === null` allows bind; an existing bound tab shows "已绑定其他标签" and refuses bind or implicit takeover — explicit `unbind` is the third legal unbind cause, amend §7), the **manual command surface** (`submit`/`skip`/`fail`/`override`/`open`/`set_auto`/`set_paused`/`unbind`/`decision`), the **inlined pending-decision** control (no `window.confirm`, amend §4.9), and the **complete claim→navigate→capture→complete integration test** with the gate enabled in the test build (slice-5 acceptance row, spec §6.2). This slice **also** closes the last unwired seam: the SW-side `chrome.runtime.onMessage` router that turns `CsToSw` messages into `Controller.tick()`/`deliver*`/command calls and turns `PanelState` changes into `STATE_CHANGED`/`TOAST` pushes to the bound tab — slices 1–4 exposed `deliver*` on the controller object but never registered a listener. The whole slice stays **gated OFF** in the official build (`EXCLUSIVE_CONTROL_ENABLED = false`); only the test harness enables it. No HTTP-contract change, no DB-schema change.

**Architecture:** Slice 5 adds four new modules and widens three existing ones. New: (1) `content/panel.ts` — a Shadow-DOM panel renderer that is dependency-injected over a small `PanelDom` surface (`attachShadow`, `createElement`, `addEventListener`) so node tests drive it without a live DOM; it renders the existing userscript controls (auto/pause/open/submit/skip/fail/override-url) **plus** the bind/unbind controls and the inlined pending-decision, and it converts DOM clicks into `PanelCommand` values via a pure `commandFromClick(target, formData)` decider. Panel CSS is inlined as a TS string constant (esbuild's `.css` text-loader is for *imported* `.css`; the panel keeps a string so it needs no loader change and stays unit-testable). (2) `content/panelState.ts` — a pure `formatError(e: ControllerError): string` mapper (amend §7: the content panel renders human-readable text, the SW never does) plus a pure `PanelState`-equality helper used to short-circuit re-renders. (3) `controller/messageRouter.ts` — the SW-side `chrome.runtime.onMessage` handler: validates `sender.id`/`sender.tab`/`sender.frameId`/`sender.documentId` per amend §4.1 + §4.7, routes `REGISTER`/`TICK` → reply `{ received: true, state }` (state computed for the *sender*, so a non-bound tab gets `bound:true, isBoundTab:false`), routes `PAGE_READY` → `deliverPageReady`, routes `CAPTURE_RESULT`/`ACTION_PREPARED`/`ACTION_RESULT` → the matching `deliver*`, and routes `COMMAND` → the controller command method. It also owns **broadcast**: after any state-mutating call, it sends `STATE_CHANGED` to the bound tab and the commanding sender (amend §7), and `TOAST` for user-visible outcomes. (4) `controller/panelState.ts` — the SW-side `buildPanelState(state, sender): PanelState` projector (computes `isBoundTab` from `sender.tab?.id === state.boundTabId`; carries `navigationAttempt`, `pendingDecision`; `lastError` is the raw `ControllerError | null`, NOT a string). Widened: `controller/controller.ts` gains the command methods (`submit`/`skip`/`fail`/`override`/`open`/`setPaused`/`unbind`/`resolveDecision`) under the existing mutex, plus a `pendingDecision` field on `ControllerState` populated by `/status` (reconcile already reads `current_job`; the decision is a *separate* `/decision` GET — added to `api.ts` as `getDecision()`), plus a `broadcastPanelState(sender?)` hook the messageRouter calls. Widened: `api.ts` gains `getDecision()` (GET `/decision`, nullable) and `resolveDecision(id, action)` (POST `/decision/{id}/resolve`). Widened: `content/index.ts` wires the panel into `bootstrapContent` (mount panel on allowed host, register a `STATE_CHANGED` listener that re-renders, send `REGISTER` + a 2s `TICK` interval). The CS still never calls the backend, never navigates, never holds authoritative job state.

**Task ordering rationale (each task leaves the full suite green):** Tasks 1–3 are pure (panelState-formatError, SW buildPanelState projector, panel commandFromClick + render) and depend only on `shared/rpc`/`shared/state` types — each green independently, no chrome/DOM coupling. Task 4 (api.getDecision/resolveDecision) is additive and isolated. Task 5 (Controller command methods + `pendingDecision` + broadcast hook) depends on Task 4 and widens the controller under its existing mutex. Task 6 (SW messageRouter) depends on Tasks 1–5 and is the composition seam that finally connects `chrome.runtime.onMessage` to everything slices 1–4 built. Task 7 (content panel wiring) depends on Tasks 1–3 and adds the live DOM mount + REGISTER/TICK. Task 8 is the build/gate test update (slice-5 acceptance: gate ON in the test build is already true via the harness; the build.test asserts the *official* default stays OFF — no change there, but a new test asserts the messageRouter is wired into `background.ts`). Task 9 is the end-to-end integration test (claim→navigate→capture→complete) driven through the messageRouter with fakes. This keeps "one green commit per step" true at every boundary.

**Tech Stack:** TypeScript 5.7, MV3 Chrome/Edge (≥110), esbuild, node:test, Shadow DOM (`element.attachShadow`), `chrome.runtime.onMessage`/`MessageSender` (`sender.id`/`sender.tab`/`sender.frameId`/`sender.documentId`), `chrome.tabs.sendMessage` for `STATE_CHANGED`/`TOAST` (already on `ChromeRuntime.sendMessage`), DOM lib (content config).

## Global Constraints

Copied verbatim from the spec/amend + slice-1/2/3/4 plans so every task implicitly inherits them:

- **Gate default false.** Slice 1–5 official builds default `EXCLUSIVE_CONTROL_ENABLED = false`; every Controller `tick`/`bind`/`deliver*`/command/dispatch body short-circuits on the gate (positive form `if (GATE) {...}`). Only unit tests (harness injects `true`) enable it. The slice-5 acceptance integration test runs under the harness's gate-ON bundle. (spec §6.1)
- **`/status.current_job` is the sole job-state truth.** `ControllerState.currentJob` is a client cache only; reconcile discards stale `navigation`/`pendingRpc` when the job id changes. The CS holds NO authoritative job state — `PanelState` is a **mirror only**, computed per-sender by the SW (amend §7). (spec §1.3, amend §7, §9)
- **Controller is the sole `chrome.tabs.update` caller.** The CS never calls `tabs.update` and never mutates `window.location`. `form.submit()` is the one controlled exception (slice 4, gated behind SW-persisted navigation intent) — slice 5 adds nothing new here. (spec §3.6)
- **`PanelState.lastError` is `ControllerError | null` (amend §7).** The SW projects the raw discriminated error; the **content panel** converts it to human-readable text via `formatError`. The SW never holds a human-readable error string in `PanelState`. (amend §7, spec §4.8)
- **All-tab `REGISTER`/`TICK` carry a sender-computed `PanelState` (amend §7).** A `MessageReceipt = { received: true; state?: PanelState }`. The SW computes `state` for the *sender*: a bound tab gets `isBoundTab:true`; any other tab gets `bound:true` (a controller is bound) but `isBoundTab:false`. This is how a non-bound tab learns the global binding state without a tab registry surviving SW restarts. `boundTabId === null` → `bound:false`, and any tab may bind. (amend §7)
- **Non-preemptible binding (amend §7, spec §1.2).** An existing bound tab blocks bind from other tabs: a `bind` command from a tab whose `sender.tab.id !== state.boundTabId` is refused (the panel shows "已绑定其他标签"). Switching owner requires an explicit `unbind` from the current bound tab first, then a `bind` from the target. `unbind` is the third legal unbind cause (after `tabs.onRemoved` and an invalid persisted `boundTabId`); unbind does NOT auto fail/skip the current job — it drops to `assigned` keeping the `currentJob` cache (spec §2.5 row "boundTabId 无效"). (amend §7, spec §1.2, §2.5)
- **`STATE_CHANGED` distribution (amend §7).** Sent immediately to the bound tab and to the just-commanding sender; other tabs see the update no later than the next 2s `TICK` (their `TICK` receipt carries the fresh `PanelState`). `STATE_CHANGED`/`TOAST` use `chrome.tabs.sendMessage(tabId, msg, { frameId: 0 })` (no `documentId` — these are side-effect-free, amend §4.1 last paragraph). (amend §7)
- **TICK is read-only for non-bound tabs (amend §7).** A `TICK` from the bound tab may trigger Controller reconciliation (it calls `controller.tick()`); a `TICK` from a non-bound tab only reads state and replies with a `PanelState` — it does NOT trigger claim/navigation/heartbeat. (amend §7)
- **Permission boundary per message (amend §4.7).** SW accepts only its own extension (`sender.id === this extension id`), only top-frame (`sender.frameId === 0`), only allowed-host `sender.tab` URLs (top frame). An **unbound tab may send ONLY `bind`**; all other `COMMAND` variants and all work-RPC results must come from the bound tab. Work-RPC results additionally validate `rpcId`/`jobId`/`tab.id`/`documentId` per amend §4.1 (already implemented in slices 3–4 `deliver*`; the messageRouter reuses those handlers unchanged). (amend §4.7, §4.1)
- **No `window.confirm` (amend §4.9).** The pending-decision control is inlined in the panel; its accept/resolve posts `COMMAND { kind:'decision', id, action }`. The CS technically *can* call `window.confirm` but the architecture forbids it — decisions route through the SW. (amend §4.9)
- **2s TICK does not unconditionally write storage** — `saveIfChanged(prev, state)` is reused; command/dispatch paths persist only on change. The 2s `TICK` from the CS is a wake/reconcile trigger, not a storage write. (spec §2.2)
- **HTTP contract is FIXED.** No new/changed endpoints, no DB schema change. Slice 5 surfaces endpoints that already exist and are already used by the userscript: `GET /decision` (`PendingDecision | 204`), `POST /decision/{id}/resolve` (body `{ action }`), `POST /jobs/{id}/override` (body `{ new_url }` → `FetchJob`). Late `/complete`/`/fail`/`/skip` idempotency is unchanged (CLAUDE.md bug trap). (CLAUDE.md, spec §1.3, amend §9)
- **One conventional commit per green step.** `feat(browserext)`/`test(browserext)`/`chore(browserext)`/`docs(browserext)`. (CLAUDE.md)
- **browserext tests run with `npm test` (node:test, no pytest).** Relative imports in `src/*.ts` carry `.js`. The harness bundles via esbuild and injects `EXCLUSIVE_CONTROL_ENABLED = true` so gated-ON paths are exercised. (CLAUDE.md)
- **`dist/` is gitignored.**
- **Baseline before starting: `cd browserext && npm test` reports `pass 112 fail 0` (or the current green count) and `npm run typecheck` exits 0 on branch `slice4`.**

---

## File Structure

New/modified files in this slice. Decomposition: the panel render + command decider are pure over an injected DOM surface (not the `document` global) so they are unit-testable in node with a fake DOM; the SW-side `buildPanelState` projector is pure over `ControllerState`+`sender`; the `formatError` mapper is pure over `ControllerError`. The messageRouter is stateful but dependency-injected (it takes the controller + a `chrome`-like `tabs.sendMessage` + the extension id) so node tests drive it with fakes. The Controller gains command methods under its existing mutex.

```
browserext/
  src/content/panelState.ts            # CREATE (Task 1): pure formatError(ControllerError):string + panelStateEqual(a,b) (amend §7)
  src/controller/panelState.ts        # CREATE (Task 2): pure buildPanelState(state, sender):PanelState (amend §7) — isBoundTab from sender.tab.id, navigationAttempt, pendingDecision, raw lastError
  src/content/panel.ts                # CREATE (Task 3): Shadow-DOM panel — renderPanel(host, state, handlers) + pure commandFromClick(target, formData):PanelCommand|null + inlined CSS string (amend §4.9)
  src/api.ts                          # MODIFY (Task 4): + getDecision():Promise<PendingDecision|null> + resolveDecision(id,action):Promise<void> (GET /decision, POST /decision/{id}/resolve)
  src/controller/controller.ts        # MODIFY (Task 5): + pendingDecision on ControllerState + command methods (submit/skip/fail/override/open/setPaused/unbind/resolveDecision) + broadcastPanelState(sender?) hook + getDecision wired into tick (slice-5 polling) — all under the existing mutex, all gated
  src/controller/messageRouter.ts     # CREATE (Task 6): SW chrome.runtime.onMessage router — sender validation, REGISTER/TICK→receipt+state, PAGE_READY/RESULT→deliver*, COMMAND→command method, STATE_CHANGED/TOAST broadcast (amend §4.1, §4.7, §7)
  src/content/index.ts                # MODIFY (Task 7): mount panel on allowed host, register STATE_CHANGED listener → renderPanel, send REGISTER + 2s TICK interval — still gated
  src/background.ts                   # MODIFY (Task 8): wire createMessageRouter(controller, chrome, api, extensionId).start() into wireBackground — gated
  src/shared/state.ts                 # MODIFY (Task 5): + pendingDecision: PendingDecision | null on ControllerState (initialControllerState sets null)
  src/shared/rpc.ts                   # NO CHANGE (PanelState/CsToSw/SwToSw already carry everything — slice 1/4 placed them)
  tests/content/panelState.test.mjs   # CREATE (Task 1)
  tests/controller/panelState.test.mjs # CREATE (Task 2)
  tests/content/panel.test.mjs        # CREATE (Task 3)
  tests/api.test.mjs                  # MODIFY (Task 4): + getDecision/resolveDecision
  tests/controller/controller.test.mjs # MODIFY (Task 5): + command-method tests + pendingDecision
  tests/controller/messageRouter.test.mjs # CREATE (Task 6)
  tests/content/index.test.mjs        # MODIFY (Task 7): + panel mount + REGISTER/TICK + STATE_CHANGED render
  tests/background.test.mjs           # MODIFY (Task 8): assert messageRouter wired
  tests/integration.test.mjs         # CREATE (Task 9): end-to-end claim→navigate→capture→complete through the messageRouter with fakes (gate ON)
```

Boundary notes:
- `content/panelState.ts` + `content/panel.ts` are pure (no `chrome`, no `document` global — DOM is an injected `PanelDom` struct). They live under `src/content/` (content tsconfig only) because they read DOM types; `controller/panelState.ts` lives under `src/controller/` (background tsconfig) because it reads `ControllerState`. Both import `shared/rpc` (types) which is in both configs.
- `controller/messageRouter.ts` is stateful but dependency-injected: it takes `{ controller, chrome, api, extensionId, now? }` where `chrome` is `Pick<ChromeRuntime, 'sendMessage'>` (reuses the slice-4 `sendMessage(tabId, msg, { frameId })`). It imports `shared/rpc` (types), `shared/state` (types), and `controller/panelState` (`buildPanelState`). It does NOT import DOM. Tests inject a fake `chrome` recording `sendMessage` calls.
- `controller/controller.ts` grows by the command methods + `broadcastPanelState` + `pendingDecision` handling. `CrawlControllerDeps.chrome` already has `sendMessage` (slice 4); the broadcast uses it. The `CrawlController` interface gains the command methods so the messageRouter can call them.
- `content/index.ts` already registers the work-RPC `onMessage` listener (slice 4) and emits `PAGE_READY`; Task 7 adds the panel mount + a `STATE_CHANGED` listener + `REGISTER`/`TICK` senders alongside, without disturbing the existing listener.
- `background.ts` already constructs the controller + navMonitor + alarm; Task 8 adds the messageRouter construction in the same `wireBackground` and self-invokes it. The `typeof chrome` guard is unchanged.

---

## Task 1: content/panelState.ts — pure error formatting + equality

**Files:**
- Create: `browserext/src/content/panelState.ts`
- Create: `browserext/tests/content/panelState.test.mjs`

**Interfaces:**
- Consumes: `ControllerError` (type) from `../shared/state.js`; `PanelState` (type) from `../shared/rpc.js`.
- Produces (pure):
  - `formatError(e: ControllerError | null): string` — maps the discriminated `ControllerError` to a human-readable Chinese string (amend §7: the content panel renders the text, the SW never does). Returns `''` for `null`.
  - `panelStateEqual(a: PanelState | undefined, b: PanelState | undefined): boolean` — shallow structural equality used to short-circuit re-renders when a `STATE_CHANGED` carries an unchanged state.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/panelState.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('formatError(null) → empty string', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try { assert.equal(mod.formatError(null), ''); } finally { await cleanup(); }
});

test('formatError(nav_error) → nav_error:<error>', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    assert.equal(mod.formatError({ kind: 'nav_error', error: 'ERR_CONNECTION_RESET' }), 'nav_error:ERR_CONNECTION_RESET');
  } finally { await cleanup(); }
});

test('formatError(content_unavailable) → 人工处理 message with missing tag (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    const s = mod.formatError({
      kind: 'content_unavailable', missing: 'page_ready', sourceDocumentId: 'DOC-1',
      since: 1000, recoveryAttempts: 3, nextRecoveryAt: null, recoveryExhausted: true,
    });
    assert.match(s, /page_ready/);
    assert.match(s, /人工/);
  } finally { await cleanup(); }
});

test('formatError(gateway_5xx / rate_limited / unexpected_status) → stable labels', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    assert.equal(mod.formatError({ kind: 'gateway_5xx' }), 'gateway_5xx');
    assert.equal(mod.formatError({ kind: 'rate_limited' }), 'rate_limited');
    assert.equal(mod.formatError({ kind: 'unexpected_status', statusCode: 403 }), 'unexpected_status:403');
  } finally { await cleanup(); }
});

test('panelStateEqual: undefined === undefined; differing phase is unequal', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    const a = { isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false, phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null };
    assert.equal(mod.panelStateEqual(undefined, undefined), true);
    assert.equal(mod.panelStateEqual(a, { ...a, phase: 'navigating' }), false);
    assert.equal(mod.panelStateEqual(a, a), true);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="formatError|panelStateEqual"`
Expected: FAIL with "Cannot find module '.../panelState.ts'" / `mod.formatError is not a function`.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/panelState.ts`:
```ts
/** Pure PanelState helpers for the content panel (amend §7). The SW projects
 *  the raw ControllerError; THIS module turns it into human-readable text so the
 *  SW never holds a localized string in PanelState. Also a shallow equality helper
 *  to skip no-op re-renders on identical STATE_CHANGED payloads. No chrome, no DOM. */

import type { ControllerError } from '../shared/state.js';
import type { PanelState } from '../shared/rpc.js';

const CONTENT_UNAVAILABLE_LABELS: Record<string, string> = {
  page_ready: 'page_ready',
  capture_result: 'capture_result',
  action_prepare: 'action_prepare',
  action_result: 'action_result',
  http_outcome: 'http_outcome',
};

export function formatError(e: ControllerError | null): string {
  if (e === null) return '';
  switch (e.kind) {
    case 'nav_error': return `nav_error:${e.error}`;
    case 'gateway_5xx': return 'gateway_5xx';
    case 'rate_limited': return 'rate_limited';
    case 'unexpected_status': return `unexpected_status:${e.statusCode}`;
    case 'content_unavailable':
      return `content_unavailable:${CONTENT_UNAVAILABLE_LABELS[e.missing] ?? e.missing}（需人工处理）`;
  }
}

/** Shallow structural equality over the PanelState fields the panel renders.
 *  Used to skip re-render when a STATE_CHANGED carries an unchanged state. */
export function panelStateEqual(a: PanelState | undefined, b: PanelState | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.isBoundTab === b.isBoundTab &&
    a.bound === b.bound &&
    a.connected === b.connected &&
    a.autoMode === b.autoMode &&
    a.paused === b.paused &&
    a.phase === b.phase &&
    a.navigationAttempt === b.navigationAttempt &&
    a.currentJob?.id === b.currentJob?.id &&
    a.lastError?.kind === b.lastError?.kind &&
    a.pendingDecision?.id === b.pendingDecision?.id
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="formatError|panelStateEqual"`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/content/panelState.ts browserext/tests/content/panelState.test.mjs
git commit -m "feat(browserext): slice 5 panelState — pure formatError + equality (amend §7)"
```

---

## Task 2: controller/panelState.ts — pure SW-side PanelState projector

**Files:**
- Create: `browserext/src/controller/panelState.ts`
- Create: `browserext/tests/controller/panelState.test.mjs`

**Interfaces:**
- Consumes: `ControllerState` (type) from `../shared/state.js`; `PanelState` (type) from `../shared/rpc.js`.
- Produces (pure):
  - `PanelSender = { tabId: number | null }` — the sender identity the projector keys `isBoundTab` off.
  - `buildPanelState(state: ControllerState, sender: PanelSender): PanelState` — projects the SW state into a `PanelState` for the *sender*: `isBoundTab = sender.tabId !== null && sender.tabId === state.boundTabId`; `bound = state.boundTabId !== null`; `connected`/`autoMode`/`paused`/`phase`/`currentJob` copied; `navigationAttempt = state.navigation?.attempt ?? 0`; `lastError = state.lastError` (raw `ControllerError | null`, NOT a string — amend §7); `pendingDecision = state.pendingDecision`.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/panelState.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function state(o = {}) {
  return {
    boundTabId: 42, boundAt: 1000, connected: true, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 1000, navigation: null, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null, pendingDecision: null, ...o,
  };
}

test('buildPanelState: bound tab sender → isBoundTab true, full state', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state({ phase: 'navigating', navigation: { attempt: 2 } }), { tabId: 42 });
    assert.equal(ps.isBoundTab, true);
    assert.equal(ps.bound, true);
    assert.equal(ps.phase, 'navigating');
    assert.equal(ps.navigationAttempt, 2);
  } finally { await cleanup(); }
});

test('buildPanelState: non-bound tab sender → isBoundTab false, bound still true (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state(), { tabId: 99 });
    assert.equal(ps.isBoundTab, false);
    assert.equal(ps.bound, true);
  } finally { await cleanup(); }
});

test('buildPanelState: null tabId sender (no tab) → isBoundTab false', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state(), { tabId: null });
    assert.equal(ps.isBoundTab, false);
  } finally { await cleanup(); }
});

test('buildPanelState: unbound controller → bound false (any tab may bind)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state({ boundTabId: null, boundAt: null }), { tabId: 99 });
    assert.equal(ps.bound, false);
    assert.equal(ps.isBoundTab, false);
  } finally { await cleanup(); }
});

test('buildPanelState: lastError is the raw ControllerError, not a string (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const err = { kind: 'rate_limited' };
    const ps = mod.buildPanelState(state({ lastError: err }), { tabId: 42 });
    assert.deepEqual(ps.lastError, err);
  } finally { await cleanup(); }
});

test('buildPanelState: pendingDecision carried through', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const dec = { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 3, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' };
    const ps = mod.buildPanelState(state({ pendingDecision: dec }), { tabId: 42 });
    assert.equal(ps.pendingDecision?.id, 'dec-1');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="buildPanelState"`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/panelState.ts`:
```ts
/** Pure SW-side PanelState projector (amend §7). Computes the PanelState for a
 *  given sender so REGISTER/TICK receipts and STATE_CHANGED pushes carry a
 *  sender-correct view: isBoundTab is true only for the bound tab; `bound`
 *  reflects whether ANY tab is bound (so a non-bound tab knows it cannot take
 *  over). lastError is the RAW ControllerError — the content panel (panelState.ts
 *  /formatError) renders the human text; the SW never localizes. No chrome, no DOM. */

import type { ControllerState } from '../shared/state.js';
import type { PanelState } from '../shared/rpc.js';

export interface PanelSender {
  tabId: number | null;
}

export function buildPanelState(state: ControllerState, sender: PanelSender): PanelState {
  return {
    isBoundTab: sender.tabId !== null && sender.tabId === state.boundTabId,
    bound: state.boundTabId !== null,
    connected: state.connected,
    autoMode: state.autoMode,
    paused: state.paused,
    phase: state.phase,
    currentJob: state.currentJob,
    navigationAttempt: state.navigation?.attempt ?? 0,
    lastError: state.lastError,
    pendingDecision: state.pendingDecision,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="buildPanelState"`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/controller/panelState.ts browserext/tests/controller/panelState.test.mjs
git commit -m "feat(browserext): slice 5 SW panelState projector (amend §7)"
```

---

## Task 3: content/panel.ts — Shadow-DOM panel renderer + command decider

**Files:**
- Create: `browserext/src/content/panel.ts`
- Create: `browserext/tests/content/panel.test.mjs`

**Interfaces:**
- Consumes: `PanelState`, `PanelCommand` (types) from `../shared/rpc.js`; `formatError` from `./panelState.js`.
- Produces:
  - `PanelDom` — the injected DOM surface (`attachShadow`, `createElement`, `addEventListener`, `getElementById`, `querySelectorAll`) so tests run without a real `document`.
  - `PANEL_CSS: string` — the inlined CSS constant (kept as a TS string; no esbuild loader change needed).
  - `mountPanel(dom: PanelDom): { render(state: PanelState): void }` — attaches a Shadow DOM host once, returns a `render(state)` that (re)renders the controls from a `PanelState`. Idempotent: a second `mountPanel` on the same host reuses the shadow root.
  - `commandFromClick(target: { dataset: Record<string,string> } | null, formData: (id: string) => string | null): PanelCommand | null` — pure decider mapping a clicked element's `data-cmd`/`data-*` attributes + form values to a `PanelCommand`. Returns `null` for non-command clicks. This is split out so the click handler is a thin wrapper and the mapping is unit-testable.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/panel.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// Minimal fake DOM good enough for mountPanel + commandFromClick.
function fakeDom() {
  const el = (id) => ({
    id, dataset: {}, style: {}, textContent: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, appendChild() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, setAttribute() {}, getAttribute() { return null; },
    remove() {}, cloneNode() { return { outerHTML: '', querySelectorAll() { return []; } }; },
  });
  const host = { ...el('dext-panel-host'), attachShadow() { return shadowRoot; } };
  const shadowRoot = {
    innerHTML: '',
    querySelector() { return null; }, querySelectorAll() { return []; },
    getElementById(id) { return els[id] ?? null; }, addEventListener() {},
  };
  const els = {};
  const document = {
    createElement: (tag) => { const e = el(tag); return e; },
    getElementById: (id) => els[id] ?? null,
    body: { appendChild: (n) => { if (n && n.id) els[n.id] = n; } },
  };
  return { host, shadowRoot, document, els, register: (id) => { els[id] = el(id); return els[id]; } };
}

test('commandFromClick: data-cmd=bind → bind command (unbound only allowed op, amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const cmd = mod.commandFromClick({ dataset: { cmd: 'bind' } }, () => null);
    assert.deepEqual(cmd, { kind: 'bind' });
  } finally { await cleanup(); }
});

test('commandFromClick: data-cmd=override with override-url input value', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const cmd = mod.commandFromClick({ dataset: { cmd: 'override' } }, (id) => id === 'dext-override-url' ? 'https://x.edu.cn/new' : null);
    assert.deepEqual(cmd, { kind: 'override', url: 'https://x.edu.cn/new' });
  } finally { await cleanup(); }
});

test('commandFromClick: data-cmd=decision carries id+action', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const cmd = mod.commandFromClick({ dataset: { cmd: 'decision', id: 'dec-1', action: 'skip' } }, () => null);
    assert.deepEqual(cmd, { kind: 'decision', id: 'dec-1', action: 'skip' });
  } finally { await cleanup(); }
});

test('commandFromClick: non-command click → null', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.equal(mod.commandFromClick({ dataset: {} }, () => null), null);
    assert.equal(mod.commandFromClick(null, () => null), null);
  } finally { await cleanup(); }
});

test('commandFromClick: set_auto/set_paused carry value from dataset', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.deepEqual(mod.commandFromClick({ dataset: { cmd: 'set_auto', value: 'true' } }, () => null), { kind: 'set_auto', value: true });
    assert.deepEqual(mod.commandFromClick({ dataset: { cmd: 'set_paused', value: 'false' } }, () => null), { kind: 'set_paused', value: false });
  } finally { await cleanup(); }
});

test('mountPanel: render shows bind button when unbound (isBoundTab false, bound false)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    const unbound = { isBoundTab: false, bound: false, connected: true, autoMode: false, paused: false, phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null };
    panel.render(unbound);
    // The shadow root receives an innerHTML string containing a bind control.
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="bind"/);
  } finally { await cleanup(); }
});

test('mountPanel: render shows controls + error text when bound (amend §7 formatError)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: true, paused: false,
      phase: 'capturing', currentJob: { id: 'job-1', url: 'https://x.edu.cn/j', status: 'assigned', context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] }, created_at: 't', timeout_seconds: 60, action: null, identity_url: null },
      navigationAttempt: 1, lastError: { kind: 'rate_limited' }, pendingDecision: null,
    });
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="unbind"/);
    assert.match(dom.shadowRoot.innerHTML, /rate_limited/);
  } finally { await cleanup(); }
});

test('mountPanel: render inlines pending-decision controls (no window.confirm, amend §4.9)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false,
      phase: 'landed', currentJob: null, navigationAttempt: 0, lastError: null,
      pendingDecision: { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 3, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' },
    });
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="decision"/);
    assert.match(dom.shadowRoot.innerHTML, /dec-1/);
    // window.confirm is forbidden (amend §4.9) — the panel must not reference it.
    assert.doesNotMatch(mod.PANEL_CSS + dom.shadowRoot.innerHTML, /window\.confirm/);
  } finally { await cleanup(); }
});

test('mountPanel is idempotent — second mount reuses the same shadow root', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    mod.mountPanel(dom);
    const calls = dom.host.attachShadowCalls ?? 0;
    mod.mountPanel(dom);
    // attachShadow called at most once across both mounts.
    assert.ok((dom.host.attachShadowCalls ?? 0) <= 1);
    void calls;
  } finally { await cleanup(); }
});
```

(Note: the fake DOM above must record `attachShadow` calls — adjust `fakeDom()` so `host.attachShadow` increments `host.attachShadowCalls`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="commandFromClick|mountPanel"`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/panel.ts`:
```ts
/** Content-script control panel (spec §4.9, amend §7). Mounted in a Shadow DOM
 *  so its CSS never collides with the host page. Renders the existing userscript
 *  controls (auto/pause/open/submit/skip/fail/override-url) PLUS bind/unbind and
 *  the inlined pending-decision (NO window.confirm — amend §4.9). DOM is injected
 *  as a PanelDom surface so node tests drive it without a real document.
 *
 *  Click handling is split: mountPanel wires ONE click listener on the shadow root
 *  that reads the clicked element's data-cmd/data-* and calls commandFromClick,
 *  then forwards the PanelCommand to the onCommand callback. commandFromClick is
 *  pure and unit-tested directly. */

import type { PanelCommand, PanelState } from '../shared/rpc.js';
import { formatError } from './panelState.js';

export interface PanelDom {
  host: {
    attachShadow(init: { mode: 'open' }): PanelShadowRoot;
    attachShadowCalls?: number;
  };
  document?: unknown;
}

export interface PanelShadowRoot {
  innerHTML: string;
  querySelector(sel: string): { dataset: Record<string, string> } | null;
  querySelectorAll(sel: string): Array<{ dataset: Record<string, string> }>;
  addEventListener(type: 'click', cb: (e: { target: unknown }) => void): void;
}

/** Inlined CSS — a TS string so no esbuild loader change is needed and the
 *  panel stays unit-testable. Spec §5.4 mentioned the .css text-loader for
 *  imported CSS; the panel deliberately keeps its CSS inline. */
export const PANEL_CSS = `
#dext-panel { font: 13px/1.4 system-ui, sans-serif; padding: 8px; }
.dext-row { margin: 4px 0; }
.dext-btn { margin-right: 4px; cursor: pointer; }
.dext-err { color: #b00; }
.dext-muted { color: #666; }
`;

export interface MountedPanel {
  render(state: PanelState): void;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

/** Pure: map a clicked element's dataset + form values to a PanelCommand, or null. */
export function commandFromClick(
  target: { dataset: Record<string, string> } | null,
  formData: (id: string) => string | null,
): PanelCommand | null {
  if (!target) return null;
  const d = target.dataset;
  switch (d.cmd) {
    case 'bind': return { kind: 'bind' };
    case 'unbind': return { kind: 'unbind' };
    case 'open': return { kind: 'open' };
    case 'submit': return { kind: 'submit' };
    case 'skip': return { kind: 'skip', reason: d.reason };
    case 'fail': return { kind: 'fail', message: undefined };
    case 'set_auto': return { kind: 'set_auto', value: d.value === 'true' };
    case 'set_paused': return { kind: 'set_paused', value: d.value === 'true' };
    case 'override': {
      const url = formData('dext-override-url');
      if (!url) return null;
      return { kind: 'override', url };
    }
    case 'decision': {
      if (!d.id || !d.action) return null;
      return { kind: 'decision', id: d.id, action: d.action };
    }
    default: return null;
  }
}

export function mountPanel(dom: PanelDom, onCommand?: (cmd: PanelCommand) => void): MountedPanel {
  let shadow: PanelShadowRoot | null = null;
  let lastRendered: PanelState | undefined;

  function ensureShadow(): PanelShadowRoot {
    if (shadow) return shadow;
    const hostAny = dom.host as PanelDom['host'] & { attachShadowCalls?: number };
    hostAny.attachShadowCalls = (hostAny.attachShadowCalls ?? 0) + 1;
    shadow = dom.host.attachShadow({ mode: 'open' });
    if (onCommand) {
      shadow.addEventListener('click', (e) => {
        const t = e.target as { dataset?: Record<string, string> } | null;
        const cmd = commandFromClick((t && t.dataset) ? t : null, () => null);
        if (cmd) onCommand(cmd);
      });
    }
    return shadow;
  }

  function render(state: PanelState): void {
    const sh = ensureShadow();
    const errText = formatError(state.lastError);
    const parts: string[] = [];
    parts.push(`<style>${PANEL_CSS}</style>`);
    parts.push(`<div id="dext-panel">`);
    if (!state.bound) {
      parts.push(`<div class="dext-row"><button class="dext-btn" data-cmd="bind">绑定并开始</button></div>`);
    } else if (!state.isBoundTab) {
      parts.push(`<div class="dext-row dext-muted">已绑定其他标签</div>`);
    } else {
      parts.push(`<div class="dext-row">phase: ${escapeHtml(state.phase)} · attempt ${state.navigationAttempt}</div>`);
      parts.push(`<div class="dext-row">connected: ${state.connected ? 'ok' : 'down'}</div>`);
      parts.push(`<div class="dext-row">auto: <button class="dext-btn" data-cmd="set_auto" data-value="${state.autoMode ? 'false' : 'true'}">${state.autoMode ? '关闭' : '开启'}</button> · paused: <button class="dext-btn" data-cmd="set_paused" data-value="${state.paused ? 'false' : 'true'}">${state.paused ? '继续' : '暂停'}</button></div>`);
      parts.push(`<div class="dext-row">`);
      parts.push(`<button class="dext-btn" data-cmd="open">打开</button>`);
      parts.push(`<button class="dext-btn" data-cmd="submit">提交</button>`);
      parts.push(`<button class="dext-btn" data-cmd="skip">跳过</button>`);
      parts.push(`<button class="dext-btn" data-cmd="fail">失败</button>`);
      parts.push(`</div>`);
      parts.push(`<div class="dext-row"><input id="dext-override-url" placeholder="override url"/><button class="dext-btn" data-cmd="override">覆盖</button></div>`);
      parts.push(`<div class="dext-row"><button class="dext-btn" data-cmd="unbind">解除绑定</button></div>`);
      if (errText) parts.push(`<div class="dext-row dext-err">${escapeHtml(errText)}</div>`);
      const dec = state.pendingDecision;
      if (dec) {
        parts.push(`<div class="dext-row" id="dext-decision">`);
        parts.push(`<div>待处理决策: ${escapeHtml(dec.id)} (${escapeHtml(dec.suggested_action)})</div>`);
        parts.push(`<button class="dext-btn" data-cmd="decision" data-id="${escapeHtml(dec.id)}" data-action="accept">接受</button>`);
        parts.push(`<button class="dext-btn" data-cmd="decision" data-id="${escapeHtml(dec.id)}" data-action="skip">拒绝</button>`);
        parts.push(`</div>`);
      }
    }
    parts.push(`</div>`);
    sh.innerHTML = parts.join('');
    lastRendered = state;
  }

  return { render };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="commandFromClick|mountPanel"`
Expected: PASS (8 tests). If the idempotency test needs `attachShadowCalls` recorded, ensure `fakeDom()`'s `attachShadow` increments a counter on `host`.

- [ ] **Step 5: Commit**

```bash
git add browserext/src/content/panel.ts browserext/tests/content/panel.test.mjs
git commit -m "feat(browserext): slice 5 Shadow-DOM panel + command decider (amend §4.9, §7)"
```

---

## Task 4: api.ts — getDecision / resolveDecision endpoints

**Files:**
- Modify: `browserext/src/api.ts`
- Modify: `browserext/tests/api.test.mjs`

**Interfaces:**
- Consumes: `PendingDecision` (type) from `./shared/types.js` (already imported).
- Produces (on `ApiClient`):
  - `getDecision(): Promise<PendingDecision | null>` — `GET /decision`; `204`/error → `null` (no decision pending).
  - `resolveDecision(id: string, action: string): Promise<void>` — `POST /decision/{id}/resolve` body `{ action }`; best-effort swallow (a late resolve against a backend-released decision is idempotent/no-op).

- [ ] **Step 1: Write the failing test**

Append to `browserext/tests/api.test.mjs` (read it first to match its existing fake-fetch style):
```js
test('getDecision: 200 → PendingDecision; 204 → null', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let status = 204, body = null;
    const fetchFn = async () => ({ status, ok: status < 400, json: async () => body });
    const api = mod.createFetchApi('http://x/api', fetchFn);
    assert.equal(await api.getDecision(), null);
    status = 200; body = { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 1, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' };
    const dec = await api.getDecision();
    assert.equal(dec?.id, 'dec-1');
  } finally { await cleanup(); }
});

test('getDecision: network error → null (swallow)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('net'); };
    const api = mod.createFetchApi('http://x/api', fetchFn);
    assert.equal(await api.getDecision(), null);
  } finally { await cleanup(); }
});

test('resolveDecision: POSTs /decision/{id}/resolve with {action}; swallows errors', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const calls = [];
    const fetchFn = async (url, init) => { calls.push({ url, init }); return { ok: true }; };
    const api = mod.createFetchApi('http://x/api', fetchFn);
    await api.resolveDecision('dec-1', 'accept');
    assert.equal(calls[0].url, 'http://x/api/decision/dec-1/resolve');
    assert.equal(calls[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].init.body), { action: 'accept' });
    // error swallow
    const errApi = mod.createFetchApi('http://x/api', async () => { throw new Error('net'); });
    await errApi.resolveDecision('dec-1', 'accept');   // does not throw
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="getDecision|resolveDecision"`
Expected: FAIL — `api.getDecision is not a function`.

- [ ] **Step 3: Write minimal implementation**

In `browserext/src/api.ts`, add `getDecision`/`resolveDecision` to the `ApiClient` interface and the returned object. Add `PendingDecision` to the type import if not already present:
```ts
import type { FetchJob, PaginationState, PendingDecision } from './shared/types.js';
```
Add to the `ApiClient` interface:
```ts
  getDecision(): Promise<PendingDecision | null>;
  resolveDecision(id: string, action: string): Promise<void>;
```
Add the implementations inside `createFetchApi` (before `return`):
```ts
  async function getDecision(): Promise<PendingDecision | null> {
    try {
      const res = await fetch(`${base}/decision`, { method: 'GET' });
      if (res.status === 204) return null;
      if (!res.ok) return null;
      return (await res.json()) as PendingDecision;
    } catch {
      return null;
    }
  }

  async function resolveDecision(id: string, action: string): Promise<void> {
    try {
      await fetch(`${base}/decision/${encodeURIComponent(id)}/resolve`, {
        method: 'POST', headers,
        body: JSON.stringify({ action }),
      });
    } catch {
      // late resolve against a backend-released decision is a no-op; swallow.
    }
  }
```
Add to the returned object literal:
```ts
  return { getStatus, claimNextJob, completeJob, failJob, skipJob, sendHeartbeat, getDecision, resolveDecision };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="getDecision|resolveDecision"`
Expected: PASS (3 tests). Then run the full api suite: `cd browserext && npm test -- --test-name-pattern="api"` or `node --test tests/api.test.mjs` to confirm no regression.

- [ ] **Step 5: Commit**

```bash
git add browserext/src/api.ts browserext/tests/api.test.mjs
git commit -m "feat(browserext): slice 5 api getDecision/resolveDecision (GET /decision, POST /decision/{id}/resolve)"
```

---

## Task 5: Controller — pendingDecision + command methods + broadcast hook

**Files:**
- Modify: `browserext/src/shared/state.ts`
- Modify: `browserext/src/controller/controller.ts`
- Modify: `browserext/tests/controller/controller.test.mjs`

**Interfaces:**
- Consumes: `ApiClient.getDecision`/`resolveDecision` (Task 4); `PanelCommand` (type); `buildPanelState` (Task 2).
- Produces (on `CrawlController` + `ControllerState`):
  - `ControllerState.pendingDecision: PendingDecision | null` (added to `initialControllerState` as `null`).
  - Command methods (all under the existing mutex, all gated `if (!EXCLUSIVE_CONTROL_ENABLED) return;`):
    - `setPaused(paused: boolean, now?: number): Promise<void>`
    - `unbind(now?: number): Promise<void>` — sets `boundTabId:null`/`boundAt:null`, drops to `assigned` keeping `currentJob` cache (spec §2.5; does NOT fail/skip).
    - `manualComplete(now?: number): Promise<void>` — `submit` command: if `phase==='landed'` (or `capturing` with a stale rpc), dispatch a fresh CAPTURE (re-use `dispatchCapture`); else no-op. Sets `phase:'submitting'` then dispatch.
    - `manualSkip(reason: string | undefined, now?: number): Promise<void>` — skip the cached job (`POST /jobs/{id}/skip`), clear `navigation`/`pendingRpc`, go `assigned` (job may still be current) or `idle`.
    - `manualFail(message: string | undefined, now?: number): Promise<void>` — `POST /jobs/{id}/fail`, clear, go `error`.
    - `overrideUrl(url: string, now?: number): Promise<void>` — `POST /jobs/{id}/override` (returns a refreshed `FetchJob`); cache it, clear `navigation`/`pendingRpc`/`lastError`, go `assigned` so the next tick navigates the new URL. (If the controller is unbound, no-op — navigation needs a bound tab.)
    - `resolveDecision(id: string, action: string): Promise<void>` — `POST /decision/{id}/resolve` (Task 4), then clear `pendingDecision` (a subsequent tick re-polls `/decision`).
  - `broadcastPanelState(sender?: { tabId: number | null }, reason?: string): Promise<void>` — computes the `PanelState`(s) and pushes `STATE_CHANGED` to the bound tab and (if different) the `sender` tab via `chrome.sendMessage(tabId, { op:'STATE_CHANGED', state }, { frameId: 0 })`. Exported so the messageRouter can call it after every state-mutating path. (The actual fan-out is simple: send to `boundTabId` always; if `sender.tabId` is set and differs from `boundTabId`, also send there.)
  - The `tick` polls `/decision` when bound + connected (gated by backoff like `/status`): on a non-null decision, set `state.pendingDecision` (only persist on change). On null, clear it.

- [ ] **Step 1: Write the failing test**

Append to `browserext/tests/controller/controller.test.mjs` (read it first; reuse the existing `fakeArea`/`fakeApi`/`fakeChrome`/`job`/`landedController` helpers):
```js
test('unbind: drops boundTabId, keeps currentJob cache as assigned (spec §2.5, no fail/skip)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api });
    await c.bind(42, 1000);
    await c.tick(2000);   // picks up job-1 → assigned
    await c.setAutoMode(true, 3000);
    await c.tick(4000);   // navigates
    await c.unbind(5000);
    const s = await c.getState();
    assert.equal(s.boundTabId, null);
    assert.equal(s.boundAt, null);
    assert.ok(s.currentJob, 'currentJob cache retained');
    assert.deepEqual(api.calls.skip, [], 'unbind does NOT skip');
    assert.deepEqual(api.calls.fail, [], 'unbind does NOT fail');
  } finally { await cleanup(); }
});
```
(Adjust `fakeApi` to expose `calls.skip`/`calls.fail` arrays if not already present — the existing helper records `getStatus`/`sendHeartbeat`; widen it to record `skipJob`/`failJob`/`completeJob`/`overrideJobUrl`/`getDecision`/`resolveDecision`.)

Add tests for `setPaused`, `manualSkip` (records `skipJob`), `manualFail` (records `failJob`), `overrideUrl` (records `overrideJobUrl` + caches refreshed job + phase assigned), `resolveDecision` (records `resolveDecision` + clears `pendingDecision`), and `broadcastPanelState` (records `sendMessage` with `op:'STATE_CHANGED'` to the bound tab). Each follows the same `importTsModule` + fake-area/api/chrome pattern.

Also add a `pendingDecision` tick test:
```js
test('tick polls /decision when bound+connected; caches pendingDecision (slice 5)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    api.nextDecision = { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 1, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' };
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api });
    await c.bind(42, 1000);
    await c.tick(2000);
    const s = await c.getState();
    assert.equal(s.pendingDecision?.id, 'dec-1');
  } finally { await cleanup(); }
});
```
(widen `fakeApi` so `getDecision` returns `api.nextDecision ?? null`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="unbind|setPaused|manualSkip|manualFail|overrideUrl|resolveDecision|broadcastPanelState|polls /decision"`
Expected: FAIL — `c.unbind is not a function`, `state.pendingDecision` undefined, etc.

- [ ] **Step 3: Write minimal implementation**

In `browserext/src/shared/state.ts`, add the field + initializer:
```ts
import type { FetchJob, PendingDecision } from './types.js';
```
(add `PendingDecision` to the existing import.)
Add to `ControllerState`:
```ts
  pendingDecision: PendingDecision | null;
```
Add to `initialControllerState`:
```ts
    pendingDecision: null,
```

In `browserext/src/controller/controller.ts`:
- Add imports: `buildPanelState` from `./panelState.js`; `PanelCommand` type from `../shared/rpc.js`; widen the `chrome` dep type if needed (`sendMessage` already present).
- Widen `CrawlController` interface with the new command methods + `broadcastPanelState`.
- Implement each command under the mutex with the gate guard. Reference shapes:
```ts
    async setPaused(paused: boolean, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        s.paused = paused;
        s.phaseStartedAt = now;
        await persist();
      } finally { release(); }
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
      } finally { release(); }
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
      } finally { release(); }
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
      } finally { release(); }
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
      } finally { release(); }
    },

    async resolveDecision(id: string, action: string, now: number = Date.now()): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (deps.api) await deps.api.resolveDecision(id, action);
        if (s.pendingDecision?.id === id) { s.pendingDecision = null; }
        await persist();
      } finally { release(); }
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
      } finally { release(); }
    },
```
- Add `overrideJobUrl` to `ApiClient` interface in `api.ts` and implement it (POST `/jobs/{id}/override` body `{ new_url }` → `FetchJob | null`; swallow → null). (This is a small additive change to `api.ts` in this same task — add a test alongside the controller test.)
- Add `broadcastPanelState`:
```ts
    async broadcastPanelState(sender?: { tabId: number | null }): Promise<void> {
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
```
- In `tick`, after the existing `/status` reconcile block (step 2) and only when `s.connected && s.boundTabId !== null`, poll the decision:
```ts
        if (s.connected && s.boundTabId !== null && deps.api) {
          const dec = await deps.api.getDecision();
          const changed = (dec?.id ?? null) !== (s.pendingDecision?.id ?? null);
          if (changed) { s.pendingDecision = dec; }
        }
```
(this runs under the existing `saveIfChanged` umbrella so it persists only on change — spec §2.2).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="unbind|setPaused|manualSkip|manualFail|overrideUrl|resolveDecision|broadcastPanelState|polls /decision"`
Expected: PASS. Then run the full controller suite: `cd browserext && node --test tests/controller/controller.test.mjs` to confirm no regression in the slice-1–4 tests.

- [ ] **Step 5: Commit**

```bash
git add browserext/src/shared/state.ts browserext/src/controller/controller.ts browserext/src/api.ts browserext/tests/controller/controller.test.mjs browserext/tests/api.test.mjs
git commit -m "feat(browserext): slice 5 controller commands (submit/skip/fail/override/unbind/decision) + pendingDecision + broadcastPanelState (amend §7, spec §2.5)"
```

---

## Task 6: controller/messageRouter.ts — SW onMessage router

**Files:**
- Create: `browserext/src/controller/messageRouter.ts`
- Create: `browserext/tests/controller/messageRouter.test.mjs`

**Interfaces:**
- Consumes: `CrawlController` (the widened interface from Task 5); `ApiClient`; `buildPanelState` (Task 2); `CsToSw`/`SwToCs`/`MessageReceipt`/`PanelCommand` types from `../shared/rpc.js`; `isAllowedFetchHost` from `../shared/hostPolicy.js`.
- Produces:
  - `MessageRouterDeps = { controller: CrawlController; chrome: Pick<ChromeRuntime, 'sendMessage'>; api: ApiClient; extensionId: string; now?: () => number }`.
  - `createMessageRouter(deps: MessageRouterDeps): { start(): void }` — registers a `chrome.runtime.onMessage` listener (injected; the real binding happens in `background.ts`). For each message:
    1. Validate `sender.id === deps.extensionId` (own extension only). Mismatch → no-op.
    2. Validate `sender.frameId === 0` (top frame only). Mismatch → no-op.
    3. Validate `sender.tab?.id` exists and `isAllowedFetchHost(new URL(sender.tab.url).hostname)` (top frame, allowed host — amend §4.7). Mismatch → no-op. (A non-allowed host never gets a panel and never sends here because the CS early-returns, but the SW re-checks — defense in depth.)
    4. Dispatch by `message.op`:
       - `REGISTER` / `TICK`: if `sender.tab.id === state.boundTabId` AND op is `TICK`, call `controller.tick()` (reconcile trigger — amend §7). For `REGISTER` or a non-bound-tab `TICK`, do NOT tick (read-only). Then reply `{ received: true, state: buildPanelState(state, { tabId: sender.tab.id }) }` via the async response.
       - `PAGE_READY`: call `controller.deliverPageReady({ documentId: sender.documentId, url: message.url, detection: message.detection, timeStamp: now() })`; reply `{ received: true }`.
       - `CAPTURE_RESULT` / `ACTION_PREPARED` / `ACTION_RESULT`: call the matching `controller.deliver*` with the message + sender; reply `{ received: true }`.
       - `COMMAND`: enforce the unbound-tab-only-`bind` rule (amend §4.7): if `state.boundTabId === null` and `command.kind !== 'bind'` → reply `{ received: true }` and no-op (no panel command from an unbound tab except bind). If `state.boundTabId !== null` and `sender.tab.id !== state.boundTabId` and `command.kind !== 'bind'` → refuse (no-op, the panel already shows "已绑定其他标签"). Otherwise route to the command method (`bind`→`controller.bind(tabId)`; `unbind`/`set_auto`/`set_paused`/`open`/`submit`/`skip`/`fail`/`override`/`decision`→ the matching Task-5 method). After the command, call `controller.broadcastPanelState({ tabId: sender.tab.id })` (amend §7 — immediate STATE_CHANGED to bound tab + sender).
    5. Return `true` (keep the message channel open for the async reply) when a reply is sent; `false` otherwise.
  - The router reads `state` via `controller.getState()` for the sender-computation + the bind-precondition check. (One read per message; the command methods re-acquire the mutex internally — no nested lock.)

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/messageRouter.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// Fake controller: records calls + holds a state the router reads via getState.
function fakeController(state) {
  const calls = { tick: 0, bind: [], deliverPageReady: [], deliverCaptureResult: [], deliverActionPrepared: [], deliverActionResult: [], unbind: 0, setAutoMode: [], setPaused: [], manualComplete: 0, manualSkip: [], manualFail: [], overrideUrl: [], resolveDecision: [], broadcastPanelState: [] };
  return {
    calls,
    getNavScope: () => ({ boundTabId: state.boundTabId }),
    async getState() { return JSON.parse(JSON.stringify(state)); },
    async tick() { calls.tick += 1; },
    async bind(tabId) { calls.bind.push(tabId); },
    async unbind() { calls.unbind += 1; },
    async setAutoMode(v) { calls.setAutoMode.push(v); },
    async setPaused(v) { calls.setPaused.push(v); },
    async manualComplete() { calls.manualComplete += 1; },
    async manualSkip(r) { calls.manualSkip.push(r); },
    async manualFail(m) { calls.manualFail.push(m); },
    async overrideUrl(u) { calls.overrideUrl.push(u); },
    async resolveDecision(id, a) { calls.resolveDecision.push([id, a]); },
    async deliverPageReady(e) { calls.deliverPageReady.push(e); },
    async deliverCaptureResult(r, s) { calls.deliverCaptureResult.push([r, s]); },
    async deliverActionPrepared(p, s) { calls.deliverActionPrepared.push([p, s]); },
    async deliverActionResult(r, s) { calls.deliverActionResult.push([r, s]); },
    async broadcastPanelState(s) { calls.broadcastPanelState.push(s); },
    // slice-1..4 deliver* nav handlers — present but not exercised here
    async deliverBeforeRequest() {}, async deliverBeforeRedirect() {}, async deliverCommitted() {}, async deliverHttpEvent() {}, async deliverError() {},
    async dispatchPrepareAction() {},
  };
}

function fakeChrome() {
  const sent = [];
  let listener = null;
  return {
    sent,
    async sendMessage(tabId, message, options) { sent.push({ tabId, message, options }); return { received: true }; },
    onMessage(cb) { listener = cb; },
    fire(message, sender) { return listener ? listener(message, sender) : undefined; },
  };
}

const EXT_ID = 'ext-123';
const sender = (tabId, documentId = 'DOC-1') => ({ id: EXT_ID, tab: { id: tabId, url: 'https://xjtu.edu.cn/p' }, frameId: 0, documentId });

test('REGISTER from unbound tab → receipt with bound:false, isBoundTab:false (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: null, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const reply = await chr.fire({ op: 'REGISTER', url: 'https://xjtu.edu.cn/p' }, sender(99));
    assert.equal(reply.received, true);
    assert.equal(reply.state.bound, false);
    assert.equal(reply.state.isBoundTab, false);
    assert.equal(ctrl.calls.tick, 0, 'REGISTER does not tick');
  } finally { await cleanup(); }
});

test('TICK from bound tab triggers controller.tick (amend §7); TICK from non-bound tab does NOT tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'TICK' }, sender(42));
    assert.equal(ctrl.calls.tick, 1);
    await chr.fire({ op: 'TICK' }, sender(99));
    assert.equal(ctrl.calls.tick, 1, 'non-bound TICK read-only');
  } finally { await cleanup(); }
});

test('bind command from unbound tab → controller.bind called + broadcast (amend §4.7, §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: null, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'bind' } }, sender(99));
    assert.deepEqual(ctrl.calls.bind, [99]);
    assert.equal(ctrl.calls.broadcastPanelState.length, 1);
  } finally { await cleanup(); }
});

test('non-bind command from unbound tab → refused (no-op, amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: null, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'skip' } }, sender(99));
    assert.deepEqual(ctrl.calls.manualSkip, [], 'non-bind command from unbound tab refused');
  } finally { await cleanup(); }
});

test('command from non-bound tab when another tab is bound → refused (no takeover, amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'skip' } }, sender(99));
    assert.deepEqual(ctrl.calls.manualSkip, [], 'non-bound tab cannot issue skip while 42 is bound');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT routed to deliverCaptureResult with sender (amend §4.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'capturing', currentJob: { id: 'job-1' }, connected: true, autoMode: true, paused: false, navigation: { attempt: 1 }, lastError: null, navigationAttempt: 1 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const msg = { op: 'CAPTURE_RESULT', rpcId: 'rpc-1', jobId: 'job-1', ok: true, url: 'u', html: 'h', title: 't', paginationStates: [], detection: { errorPage: false, terminalReason: null } };
    await chr.fire(msg, sender(42, 'DOC-1'));
    assert.equal(ctrl.calls.deliverCaptureResult.length, 1);
    assert.deepEqual(ctrl.calls.deliverCaptureResult[0][1], { id: EXT_ID, tab: { id: 42, url: 'https://xjtu.edu.cn/p' }, frameId: 0, documentId: 'DOC-1' });
  } finally { await cleanup(); }
});

test('sender.id mismatch (not own extension) → no-op (amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const reply = await chr.fire({ op: 'TICK' }, { ...sender(42), id: 'other-ext' });
    assert.equal(reply, undefined, 'foreign-extension message dropped');
    assert.equal(ctrl.calls.tick, 0);
  } finally { await cleanup(); }
});

test('disallowed-host sender → no-op (defense in depth, amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const offsiteSender = { id: EXT_ID, tab: { id: 42, url: 'https://evil.com/p' }, frameId: 0, documentId: 'DOC-1' };
    await chr.fire({ op: 'TICK' }, offsiteSender);
    assert.equal(ctrl.calls.tick, 0);
  } finally { await cleanup(); }
});

test('decision command → resolveDecision + broadcast (amend §4.9, §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: { id: 'dec-1' }, phase: 'landed', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'decision', id: 'dec-1', action: 'accept' } }, sender(42));
    assert.deepEqual(ctrl.calls.resolveDecision, [['dec-1', 'accept']]);
    assert.equal(ctrl.calls.broadcastPanelState.length, 1);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="REGISTER from unbound|TICK from bound|bind command|non-bind command|command from non-bound|CAPTURE_RESULT routed|sender.id mismatch|disallowed-host|decision command"`
Expected: FAIL — module not found.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/messageRouter.ts`:
```ts
/** SW-side chrome.runtime.onMessage router (amend §4.1, §4.7, §7). This is the
 *  composition seam that finally connects the browser CS messages to the
 *  CrawlController built in slices 1–4 + the slice-5 command methods. It:
 *    - validates sender (own extension, top frame, allowed host) per amend §4.7;
 *    - routes REGISTER/TICK → receipt + sender-computed PanelState (TICK from the
 *      bound tab also triggers controller.tick(); non-bound TICK is read-only);
 *    - routes PAGE_READY/CAPTURE_RESULT/ACTION_PREPARED/ACTION_RESULT → deliver*;
 *    - routes COMMAND → the command method, enforcing the unbound-tab-only-bind
 *      rule and the non-preemptible binding (amend §7);
 *    - after a state-mutating command, calls broadcastPanelState to push
 *      STATE_CHANGED to the bound tab + the commanding sender (amend §7).
 *  Dependency-injected so node tests drive it with a fake controller + chrome. */

import type { ChromeRuntime } from '../chrome.js';
import type { ApiClient } from '../api.js';
import type { CrawlController } from './controller.js';
import type { CsToSw, MessageReceipt, PanelCommand } from '../shared/rpc.js';
import { buildPanelState } from './panelState.js';
import { isAllowedFetchHost } from '../shared/hostPolicy.js';

export interface MessageSender {
  id?: string;
  tab?: { id: number; url?: string };
  frameId?: number;
  documentId?: string;
}

export interface MessageRouterDeps {
  controller: CrawlController;
  chrome: Pick<ChromeRuntime, 'sendMessage'>;
  api: ApiClient;
  extensionId: string;
  now?: () => number;
}

export interface MessageRouter {
  start(): void;
  handle(message: CsToSw, sender: MessageSender): Promise<MessageReceipt | true | false>;
}

function hostOf(url?: string): string {
  try { return url ? new URL(url).hostname : ''; } catch { return ''; }
}

export function createMessageRouter(deps: MessageRouterDeps): MessageRouter {
  const now = deps.now ?? (() => Date.now());

  async function routeCommand(cmd: PanelCommand, tabId: number): Promise<void> {
    const c = deps.controller;
    switch (cmd.kind) {
      case 'bind': await c.bind(tabId, now()); break;
      case 'unbind': await c.unbind(now()); break;
      case 'set_auto': await c.setAutoMode(cmd.value, now()); break;
      case 'set_paused': await c.setPaused(cmd.value, now()); break;
      case 'open': await c.setAutoMode(true, now()); break;   // open ⇒ ensure auto + next tick navigates
      case 'submit': await c.manualComplete(now()); break;
      case 'skip': await c.manualSkip(cmd.reason, now()); break;
      case 'fail': await c.manualFail(cmd.message, now()); break;
      case 'override': await c.overrideUrl(cmd.url, now()); break;
      case 'decision': await c.resolveDecision(cmd.id, cmd.action, now()); break;
    }
  }

  async function handle(message: CsToSw, sender: MessageSender): Promise<MessageReceipt | true | false> {
    // amend §4.7: own extension + top frame + allowed host only.
    if (sender.id !== deps.extensionId) return false;
    if (sender.frameId !== 0) return false;
    const tabId = sender.tab?.id;
    if (tabId === undefined) return false;
    if (!isAllowedFetchHost(hostOf(sender.tab?.url))) return false;

    const state = await deps.controller.getState();

    switch (message.op) {
      case 'REGISTER': {
        const ps = buildPanelState(state, { tabId });
        return { received: true, state: ps };
      }
      case 'TICK': {
        // amend §7: bound-tab TICK reconciles; non-bound TICK is read-only.
        if (tabId === state.boundTabId) await deps.controller.tick(now());
        const ps = buildPanelState(state, { tabId });
        return { received: true, state: ps };
      }
      case 'PAGE_READY': {
        await deps.controller.deliverPageReady({
          documentId: sender.documentId ?? '', url: message.url,
          detection: message.detection, timeStamp: now(),
        });
        return { received: true };
      }
      case 'CAPTURE_RESULT': {
        await deps.controller.deliverCaptureResult(message, { tab: sender.tab, frameId: sender.frameId, documentId: sender.documentId });
        return { received: true };
      }
      case 'ACTION_PREPARED': {
        await deps.controller.deliverActionPrepared(message, { tab: sender.tab, frameId: sender.frameId, documentId: sender.documentId });
        return { received: true };
      }
      case 'ACTION_RESULT': {
        await deps.controller.deliverActionResult(message, { tab: sender.tab, frameId: sender.frameId, documentId: sender.documentId });
        return { received: true };
      }
      case 'COMMAND': {
        const bound = state.boundTabId;
        // amend §4.7: unbound tab may ONLY bind; a non-bound tab cannot issue other commands while bound.
        if (bound === null && message.command.kind !== 'bind') return { received: true };
        if (bound !== null && tabId !== bound && message.command.kind !== 'bind') return { received: true };
        await routeCommand(message.command, tabId);
        await deps.controller.broadcastPanelState({ tabId });
        return { received: true };
      }
      default: return false;
    }
  }

  return {
    start() {
      // Real chrome binding is done in background.ts via chrome.runtime.onMessage.addListener;
      // tests call handle() directly. start() is a no-op here to keep the surface stable.
    },
    handle,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="REGISTER from unbound|TICK from bound|bind command|non-bind command|command from non-bound|CAPTURE_RESULT routed|sender.id mismatch|disallowed-host|decision command"`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/controller/messageRouter.ts browserext/tests/controller/messageRouter.test.mjs
git commit -m "feat(browserext): slice 5 SW messageRouter — onMessage→deliver*/command + PanelState receipts (amend §4.1, §4.7, §7)"
```

---

## Task 7: content/index.ts — panel mount + REGISTER + TICK + STATE_CHANGED render

**Files:**
- Modify: `browserext/src/content/index.ts`
- Modify: `browserext/tests/content/index.test.mjs`

**Interfaces:**
- Consumes: `mountPanel` (Task 3); `panelStateEqual` (Task 1); `CsToSw`/`SwToCs` types.
- Produces: the gated content bootstrap now also (on allowed host) mounts the panel, sends `REGISTER` once, sets a 2s `TICK` interval, and registers a `chrome.runtime.onMessage` listener for `STATE_CHANGED` → re-render via `panel.render(state)` (skipped when `panelStateEqual`).

- [ ] **Step 1: Write the failing test**

Append to `browserext/tests/content/index.test.mjs` (read it first; reuse its `fakeDoc` pattern). The slice-1/4 tests pass NO `document`/`chrome`, so they skip the new panel block by the guard — they stay green unchanged. The new test passes injected `document`/`chrome` so the panel block runs:
```js
test('bootstrapContent (gate ON): allowed host mounts panel, sends REGISTER, wires STATE_CHANGED (slice 5)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const sent = [];
    let stateChangedCb = null;
    const attrs = new Map();
    const documentElement = { getAttribute: (n) => attrs.get(n) ?? null, setAttribute: (n, v) => attrs.set(n, v), removeAttribute: (n) => attrs.delete(n) };
    const shadowRoot = { innerHTML: '', querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} };
    const fakeDoc = {
      readyState: 'complete', body: { innerText: 'x', appendChild() {} }, title: 't',
      links: { length: 1 }, forms: [], documentElement,
      addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
      createElement: () => ({ attachShadow: () => shadowRoot, style: {}, appendChild() {}, id: '' }),
    };
    const fakeChrome = {
      runtime: {
        sendMessage: (m) => { sent.push(m); return Promise.resolve({ received: true, state: undefined }); },
        onMessage: { addListener: (cb) => { stateChangedCb = cb; } },
        id: 'ext-123',
      },
    };
    await mod.bootstrapContent({ hostname: 'xjtu.edu.cn', documentElement, now: 1000, document: fakeDoc, chrome: fakeChrome });
    assert.ok(sent.some((m) => m.op === 'REGISTER'), 'REGISTER sent on allowed host');
    assert.equal(typeof stateChangedCb, 'function', 'STATE_CHANGED listener wired');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="mounts panel|REGISTER|TICK"`
Expected: FAIL — no panel mount / REGISTER.

- [ ] **Step 3: Write minimal implementation**

In `browserext/src/content/index.ts`, widen `ContentDeps` and add panel wiring inside the gated `if (EXCLUSIVE_CONTROL_ENABLED)` block, after the existing `onAllowedHost`/rpcRouter/PAGE_READY wiring. **Key:** the existing slice-1/4 tests call `bootstrapContent` WITHOUT a `document`/`chrome` and expect the marker + (for slice 4) the `chrome.runtime` work-RPC block to run against the node globals — which are `undefined` there, so those blocks already no-op safely. To keep that true, resolve `doc`/`chr` as `deps.document ?? document` / `deps.chrome ?? chrome` and guard every access with a presence check (as the slice-4 block already does). Do NOT remove the existing `typeof chrome` guard.
```ts
import { mountPanel } from './panel.js';
import { panelStateEqual } from './panelState.js';
import type { PanelState } from '../shared/rpc.js';
```
Add to `ContentDeps` (optional, defaulting to the globals):
```ts
  document?: Document;
  chrome?: typeof chrome;
```
Inside the allowed-host branch (after the existing `chrome.runtime.onMessage` work-RPC listener + PAGE_READY setup), add a panel block guarded so the slice-1/4 node tests (no `document`/`chrome` passed) skip it without error:
```ts
    // Slice 5: panel + REGISTER + 2s TICK + STATE_CHANGED render.
    const doc = deps.document ?? (typeof document !== 'undefined' ? document : undefined);
    const chr = deps.chrome ?? (typeof chrome !== 'undefined' ? chrome : undefined);
    if (doc?.body && chr?.runtime?.sendMessage) {
      let panel: ReturnType<typeof mountPanel> | null = null;
      try {
        const host = doc.createElement('div') as HTMLDivElement & { attachShadow: (i: { mode: 'open' }) => unknown };
        host.id = 'dext-panel-host';
        doc.body.appendChild(host);
        panel = mountPanel({ host: host as never });
      } catch { panel = null; }
      if (panel) {
        // REGISTER once (receipt carries the initial PanelState).
        void chr.runtime.sendMessage({ op: 'REGISTER', url: location.href } as CsToSw)
          .then((receipt: unknown) => {
            const r = receipt as { state?: PanelState };
            if (r?.state) panel!.render(r.state);
          })
          .catch(() => {});
        // 2s TICK — wake/reconcile the SW (MV3 doesn't guarantee SW stays alive).
        setInterval(() => { try { void chr.runtime.sendMessage({ op: 'TICK' } as CsToSw); } catch { /* SW asleep */ } }, 2000);
        // STATE_CHANGED → re-render (skip no-op renders via panelStateEqual).
        let last: PanelState | undefined;
        chr.runtime.onMessage.addListener((msg: SwToCs) => {
          if (msg && msg.op === 'STATE_CHANGED') {
            if (panelStateEqual(last, msg.state)) return false;
            last = msg.state;
            try { panel!.render(msg.state); } catch { /* panel unmounted */ }
          }
          return false;
        });
      }
    }
```
(Both `onMessage` listeners — the slice-4 work-RPC listener and this slice-5 `STATE_CHANGED` listener — coexist; `chrome.runtime.onMessage` supports multiple listeners.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="mounts panel|REGISTER|TICK"`
Expected: PASS. Then run the full content suite: `cd browserext && node --test tests/content/index.test.mjs` to confirm no regression in the slice-1/4 content tests.

- [ ] **Step 5: Commit**

```bash
git add browserext/src/content/index.ts browserext/tests/content/index.test.mjs
git commit -m "feat(browserext): slice 5 content panel mount + REGISTER/TICK + STATE_CHANGED render (amend §7)"
```

---

## Task 8: background.ts — wire the messageRouter (gated)

**Files:**
- Modify: `browserext/src/background.ts`
- Modify: `browserext/tests/background.test.mjs`

**Interfaces:**
- Consumes: `createMessageRouter` (Task 6); the existing `wireBackground` controller + chrome.
- Produces: `wireBackground` also constructs the `MessageRouter` and registers it on `chrome.runtime.onMessage` (the real binding — `start()` is a stable no-op surface; the actual `addListener` happens here so the router has access to the real `chrome.runtime`).

- [ ] **Step 1: Write the failing test**

Append to `browserext/tests/background.test.mjs` (read it first; reuse its existing inline `fakeChrome`/`fakeApi` + the `globalThis.chrome.storage.local` shim). Widen the existing test's `fakeApi` minimally so `wireBackground` passing it to the router doesn't trip a missing-method (the router only *calls* api methods at message-handle time, so adding no-op stubs is enough for the registration test). Add a NEW test that wires a fake `chrome.runtime.onMessage`:
```js
test('wireBackground registers a chrome.runtime.onMessage listener (slice 5 router wired)', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  globalThis.chrome = globalThis.chrome ?? {};
  globalThis.chrome.storage = globalThis.chrome.storage ?? {};
  globalThis.chrome.runtime = globalThis.chrome.runtime ?? { id: 'ext-123', onMessage: { addListener() {} } };
  let registered = false;
  const prevOnMessage = globalThis.chrome.runtime.onMessage;
  globalThis.chrome.runtime.onMessage = { addListener: () => { registered = true; } };
  try {
    const store = new Map();
    globalThis.chrome.storage.local = {
      async get(keys) { const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]); const o = {}; for (const k of arr) if (store.has(k)) o[k] = store.get(k); return o; },
      async set(o) { for (const [k, v] of Object.entries(o)) store.set(k, v); },
      async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
    };
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {}, onBeforeRequest() {}, onBeforeRedirect() {}, onCommitted() {}, onHistoryStateUpdated() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; }, async getTab() { return null; }, async sendMessage() { return { received: true }; },
      registerAlarm() {},
    };
    const fakeApi = { async getStatus() { return null; }, async claimNextJob() { return null; }, async failJob() {}, async skipJob() {}, async sendHeartbeat() {}, async getDecision() { return null; }, async resolveDecision() {}, async completeJob() {}, async overrideJobUrl() { return null; } };
    mod.wireBackground({ chrome: fakeChrome, api: fakeApi });
    assert.equal(registered, true, 'messageRouter registered on chrome.runtime.onMessage');
  } finally {
    globalThis.chrome.runtime.onMessage = prevOnMessage;
    await cleanup();
  }
});
```
The existing `wireBackground constructs navMonitor ...` test stays unchanged and still passes because the router registration is guarded (Step 3) — its fake `chrome` has no `runtime.onMessage`, so the guard skips registration and nothing throws.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="messageRouter wired|onMessage listener"`
Expected: FAIL — `onMessageListener` stays null (router not wired).

- [ ] **Step 3: Write minimal implementation**

In `browserext/src/background.ts`, after the navMonitor + alarm wiring in `wireBackground`, construct the router and register it on the **global** `chrome.runtime.onMessage` (parallel to how `createRealChromeRuntime.registerAlarm` reaches for the global `chrome.alarms`). The `ChromeRuntime` interface does NOT carry `runtime`, so reach for the global directly, guarded:
```ts
import { createMessageRouter } from './controller/messageRouter.js';
```
and inside `wireBackground`, after `navMonitor.start()` / the alarm registration:
```ts
  const router = createMessageRouter({
    controller,
    chrome: deps.chrome,
    api: deps.api,
    extensionId: (typeof chrome !== 'undefined' && chrome.runtime?.id) ? chrome.runtime.id : 'dext',
  });
  if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage) {
    chrome.runtime.onMessage.addListener(
      (msg: unknown, sender: unknown, sendResponse: (r: unknown) => void) => {
        void router
          .handle(msg as never, sender as never)
          .then((reply) => { try { sendResponse(reply); } catch { /* listener already returned */ } });
        return true;   // keep the channel open for the async sendResponse
      },
    );
  }
```
The `typeof chrome` guard means the existing `background.test.mjs` fake (no `chrome.runtime`) skips registration and stays green — only the new Task-8 test (which installs `chrome.runtime.onMessage`) asserts the `addListener` call. The gate semantics are preserved: when the official build is gate-OFF, the controller's `tick`/`bind`/`deliver*`/command bodies early-return, so a message through the router is a harmless no-op (consistent with slices 2–4 wiring the controller/navMonitor regardless of the gate).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="messageRouter wired|onMessage listener"`
Expected: PASS. Then run the full background suite + build test:
```bash
cd browserext && node --test tests/background.test.mjs && node --test tests/build.test.mjs
```
Confirm the build test still passes (the official default gate stays OFF — `if (true) return` in the controller, `if (false) {` in the content body).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/background.ts browserext/tests/background.test.mjs
git commit -m "feat(browserext): slice 5 wire messageRouter into background SW (amend §4.7, §7)"
```

---

## Task 9: Integration test — claim→navigate→capture→complete through the router (gate ON)

**Files:**
- Create: `browserext/tests/integration.test.mjs`

**Interfaces:**
- Consumes: `createCrawlController`, `createControllerStorage`, `createMessageRouter`, `buildPanelState`, `createFetchApi` (or a fake api).
- Produces: an end-to-end test that drives the **whole slice-1–5 stack** through the messageRouter with fakes, exercising the slice-5 acceptance row (spec §6.2: "完整 claim→navigate→capture→complete 集成; 测试构建开启门控"). The gate is ON (harness injects `true`).

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/integration.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeArea() {
  const store = new Map();
  return {
    async get(keys) { const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]); const o = {}; for (const k of arr) if (store.has(k)) o[k] = store.get(k); return o; },
    async set(o) { for (const [k, v] of Object.entries(o)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
}

function job(id, url = `https://xjtu.edu.cn/${id}`) {
  return { id, url, status: 'assigned', context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] }, created_at: 't', timeout_seconds: 60, action: null, identity_url: null };
}

test('integration: bind → claim → navigate → land → capture → complete (gate ON, slice-5 acceptance)', async () => {
  const ctrlMod = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  const routerMod = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const area = fakeArea();
    // Fake api: first getStatus returns job-1; after complete, returns null.
    let completed = false;
    const api = {
      async getStatus() { return completed ? { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } } : { current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }; },
      async claimNextJob() { return job('job-1'); },
      async failJob() {}, async skipJob() {}, async sendHeartbeat() {},
      async getDecision() { return null; }, async resolveDecision() {},
      async completeJob(id) { assert.equal(id, 'job-1'); completed = true; },
      async overrideJobUrl() { return null; },
    };
    const sent = [];
    const chrome = {
      async getTab() { return { id: 42, url: 'https://xjtu.edu.cn/job-1' }; },
      async updateTabUrl(tabId, url) { sent.push({ type: 'updateTabUrl', tabId, url }); },
      async sendMessage(tabId, message, options) { sent.push({ type: 'sendMessage', tabId, message, options }); return { received: true }; },
    };
    const c = ctrlMod.createCrawlController({ storage: ctrlMod.createControllerStorage(area), api, chrome });
    const router = routerMod.createMessageRouter({ controller: c, chrome, api, extensionId: 'ext-123' });

    const sender = (documentId = 'DOC-1') => ({ id: 'ext-123', tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' }, frameId: 0, documentId });

    // 1. bind (command from the tab that will become bound)
    await router.handle({ op: 'COMMAND', command: { kind: 'bind' } }, sender());
    // 2. enable auto + tick → claim + navigate
    await router.handle({ op: 'COMMAND', command: { kind: 'set_auto', value: true } }, sender());
    await c.tick(2000);
    assert.equal((await c.getState()).phase, 'navigating');
    assert.ok(sent.some((s) => s.type === 'updateTabUrl' && s.url === 'https://xjtu.edu.cn/job-1'));

    // 3. landing signals: beforeRequest → committed → http ok → PAGE_READY
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 2100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 2200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 2300 }, 'ok');
    await router.handle({ op: 'PAGE_READY', url: 'https://xjtu.edu.cn/job-1', title: 'T', detection: { errorPage: false, terminalReason: null } }, sender('DOC-1'));
    assert.equal((await c.getState()).phase, 'capturing', 'landed → capturing (CAPTURE dispatched via sendMessage)');
    assert.ok(sent.some((s) => s.type === 'sendMessage' && s.message.op === 'CAPTURE' && s.options.documentId === 'DOC-1'));

    // 4. CAPTURE_RESULT ok → complete
    const rpc = (await c.getState()).pendingRpc;
    await router.handle({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: true, url: 'https://xjtu.edu.cn/job-1', html: '<html/>', title: 'T', paginationStates: [], detection: { errorPage: false, terminalReason: null } }, sender('DOC-1'));
    assert.equal(completed, true, '/jobs/job-1/complete called');
    assert.equal((await c.getState()).phase, 'idle');

    // 5. STATE_CHANGED was broadcast to the bound tab after the command(s).
    assert.ok(sent.some((s) => s.type === 'sendMessage' && s.message.op === 'STATE_CHANGED'), 'STATE_CHANGED broadcast (amend §7)');
  } finally {
    await ctrlMod.cleanup();
    await routerMod.cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && node --test tests/integration.test.mjs`
Expected: FAIL (initially red — likely a missing wiring or a phase assertion off; iterate until green).

- [ ] **Step 3: Make it pass (no new implementation — this is an acceptance test)**

If a wiring gap surfaces (e.g. `broadcastPanelState` not called after `bind` because `bind` is a controller method that doesn't self-broadcast), the fix is in `controller.ts`/`messageRouter.ts` (the messageRouter already calls `broadcastPanelState` after `routeCommand` — verify that path covers `bind`). If `deliverPageReady` via the router needs the `sender.documentId` to populate `PAGE_READY`, the router already passes it (Task 6 Step 3). Iterate on the test/assertions only; do NOT change the contract. If the gate needs to be ON, the harness already injects `true` for both `controller.ts` and `messageRouter.ts` bundles.

- [ ] **Step 4: Run the full suite to verify green + no regression**

Run: `cd browserext && npm test`
Expected: all pass, including the new integration test. Then: `cd browserext && npm run typecheck`
Expected: exits 0.

- [ ] **Step 5: Commit**

```bash
git add browserext/tests/integration.test.mjs
git commit -m "test(browserext): slice 5 integration — claim→navigate→capture→complete through messageRouter (gate ON)"
```

---

## Self-Review (run after writing; fix inline)

**1. Spec coverage (slice-5 row, spec §6.2 + amend §7 + §4.7–4.9):**
- Panel UI (inlined CSS) → Task 3. ✓
- `STATE_CHANGED`/`PanelState` → Tasks 2 + 5 (`broadcastPanelState`) + 6 (router) + 7 (render). ✓
- Inlined pending-decision (no `window.confirm`) → Task 3 (decision controls + the `doesNotMatch /window\.confirm/` assertion). ✓
- Bind/unbind controls → Tasks 3 + 5 (`unbind`). ✓
- 完整 claim→navigate→capture→complete 集成 → Task 9. ✓
- 测试构建开启门控 → Task 9 runs under the harness gate-ON bundle (no change to `build.test.mjs`'s official-OFF assertion — that is the slice-6 flip). ✓
- amend §7 REGISTER/TICK transport receipts carrying sender-computed `PanelState` → Task 6. ✓
- amend §7 non-preemptible binding (existing bound tab blocks bind from other tabs) → Task 6 (the `bound !== null && tabId !== bound` refuse branch) + Task 3 ("已绑定其他标签" render). ✓
- amend §7 non-bound TICK read-only → Task 6. ✓
- amend §7 STATE_CHANGED to bound tab + commanding sender → Task 5 (`broadcastPanelState`). ✓
- amend §7 `PanelState.lastError` is `ControllerError | null` (panel renders text) → Tasks 1 + 2. ✓
- amend §4.7 permission boundary per message (own ext / top frame / allowed host; unbound tab only `bind`) → Task 6. ✓
- amend §4.9 no `window.confirm` → Task 3. ✓
- Gap check: the `submit` command → `manualComplete` → `dispatchCapture`; the `submitting` phase is set then `dispatchCapture` moves to `capturing`. ✓ (Task 5)
- Gap check: `override` needs `api.overrideJobUrl` — added in Task 5 Step 3 (small `api.ts` addition + test). ✓

**2. Placeholder scan:** No TBD/TODO/"add appropriate"/"similar to Task N". Every code step shows the full code. The Task 8 Step 3 `sendResponse` wiring has a note to refine the 3-arg listener signature — this is an explicit instruction with the real signature, not a placeholder. ✓

**3. Type consistency:** `PanelSender.tabId` (Task 2) vs `sender.tab?.id` (Task 6) — the router passes `{ tabId }` to `buildPanelState` consistently. `manualComplete`/`manualSkip`/`manualFail`/`overrideUrl`/`resolveDecision`/`setPaused`/`unbind` names match between the `CrawlController` interface (Task 5), the router's `routeCommand` (Task 6), and the test fakes. `pendingDecision` field name matches between `state.ts` (Task 5), `buildPanelState` (Task 2), and the tick-poll (Task 5). `formatError`/`panelStateEqual` (Task 1) consumed by `panel.ts` (Task 3) and `index.ts` (Task 7). ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-29-browserext-slice5-panel-integration.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
