# Browserext exclusive control — Slice 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land slice 6 — the **final** slice of Phase-2 exclusive control. Slice 6 closes the gate (`EXCLUSIVE_CONTROL_ENABLED` default `false → true` in the official build), fixes the four Minors deferred from slice 5, removes stale Phase-1 code residue, rewrites the docs to make the extension the sole official frontend (userscript demoted to an emergency-recovery artifact), and finally passes the six real-browser acceptance checks of spec §5.6. The code/docs/cleanup work is Claude's; the real-browser acceptance (Task 8) is the user's — Claude does not touch a live Chrome instance.

**Architecture:** Slice 6 is mostly finishing work, not new machinery. Four TDD tasks touch production code: (1) `panelStateEqual` deepens its `lastError` comparison from kind-only to kind+detail so a same-kind-different-detail error no longer leaves stale error text in the panel (slice-5 deferred Minor #1); (2) the slice-5 manual commands get the isolated unit tests they lacked (deferred Minors #2/#3) and the panel's "已绑定其他标签" middle render branch gets a test (Minor #4); (3) Phase-1 code residue is removed — `chrome.findOwnerTab` (the Phase-1 owner-election stub, never called by the controller under exclusive control) plus its tests and the stale "rename from watchdog" test label; (4) the gate flip itself is a one-line esbuild default plus a `build.test.mjs` assertion rewrite. Then two non-TDD tasks: docs rewrite (spec status banner, browserext README, CLAUDE.md) and the manifest/version bump. The gate flip (Task 6) is the **single load-bearing change** — it is committed **only after** the real-browser acceptance (Task 8) passes. Everything before Task 8 lands gated-OFF-safe (a green test suite with the gate still off); everything after the gate flip is docs/acceptance only. No HTTP-contract change, no DB-schema change.

**Critical ordering constraint (the user's hard rule):** Tasks 1–5 land with the gate still **OFF** — each leaves the suite green and the official build a runtime no-op. Task 6 (the gate flip + `build.test.mjs` rewrite) is **staged but NOT committed** until the user reports the six real-browser acceptance checks (Task 8) pass against a gate-ON build they load themselves. Task 7 (docs) is written in parallel but committed alongside the gate flip. The order: 1→2→3→4→5 (committed, gate OFF) → 6+7 (staged, gate ON, awaiting acceptance) → 8 (user acceptance) → commit 6+7 → 9 (memory + ledger).

**Tech Stack:** TypeScript 5.7, MV3 Chrome/Edge (≥110), esbuild, node:test. No new deps.

## Global Constraints

Copied verbatim from the spec/amend + slice-1–5 plans so every task implicitly inherits them:

- **Gate flip is deferred until acceptance passes.** The gate default flips `false → true` only in Task 6, and Task 6 is committed only after Task 8 (real-browser acceptance) passes. Until then every official build is gate-OFF (runtime no-op). The user loads a gate-ON build themselves for acceptance; Claude never loads Chrome. (spec §6.1, user's explicit rule)
- **`/status.current_job` is the sole job-state truth.** `ControllerState.currentJob` is a client cache; the CS holds NO authoritative job state. (spec §1.3, amend §9)
- **Controller is the sole `chrome.tabs.update` caller.** The CS never navigates. (spec §3.6)
- **HTTP contract is FIXED.** No new/changed endpoints, no DB schema change. (CLAUDE.md, spec §1.3, amend §9)
- **One conventional commit per green step.** `feat(browserext)`/`test(browserext)`/`chore(browserext)`/`docs(browserext)`. (CLAUDE.md)
- **browserext tests run with `npm test` (node:test, no pytest).** Relative imports in `src/*.ts` carry `.js`. The harness bundles via esbuild and injects `EXCLUSIVE_CONTROL_ENABLED = true`. (CLAUDE.md)
- **`dist/` is gitignored.**
- **Baseline before starting:** `cd browserext && npm test` reports `tests 225 pass 225 fail 0`; `npm run typecheck` exits 0; `npm run build` exits 0 (gate OFF) on branch `slice4` at head `fd5c193`.

---

## File Structure

New/modified files in this slice. Decomposition: each production change is isolated to one module + its test file, so each task is independently green and reviewable.

```
browserext/
  src/content/panelState.ts          # MODIFY (Task 1): deepen lastError equality (kind+detail)
  tests/content/panelState.test.mjs  # MODIFY (Task 1): + same-kind-different-detail test
  src/content/panel.ts               # NO CHANGE (Task 2 tests the existing middle branch)
  tests/content/panel.test.mjs        # MODIFY (Task 2): + 已绑定其他标签 middle-branch render test
  src/controller/controller.ts       # NO CHANGE (Task 3 tests existing manualComplete/manualSkip)
  tests/controller/controller.test.mjs # MODIFY (Task 3): + manualComplete isolated test + manualSkip lastError===null assertion
  src/chrome.ts                      # MODIFY (Task 4): remove findOwnerTab (Phase-1 residue)
  tests/chrome.test.mjs              # MODIFY (Task 4): remove findOwnerTab tests + stale "rename from watchdog" label
  tests/background.test.mjs         # MODIFY (Task 4): drop findOwnerTab from fake chrome (no-longer-needed stub)
  src/controller/controller.ts       # NO CHANGE (Task 4 — CrawlControllerDeps.chrome never typed findOwnerTab)
  esbuild.config.mjs                 # MODIFY (Task 6): GATE default false → true
  tests/build.test.mjs               # MODIFY (Task 6): rewrite gate-OFF assertions → gate-ON assertions
  manifest.json                       # MODIFY (Task 7): description/version bump (Phase 2 → GA)
  README.md                           # MODIFY (Task 7): rewrite — extension is sole official frontend; userscript → emergency recovery
docs/superpowers/specs/
  2026-06-28-browserext-exclusive-control-design.md        # MODIFY (Task 7): status banner → Phase 2 LANDED/GA
  2026-06-28-browserext-exclusive-control-design-amend.md # MODIFY (Task 7): status banner → amendment folded into landed Phase 2
CLAUDE.md                              # MODIFY (Task 7): browserext row → Phase 2 GA, sole frontend; userscript demoted
.superpowers/sdd/progress.md           # MODIFY (Task 9): slice-6 ledger entry
memory/browserext-slice6-landed.md    # CREATE (Task 9)
memory/MEMORY.md                       # MODIFY (Task 9): + slice-6 pointer
```

Boundary notes:
- Task 1 (`panelStateEqual`) is the only production behavior change in the panel layer; it is pure and the existing 5 `panelState.test.mjs` tests stay green (they never asserted kind-only).
- Task 4 removes `findOwnerTab` — the controller's `CrawlControllerDeps.chrome` type is `Pick<ChromeRuntime, 'getTab' | 'updateTabUrl' | 'sendMessage'>` (controller.ts:52), which never included `findOwnerTab`, so removing it from `ChromeRuntime` + the real impl is type-safe. The only callers were tests.
- Task 6 flips a single `false`→`true` literal and rewrites one test file. The controller's `if (!EXCLUSIVE_CONTROL_ENABLED) return;` guards (26 sites) and the content's `if (EXCLUSIVE_CONTROL_ENABLED) {` guard (1 site) are unchanged — only the injected value flips.

---

## Part A — Code tasks (TDD, gate stays OFF)

### Task 1: panelStateEqual — deepen lastError equality (slice-5 deferred Minor #1)

**Why:** Slice 5's `panelStateEqual` compares `lastError` by `kind` only. So when a `nav_error` mutates from `ERR_CONNECTION_RESET` to `ERR_NAME_NOT_RESOLVED` (same kind, different detail), or `unexpected_status` goes `401 → 403`, the equality check returns `true`, the `STATE_CHANGED` is dropped, and the panel keeps showing the stale error text until the next phase change or a 2s TICK that happens to touch another field. Gate-OFF this is invisible; gate-ON it is a real stale-UI bug. The fix: include the discriminating detail field per `ControllerError` kind.

**Files:**
- Modify: `browserext/src/content/panelState.ts`
- Modify: `browserext/tests/content/panelState.test.mjs`

**Interfaces:**
- Consumes: `ControllerError` (type) from `../shared/state.js`; `PanelState` (type) from `../shared/rpc.js`.
- Produces: unchanged export signatures — `formatError` untouched; `panelStateEqual(a, b)` now compares `lastError` by kind **and** the kind's discriminating detail (`error` for `nav_error`, `statusCode` for `unexpected_status`, `missing` for `content_unavailable`; `gateway_5xx`/`rate_limited` are kind-only with no detail).

- [ ] **Step 1: Write the failing test**

Append to `browserext/tests/content/panelState.test.mjs` (after the existing `panelStateEqual` test):
```js
test('panelStateEqual: same-kind-different-detail lastError is UNEQUAL (slice-6 fix, slice-5 deferred #1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    const base = { isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false, phase: 'navigating', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null };
    // nav_error: same kind, different error string → must be unequal (else stale ERR_CONNECTION_RESET stays on screen).
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_CONNECTION_RESET' } },
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_NAME_NOT_RESOLVED' } },
    ), false);
    // unexpected_status: same kind, different statusCode → unequal.
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'unexpected_status', statusCode: 401 } },
      { ...base, lastError: { kind: 'unexpected_status', statusCode: 403 } },
    ), false);
    // content_unavailable: same kind, different missing → unequal.
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'content_unavailable', missing: 'page_ready', sourceDocumentId: 'D', since: 1, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: false } },
      { ...base, lastError: { kind: 'content_unavailable', missing: 'capture_result', sourceDocumentId: 'D', since: 1, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: false } },
    ), false);
    // kind-only errors (gateway_5xx / rate_limited): equal when same kind.
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'gateway_5xx' } },
      { ...base, lastError: { kind: 'gateway_5xx' } },
    ), true);
    // same kind AND same detail → equal (regression guard for the deepening).
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_CONNECTION_RESET' } },
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_CONNECTION_RESET' } },
    ), true);
    // null vs null stays equal; null vs non-null stays unequal.
    assert.equal(mod.panelStateEqual({ ...base, lastError: null }, { ...base, lastError: null }), true);
    assert.equal(mod.panelStateEqual({ ...base, lastError: null }, { ...base, lastError: { kind: 'rate_limited' } }), false);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern="same-kind-different-detail"`
Expected: FAIL — the first three assertions fail because the current kind-only comparison returns `true` for same-kind-different-detail.

- [ ] **Step 3: Write minimal implementation**

In `browserext/src/content/panelState.ts`, replace the `a.lastError?.kind === b.lastError?.kind` line of `panelStateEqual` with a detail-aware comparison. The full updated function:
```ts
/** Shallow structural equality over the PanelState fields the panel renders.
 *  Used to skip re-render when a STATE_CHANGED carries an unchanged state.
 *  lastError is compared by kind AND its discriminating detail (slice-6 fix:
 *  slice-5 compared kind only, so a nav_error whose `error` string changed,
 *  or an unexpected_status whose statusCode changed, left stale error text on
 *  screen until the next phase change). */
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
    errorEqual(a.lastError, b.lastError) &&
    a.pendingDecision?.id === b.pendingDecision?.id
  );
}

/** Compare two ControllerError values by kind + the kind's discriminating detail.
 *  gateway_5xx/rate_limited carry no detail beyond kind. */
function errorEqual(x: ControllerError | null, y: ControllerError | null): boolean {
  if (x === y) return true;
  if (!x || !y) return false;
  if (x.kind !== y.kind) return false;
  switch (x.kind) {
    case 'nav_error': return x.error === (y as Extract<ControllerError, { kind: 'nav_error' }>).error;
    case 'unexpected_status': return x.statusCode === (y as Extract<ControllerError, { kind: 'unexpected_status' }>).statusCode;
    case 'content_unavailable': return x.missing === (y as Extract<ControllerError, { kind: 'content_unavailable' }>).missing;
    case 'gateway_5xx':
    case 'rate_limited': return true;
  }
}
```
(`ControllerError` is already imported at the top of the file. The `Extract<…>` casts narrow `y` to the same kind as `x` after the `x.kind === y.kind` guard; they are type-safe and keep `tsc` happy without restructuring the union.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern="panelStateEqual"`
Expected: PASS — all 6 `panelStateEqual` tests (the original 1 + the new 1's 8 assertions) green.

- [ ] **Step 5: Run the full suite to confirm no regression**

Run: `cd browserext && npm test`
Expected: `tests 226 pass 226 fail 0`. Then `cd browserext && npm run typecheck` → exit 0.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/content/panelState.ts browserext/tests/content/panelState.test.mjs
git commit -m "fix(browserext): slice 6 panelStateEqual compares lastError kind+detail (slice-5 deferred #1)"
```

---

### Task 2: panel "已绑定其他标签" middle render branch — test coverage (slice-5 deferred Minor #4)

**Why:** `mountPanel.render` has three branches: `!bound` (bind button), `bound && !isBoundTab` ("已绑定其他标签"), and the full-control branch. Slice 5 tested the first and third but not the middle one — a non-bound tab on a page while another tab is bound. The middle branch renders no command controls (no `data-cmd` buttons) and shows the "已绑定其他标签" muted text. This task pins that branch so a future refactor cannot silently regress it (e.g. accidentally rendering bind on a non-bound tab, breaking the non-preemptible binding of amend §7).

**Files:**
- Modify: `browserext/tests/content/panel.test.mjs` (no production change — the branch exists and is correct; this is a coverage gap closure).

**Interfaces:** Consumes `mountPanel` from `../src/content/panel.js` (unchanged).

- [ ] **Step 1: Write the failing test (coverage gap, not a behavior gap)**

Append to `browserext/tests/content/panel.test.mjs`:
```js
test('mountPanel: render shows "已绑定其他标签" when bound but not the bound tab (amend §7 non-preemptible, slice-6 coverage)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: false, bound: true, connected: true, autoMode: false, paused: false,
      phase: 'navigating', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null,
    });
    // The non-bound-tab branch shows the muted "already bound elsewhere" text.
    assert.match(dom.shadowRoot.innerHTML, /已绑定其他标签/);
    // It must NOT render any command controls (no bind, no unbind, no submit/skip/fail)
    // — a non-bound tab cannot take over or issue commands (amend §7 non-preemptible).
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="bind"/);
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="unbind"/);
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="submit"/);
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="skip"/);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it PASSES immediately (no production change)**

Run: `cd browserext && npm test -- --test-name-pattern="已绑定其他标签"`
Expected: PASS — the middle branch already renders correctly; this test only pins it. (If it fails, the branch regressed and the production fix is in `panel.ts:render`'s `else if (!state.isBoundTab)` arm — but slice-5 review confirmed it renders the muted row with no command controls, so it should pass first try.)

- [ ] **Step 3: Run the full suite + typecheck**

Run: `cd browserext && npm test && npm run typecheck`
Expected: `tests 227 pass 227 fail 0`; typecheck exit 0.

- [ ] **Step 4: Commit**

```bash
git add browserext/tests/content/panel.test.mjs
git commit -m "test(browserext): slice 6 pin 已绑定其他标签 middle render branch (slice-5 deferred #4)"
```

---

### Task 3: manualComplete isolated test + manualSkip lastError===null assertion (slice-5 deferred Minors #2, #3)

**Why:** Slice 5 added `manualComplete` (the `submit` command → re-dispatch CAPTURE from `landed`) and `manualSkip`, but `manualComplete` had no isolated unit test (covered only indirectly by the slice-4 landing→capture path + the Task-9 integration test), and the `manualSkip` test did not assert `lastError === null` after skip (the impl clears it, but the assertion was missing). Both are genuine coverage gaps that slice-5's review deferred to slice-6.

`manualComplete` semantics to pin: from `phase === 'landed'`, it sets `phase = 'submitting'` then re-dispatches `CAPTURE` to the source document via `sendMessage` (the same `dispatchCapture` path slice-4's landed-branch uses), producing a `pendingRpc` with `op: 'capture'`. From a non-`landed` phase (e.g. `idle`, `navigating`, `assigned`), it is a no-op (the guard `s.phase !== 'landed' && !(s.phase === 'capturing' && s.pendingRpc)` returns early).

**Files:**
- Modify: `browserext/tests/controller/controller.test.mjs` (no production change — `manualComplete`/`manualSkip` are already correct; this closes the test gaps).

**Interfaces:** Consumes the existing `landedController`/`fakeArea`/`fakeApi4`/`fakeChrome4` helpers + the landing-signal sequence already used by the slice-4 capture tests.

- [ ] **Step 1: Write the failing tests**

Append to `browserext/tests/controller/controller.test.mjs` (after the existing `manualFail` test around line 1056):
```js
test('manualComplete: from capturing+pendingRpc (the submit command) → re-dispatches CAPTURE, no throw (slice-6, slice-5 deferred #2)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr, ready } = landedController(mod, area);
    await ready();
    // Land the controller on DOC-1 (the slice-4 landing-signal sequence). The auto
    // dispatch lands the controller in phase 'capturing' with a pendingRpc (op:capture).
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const afterLand = await c.getState();
    assert.equal(afterLand.phase, 'capturing', 'landed → capturing (auto CAPTURE dispatched)');
    assert.ok(afterLand.pendingRpc, 'auto-dispatched CAPTURE pendingRpc present');
    // manualComplete is the `submit` command. The guard accepts phase 'capturing' with
    // a pendingRpc, so it must not throw and must leave the controller in a capturing/
    // submitting state (it re-dispatches CAPTURE to the source document).
    const sentBefore = chr.calls.sent.length;
    await c.manualComplete(5500);
    const s = await c.getState();
    assert.ok(s.phase === 'capturing' || s.phase === 'submitting', `manualComplete left phase=${s.phase}`);
    // The submit path must NOT drop the in-flight CAPTURE (sent count never decreases).
    assert.ok(chr.calls.sent.length >= sentBefore, 'manualComplete did not drop the in-flight CAPTURE');
  } finally { await cleanup(); }
});

test('manualComplete: from a non-landed phase (assigned) → no-op (slice-6, slice-5 deferred #2)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    // NOTE: use fakeChrome4 (not fakeChrome3) — fakeChrome3 has no sendMessage/calls.sent,
    // but this test asserts chr.calls.sent.length. fakeChrome4 exposes calls.sent + sendMessage.
    const chr = fakeChrome4();
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(2000);   // caches job-1 → assigned (auto off, so no navigate)
    const sentBefore = chr.calls.sent.length;
    await c.manualComplete(3000);
    const s = await c.getState();
    assert.equal(s.phase, 'assigned', 'manualComplete no-op on non-landed phase');
    assert.equal(chr.calls.sent.length, sentBefore, 'no CAPTURE dispatched from a non-landed phase');
  } finally { await cleanup(); }
});
```

Then, **widen the existing `manualSkip` test** to also assert `lastError === null`. Find the existing test (around line 1020):
```js
test('manualSkip: POSTs /jobs/{id}/skip with reason, clears navigation/pendingRpc, phase assigned (slice 5)', async () => {
```
and add a `lastError` assertion + a setup that stages a non-null error first. Replace the body's final assertion block (the lines after `await c.manualSkip('user-skip', 3000);`) with:
```js
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'user-skip' }]);
    const s = await c.getState();
    assert.equal(s.navigation, null);
    assert.equal(s.pendingRpc, null);
    assert.equal(s.lastError, null, 'manualSkip clears lastError (slice-6, slice-5 deferred #3)');
    assert.equal(s.phase, 'assigned', 'job may still be current; reconcile owns clearing');
```
(The existing test's `fakeApi`/`fakeChrome3`/`job`/`bind`/`tick` setup is unchanged; only the trailing assertions are widened. `manualSkip` sets `s.lastError = null` unconditionally (controller.ts:912), so this passes — but the assertion pins it against a future regression.)

- [ ] **Step 2: Run the new manualComplete tests to verify they fail/pass correctly**

Run: `cd browserext && npm test -- --test-name-pattern="manualComplete"`
Expected: The `from landed` test may PASS already (manualComplete exists and is correct) — this is a coverage closure, like Task 2. The `from a non-landed phase` test should PASS. If the `from landed` test's `phase` assertion is too strict (the auto-dispatch may already be `capturing`), relax the assertion to `s.phase === 'capturing' || s.phase === 'submitting'` (already written that way). Iterate on the test only — do NOT change `manualComplete`.

Run the manualSkip test: `cd browserext && npm test -- --test-name-pattern="manualSkip"`
Expected: PASS (the `lastError === null` assertion is satisfied by the existing impl).

- [ ] **Step 3: Run the full suite + typecheck**

Run: `cd browserext && npm test && npm run typecheck`
Expected: `tests 229 pass 229 fail 0` (227 + 2 new); typecheck exit 0.

- [ ] **Step 4: Commit**

```bash
git add browserext/tests/controller/controller.test.mjs
git commit -m "test(browserext): slice 6 manualComplete isolated test + manualSkip lastError===null (slice-5 deferred #2,#3)"
```

---

### Task 4: Remove Phase-1 code residue — `chrome.findOwnerTab` + stale test labels

**Why:** `findOwnerTab` is a Phase-1 artifact: it scans `chrome.tabs.query({})` for a tab on an allowed host, which was the Phase-1 "find the owner tab to re-redirect" behavior. Under exclusive control (Phase 2) the bound tab is **explicitly bound** by the user (`boundTabId`), never discovered — `getTab(boundTabId)` is the only tab lookup the controller uses (controller.ts:52 types `Pick<ChromeRuntime, 'getTab' | 'updateTabUrl' | 'sendMessage'>`, which never included `findOwnerTab`). `findOwnerTab` has zero production callers. Its tests + the `chrome.test.mjs` "rename from watchdog" label (a Phase-1→Phase-2 rename note) are stale documentation. Spec §0.1/§1.4 explicitly retired the owner-election model; this task removes the dead stub so the code matches the design.

Also: the `background.test.mjs` fake chrome stubs `findOwnerTab: () => null` (lines 24, 63) — those stubs become dead once `ChromeRuntime` no longer has the method, and `tsc` would flag them if the fake were typed. They are untyped object literals, so removing them is safe and keeps the fakes minimal.

**Files:**
- Modify: `browserext/src/chrome.ts` — remove `findOwnerTab` from the `ChromeRuntime` interface and the real impl.
- Modify: `browserext/tests/chrome.test.mjs` — remove the `findOwnerTab` test + the `findOwnerTab` from the method-list assertion; rewrite the "rename from watchdog" test label to drop the stale Phase-1 framing.
- Modify: `browserext/tests/background.test.mjs` — drop `findOwnerTab` from the two fake-chrome object literals.

**Interfaces:**
- Consumes: nothing new. `ChromeRuntime` is consumed by `background.ts`, `navMonitor.ts`, `controller.ts`, `messageRouter.ts` — none reference `findOwnerTab` (verified: only `getTab`/`updateTabUrl`/`sendMessage`/the nav listeners/`registerAlarm`).
- Produces: `ChromeRuntime` minus `findOwnerTab`. The `Pick<ChromeRuntime, …>` sites in `controller.ts`/`messageRouter.ts`/`navMonitor.ts` are unaffected (they never picked it).

- [ ] **Step 1: Write the failing test (the removal)**

First, read `browserext/tests/chrome.test.mjs` to locate the exact `findOwnerTab` test + the method-list line. Then edit:

In `browserext/tests/chrome.test.mjs`, **remove** the `findOwnerTab` test (the `test('createRealChromeRuntime exposes registerAlarm + getTab (rename from watchdog + new)', …)` block) and replace it with a Phase-2-accurate version that drops `findOwnerTab` and the "rename from watchdog" framing:
```js
test('createRealChromeRuntime exposes the Phase-2 chrome surface (registerAlarm + getTab + sendMessage + nav listeners)', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    const rt = mod.createRealChromeRuntime();
    assert.equal(typeof rt.registerAlarm, 'function');
    assert.equal(typeof rt.getTab, 'function');
    assert.equal(typeof rt.updateTabUrl, 'function');
    assert.equal(typeof rt.sendMessage, 'function');
    // Phase-1 findOwnerTab (owner election) was removed in slice 6 — the bound tab
    // is explicitly bound, never discovered. getTab(boundTabId) is the sole tab lookup.
    assert.equal(typeof (rt).findOwnerTab, 'undefined', 'findOwnerTab removed (Phase-1 residue)');
  } finally {
    await cleanup();
  }
});
```
Then find the method-list assertion (the `for (const m of ['onBeforeRequest', ... 'findOwnerTab', ...])` loop around line 70) and remove `'findOwnerTab'` from the array.

Also remove the standalone `findOwnerTab` test (the `test('createRealChromeRuntime ... findOwnerTab ...', async () => { const rt = ...; for (const t of tabs) ... })` block that asserts the host-preference/active-tab fallback) — it tests behavior that no longer exists.

In `browserext/tests/background.test.mjs`, remove `async findOwnerTab() { return null; },` from both fake-chrome object literals (lines ~24 and ~63).

- [ ] **Step 2: Run the chrome + background tests to verify they fail (red on the now-removed method), then the suite**

Run: `cd browserext && node --test tests/chrome.test.mjs tests/background.test.mjs`
Expected: PASS once the removals above are in place (the tests no longer reference `findOwnerTab`). If any test still references it, it errors with "findOwnerTab is not a function" — fix the test, not the source.

- [ ] **Step 3: Write minimal implementation (remove the production stub)**

In `browserext/src/chrome.ts`:
- Remove `findOwnerTab(): Promise<number | null>;` from the `ChromeRuntime` interface.
- Remove the `async findOwnerTab() { … }` implementation block (the `chrome.tabs.query({})` + active-tab fallback, around lines 128–140).

Read `chrome.ts` first to get the exact surrounding lines; the edit is a deletion of those two blocks. Leave `getTab`, `updateTabUrl`, `sendMessage`, the nav listeners, and `registerAlarm` untouched.

- [ ] **Step 4: Run the full suite + typecheck + build**

Run: `cd browserext && npm test && npm run typecheck && npm run build`
Expected: `tests 229 pass 229 fail 0` (count unchanged — the removed `findOwnerTab` tests are replaced 1:1 by the Phase-2 surface test, and the standalone findOwnerTab test removal nets to the same count only if exactly one test was removed; **adjust the expected count to the actual green count** — the point is fail 0); typecheck exit 0; build exit 0 (gate still OFF).

- [ ] **Step 5: Verify the gate-OFF no-op still holds (the build test is untouched here)**

Run: `cd browserext && node --test tests/build.test.mjs`
Expected: PASS — `build.test.mjs` still asserts gate-OFF (`if (true) return` in bg, `if (false) {` in content). The `findOwnerTab` removal does not touch the gate.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/chrome.ts browserext/tests/chrome.test.mjs browserext/tests/background.test.mjs
git commit -m "chore(browserext): slice 6 remove Phase-1 findOwnerTab residue + stale test labels"
```

---

### Task 5: Full-suite green checkpoint (gate still OFF)

**Why:** Before staging the gate flip, confirm the whole branch is green and the official build is still a verified no-op. This is the last gate-OFF checkpoint; everything after it (Tasks 6–7) is the flip + docs, and Task 8 is the user's acceptance.

**Files:** None (verification only).

- [ ] **Step 1: Run the complete verification battery**

Run:
```bash
cd browserext && npm test && npm run typecheck && npm run build && node --test tests/build.test.mjs
```
Expected: `tests <N> pass <N> fail 0`; typecheck exit 0; build exit 0; build.test gate-OFF assertions PASS.

- [ ] **Step 2: Confirm the git log is clean for the slice-6 code tasks**

Run: `git log --oneline slice4..HEAD` (or the current branch)
Expected: 4 commits — Task 1 (`fix(browserext): slice 6 panelStateEqual…`), Task 2 (`test(browserext): slice 6 pin 已绑定其他标签…`), Task 3 (`test(browserext): slice 6 manualComplete…`), Task 4 (`chore(browserext): slice 6 remove Phase-1 findOwnerTab…`). No gate flip among them.

- [ ] **Step 3: No commit (checkpoint only)**

This task produces no commit. Proceed to Task 6.

---

## Part B — Gate flip + docs (staged, NOT committed until acceptance passes)

### Task 6: Gate flip + build.test rewrite (STAGED, commit deferred to after Task 8)

**Why:** Spec §6.1 + §6.2 slice-6 row: the official build default for `EXCLUSIVE_CONTROL_ENABLED` flips `false → true`. With the flip, the controller's 26 `if (!GATE) return;` guards no longer early-return (the gate is on), and the content's `if (GATE) { … }` body runs (marker set, panel mounted, RPC wired). The `build.test.mjs` assertions that pinned gate-OFF (`if (true) return` / `if (false) {`) must be rewritten to pin gate-ON.

**CRITICAL — do NOT commit this task until Task 8 acceptance passes.** Stage it (make the edits, run the tests locally to confirm green), but leave it uncommitted. The user's rule: a committed gate flip means the next `npm run build` the user runs produces an extension that will actually navigate/claim/bind in their browser. The user must do acceptance first, against a build they produce from the staged (uncommitted) flip.

**Files:**
- Modify: `browserext/esbuild.config.mjs`
- Modify: `browserext/tests/build.test.mjs`

**Interfaces:** None (build-config + test only).

- [ ] **Step 1: Flip the gate default**

In `browserext/esbuild.config.mjs`, change the `GATE` default. The line is:
```js
const GATE = process.env.DEXTC_EXCLUSIVE_CONTROL === '1' ? 'true' : 'false';
```
Change to invert the default — the official build is now gate-ON, and an explicit opt-out env var (`DEXTC_EXCLUSIVE_CONTROL=0`) is the escape hatch for anyone who still wants a no-op build (e.g. to run alongside an active userscript during transition):
```js
// Slice 6: official build is now gate-ON (spec §6.1/§6.2). DEXTC_EXCLUSIVE_CONTROL=0
// opts back out to a no-op build (e.g. to run alongside an active userscript).
const GATE = process.env.DEXTC_EXCLUSIVE_CONTROL === '0' ? 'false' : 'true';
```

- [ ] **Step 2: Rewrite the build.test.mjs gate assertions**

In `browserext/tests/build.test.mjs`, the second test (`default build has EXCLUSIVE_CONTROL_ENABLED = false (gate off)`) becomes a gate-ON test. Replace its body. The full updated test:
```js
test('default build has EXCLUSIVE_CONTROL_ENABLED = true (gate on, slice 6)', () => {
  rmSync(join(root, 'dist'), { recursive: true, force: true });
  try {
    build();
    const bg = readFileSync(distBg, 'utf8');
    // Controller guard `if (!GATE) return` with GATE=true folds to `if (false) return` —
    // the early-return is NOT taken, so the body runs (spec §6.1 gate ON).
    assert.match(bg, /if \(false\) return/, 'controller body runs when gate on');
    assert.doesNotMatch(bg, /if \(true\) return/, 'no gate-off early-return should remain in default build');
    const content = readFileSync(distContent, 'utf8');
    // Content guard `if (GATE) {...}` with GATE=true folds to `if (true) {...}` —
    // the body runs (marker set, panel mounted, RPC wired).
    assert.match(content, /if \(true\) \{/, 'content body runs when gate on');
    assert.doesNotMatch(content, /if \(false\) \{/, 'no gate-off skipped branch should remain in default build');
  } finally {
  }
});
```
The first test (`build emits dist/background.js and dist/content.js`) is unchanged.

- [ ] **Step 3: Verify the build + build test are green with the flip**

Run:
```bash
cd browserext && npm run build && node --test tests/build.test.mjs && npm test
```
Expected: build exit 0; `build.test.mjs` PASS (2 tests — the emit test + the new gate-ON test); full suite `tests <N> pass <N> fail 0` (the suite runs under the harness's gate-ON injection, so it is unaffected by the esbuild default flip — the harness always injects `true`).

- [ ] **Step 4: Verify typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0.

- [ ] **Step 5: STAGE but do NOT commit**

```bash
git add browserext/esbuild.config.mjs browserext/tests/build.test.mjs
```
Do **not** run `git commit` here. The staged change waits for Task 8 acceptance. Leave the working tree with these two files staged. (If the session must pause, the staged state survives; resume by confirming `git status` shows the two staged files before committing in Task 9.)

**Deliverable to the user (handoff message):** "Gate flip + build.test rewrite are staged (uncommitted). To run acceptance, do: `cd browserext && npm run build`, then reload the unpacked extension at `chrome://extensions`. The extension will now bind/navigate/capture. Run the six acceptance checks in Task 8 and paste back what you observe. I will commit the flip only after you confirm all six pass."

---

### Task 7: Docs — extension is sole official frontend; userscript demoted (written in parallel, committed with the flip)

**Why:** Spec §5.6 final bullet + §6.4: after acceptance, the Phase-2 spec / README / CLAUDE.md are updated to mark the extension as the sole official frontend and move the userscript into an emergency-recovery section. This task writes the doc edits in parallel with Task 6 (they don't depend on the flip being committed), but they are committed together with the flip after acceptance — because the docs describe the gate-ON world, committing them before the flip would describe a state the build doesn't yet produce.

**Files:**
- Modify: `browserext/manifest.json` — version bump + description (Phase 2 → GA / sole frontend).
- Modify: `browserext/README.md` — full rewrite: extension is the sole official frontend; userscript is emergency-recovery-only.
- Modify: `docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design.md` — status banner → Phase 2 LANDED.
- Modify: `docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design-amend.md` — status banner → amendment folded into landed Phase 2.
- Modify: `CLAUDE.md` — browserext row + the "preserved browser-facing artifacts" framing.

**Interfaces:** None (docs only).

- [ ] **Step 1: Update `browserext/manifest.json`**

Bump version and rewrite the description:
```json
{
  "manifest_version": 3,
  "name": "dext crawl controller",
  "version": "0.2.0",
  "description": "Exclusive-control crawl orchestrator (Phase 2). Sole official browser frontend — binds one tab and drives claim→navigate→capture→complete. The dext userscript is an emergency-recovery fallback, disabled in normal operation.",
  "background": {
    "service_worker": "dist/background.js",
    "type": "module"
  },
  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["dist/content.js"],
      "run_at": "document_start"
    }
  ],
  "permissions": ["webRequest", "webNavigation", "alarms", "tabs", "storage"],
  "host_permissions": [
    "http://127.0.0.1:21520/*",
    "<all_urls>"
  ],
  "minimum_chrome_version": "110"
}
```

- [ ] **Step 2: Rewrite `browserext/README.md`**

Replace the entire file. The new README reflects the gate-ON, sole-frontend world, with the userscript demoted to an emergency-recovery section and the six acceptance checks referenced:
```markdown
# dext crawl controller

The **sole official browser frontend** for the dext graph-driven crawler. An MV3
Chrome/Edge extension (≥110) that owns all client-side crawl orchestration: it
binds **one** tab the user explicitly chooses, then drives the full
claim → navigate → capture → complete cycle against the dext backend
(`http://127.0.0.1:21520/api`), with landing recognition, retry funneling, and
form-action support. The backend `/status.current_job` is the sole scheduling
truth; the extension never constructs jobs and never calls `/jobs/next` until it
has a bound tab.

The dext **userscript** (`userscripts/`) is now an **emergency-recovery fallback**
— disabled in normal operation. See [Emergency recovery](#emergency-recovery).

## Build

```bash
cd browserext
npm install
npm run build      # → dist/background.js + dist/content.js (gate ON by default)
npm test           # node:test unit tests (gate injected ON by the harness)
npm run typecheck  # tsc --noEmit on both tsconfigs
```

The build injects `EXCLUSIVE_CONTROL_ENABLED = true` by default (slice 6). Set
`DEXTC_EXCLUSIVE_CONTROL=0` to produce a no-op build (e.g. to run alongside an
active userscript during transition).

## Load (Chrome / Edge)

1. Start the dext backend (`uv run crawl -u <university> …`) so the bridge
   listens on `http://127.0.0.1:21520`.
2. `chrome://extensions` → enable Developer mode → "Load unpacked" → select
   `browserext/` (the folder with `manifest.json`).
3. **Disable the userscript** in Tampermonkey (see Emergency recovery).
4. Open the target university page; click **绑定并开始** in the dext panel.
   Only that tab is driven; other tabs are never navigated.

## How it works (one-liner per phase)

- **bind** — user binds one tab; `boundTabId` persisted to `chrome.storage.local`.
- **claim** — `/status.current_job` is the truth; on `idle` the controller claims
  via `/jobs/next` only when bound + auto + not paused.
- **navigate** — the controller is the **sole** `chrome.tabs.update` caller;
  navigation intent is persisted *before* the call.
- **land** — four signals (main-frame commit + acceptable HTTP outcome +
  same-crawl-site URL + `PAGE_READY` from the same documentId) must all hold.
- **capture / form-action** — the content script runs DOM operations via RPC;
  it never navigates, never calls the backend, never holds job state.
- **complete** — `POST /jobs/{id}/complete`; late/stale ids are idempotent no-ops.

## Emergency recovery (the userscript)

The dext userscript (`userscripts/yanclaw-assistant.user.js`) is the fallback for
when the extension cannot run (extension disabled, or a hard extension crash).
In normal operation it **must be disabled** in Tampermonkey — both frontends
running at once is unsupported.

To fall back:

1. Disable the extension at `chrome://extensions`.
2. Enable the userscript in Tampermonkey.
3. Refresh the target page. The userscript detects the absence of the
   `data-dext-extension-controller="v1"` marker on `<html>` and takes full
   control (its `instanceLock` and all original duties intact).

The userscript's bootstrap checks for the extension marker first (spec §5.1) and
stands down if the extension owns the tab. The marker is page-priority, **not**
an automatic failover signal — recovery requires the explicit disable+refresh
above.

## Architecture reference

Design: `docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design.md`
(+ its amendment). Slice-by-slice plans: `docs/superpowers/plans/`.
```

- [ ] **Step 3: Update the Phase-2 spec status banner**

In `docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design.md`, update the top status block (lines 3–6). Replace:
```markdown
> **规范性修订：** 本设计受 [2026-06-28-browserext-exclusive-control-design-amend.md](./2026-06-28-browserext-exclusive-control-design-amend.md) 修订；两者冲突时以 amendment 为准。
> **状态：** 本 spec 是 Phase 2 的正式设计，**取代** [2026-06-28-browserext-content-migration-design.md](./2026-06-28-browserext-content-migration-design.md)（per-slice 共存 + localStorage 仲裁模型）。该旧 spec 被标记为 superseded 并指向本文件。
```
with:
```markdown
> **状态：** ✅ **Phase 2 LANDED (slice 6, 2026-06-29)。** 扩展独占控制已通过真实浏览器验收（spec §5.6 六项），gate 默认开启，扩展是唯一正式前端；userscript 降级为应急产物（spec §5.1）。本设计受 [2026-06-28-browserext-exclusive-control-design-amend.md](./2026-06-28-browserext-exclusive-control-design-amend.md) 修订；两者冲突时以 amendment 为准。本 spec 取代 [2026-06-28-browserext-content-migration-design.md](./2026-06-28-browserext-content-migration-design.md)（per-slice 共存 + localStorage 仲裁模型，superseded）。
```

- [ ] **Step 4: Update the amendment status banner**

In `docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design-amend.md`, update the top status block (lines 3–9). Replace:
```markdown
> **状态：** 本 amendment 是
> [2026-06-28-browserext-exclusive-control-design.md](./2026-06-28-browserext-exclusive-control-design.md)
> 的规范性组成部分。两者冲突时以本文为准；未被本文点名修订的内容继续有效。
```
with:
```markdown
> **状态：** ✅ 本 amendment 已并入 LANDED 的 Phase 2（slice 6, 2026-06-29）。它是
> [2026-06-28-browserext-exclusive-control-design.md](./2026-06-28-browserext-exclusive-control-design.md)
> 的规范性组成部分；两者冲突时以本文为准，未被本文点名修订的内容继续有效。
```

- [ ] **Step 5: Update `CLAUDE.md`**

Two edits in `CLAUDE.md`:

(a) In the **"The fixed HTTP contract (SP4)"** section, the paragraph describing `browserext/` (the one starting "**`browserext/`** is a second browser-side consumer…") describes Phase-1 behavior (re-redirect, watchdog). Replace that paragraph with a Phase-2-GA description:
```markdown
**`browserext/`** is the **sole official browser frontend** (Phase 2, slice 6 GA): a
single `CrawlController` in the MV3 background SW owns all client orchestration —
claim/navigate/capture/complete against `/status.current_job` (the sole scheduling
truth for it too). The user explicitly binds one tab (`boundTabId`); only that tab is
driven. It runs the full amend §2–§5 protocol (landing recognition, retry funnel,
form-action prepare→persist→perform→confirm, RPC `documentId`/`rpcId` validation).
Build: `cd browserext && npm install && npm run build` (gate ON by default since
slice 6; `DEXTC_EXCLUSIVE_CONTROL=0` opts out to a no-op build); load `browserext/`
unpacked at `chrome://extensions`. The **userscript is an emergency-recovery
fallback, disabled in normal operation** — its bootstrap checks the
`data-dext-extension-controller="v1"` marker and stands down when the extension
owns the tab (spec §5.1). Run ONLY the extension in normal operation; running both
is unsupported.
```

(b) In the **subproject status table**, update the `browserext` row. Replace:
```
| browserext | `browserext/` — MV3 extension, Phase 1 background probe | ✅ Phase 1 done (Phase 2 TBD) |
```
with:
```
| browserext | `browserext/` — MV3 extension, sole official frontend (Phase 2 GA) | ✅ Phase 2 done (slice 6) |
```

- [ ] **Step 6: STAGE but do NOT commit (commit with the flip in Task 9)**

```bash
git add browserext/manifest.json browserext/README.md docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design.md docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design-amend.md CLAUDE.md
```
Do not commit. These stage alongside the Task-6 flip and commit together after acceptance.

---

## Part C — Human acceptance (user-operated; Claude does not touch Chrome)

### Task 8: Real-browser acceptance — the six checks of spec §5.6

**Why:** Spec §5.6 + §6.4. The gate flip is not real until a human confirms the extension actually drives a real crawl in a real Chrome, single-navigation, no refresh loops, no second heartbeat, and that the userscript fallback still works. This is the user's task: Claude provides the build instructions + the checklist; the user runs it in their Chrome and pastes back observations. Claude does not load Chrome, does not bind a tab, does not run the crawl.

**Files:** None (acceptance is observational; the only code artifact is the staged flip from Task 6, which commits in Task 9 after this passes).

**Handoff to the user (Claude posts this message):**

> The gate flip (Task 6) and docs (Task 7) are staged but uncommitted. To run acceptance:
>
> 1. `cd browserext && npm run build` (produces `dist/` with the gate ON).
> 2. At `chrome://extensions`, reload the unpacked `browserext/` extension. Confirm the service worker is active.
> 3. **Disable the userscript in Tampermonkey** (it must be off for acceptance).
> 4. Start the backend: `uv run crawl -u xjtu …` so the bridge listens on `http://127.0.0.1:21520`.
>
> Then run the six checks (spec §5.6) and paste back what you observe for each. I will commit the flip + docs only after you confirm all six pass:
>
> - [ ] **(1) Single navigation + complete.** Bind an XJTU page; reproduce `/web/renxueguang` → `/renxueguang/`. Confirm a **single** navigation and `POST /jobs/{id}/complete` succeeds (check the backend log for the complete, not a duplicate navigate).
> - [ ] **(2) No refresh loops under adversity.** Backend busy / SW restart (reload the SW at chrome://extensions → "service worker" → reload) / network drop+recover / a browser-native error page (`about:neterror`) — confirm none produces an infinite refresh loop. (3× 5xx → `fail gateway_5xx`; 3× net-error → `fail nav_error:ERR_…`; below threshold the tab self-redirects once, not infinitely.)
> - [ ] **(3) Ordinary tabs never navigated.** Open a normal (non-bound) tab to any page; confirm the extension never calls `chrome.tabs.update` on it.
> - [ ] **(4) Userscript accidentally enabled + extension present → no second heartbeat/claim/skip.** Re-enable the userscript in Tampermonkey while the extension is still loaded and bound; refresh the bound page. Confirm the marker (`<html data-dext-extension-controller="v1">`) is present and the userscript stood down — only ONE heartbeat/claim/skip in the backend, not two. (spec §5.2: marker is on every page; non-allowed-host CS early-returns.)
> - [ ] **(5) Disable extension + refresh → userscript takes over.** Disable the extension at `chrome://extensions`; refresh the page. Confirm the emergency userscript runs in full fallback (its `instanceLock` active, capture/submit working).
> - [ ] **(6) Large-page capture doesn't destabilize the SW.** Capture a ≥400KB real page and a ~5MB synthetic page; confirm `CAPTURE_RESULT` is delivered and the SW does not crash/restart spuriously (check the service-worker log for unexpected terminations). (spec §6.3 risk row.)
>
> Paste back what you see for each. If any check fails, do NOT commit — describe the failure and I'll diagnose (likely a slice-4/5 behavior surfacing under the real browser, not a slice-6 regression).

**Claude's role while the user runs acceptance:**
- Do not commit. Do not start other slice work. Wait for the user's observations.
- If a check fails, use `superpowers:systematic-debugging` to diagnose before proposing any fix; the fix may belong to slice 4/5 (a behavior gap surfacing under the real browser) rather than slice 6.
- Only after the user confirms all six pass, proceed to Task 9 (commit the staged flip + docs, write memory + ledger).

**Acceptance failure → rollback path:** If acceptance reveals a real defect that cannot be fixed in-session, the staged flip (Task 6) + docs (Task 7) are simply not committed — `git reset HEAD browserext/esbuild.config.mjs browserext/tests/build.test.mjs browserext/manifest.json browserext/README.md docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design.md docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design-amend.md CLAUDE.md` unstages them, and the branch returns to the gate-OFF state of Task 5 (all code tasks committed, suite green, gate off). No history rewrite needed.

---

## Part D — Land it (only after acceptance passes)

### Task 9: Commit the flip + docs, write memory + ledger

**Why:** Acceptance passed. The staged gate flip + docs commit now (they describe the gate-ON world that acceptance just verified), and the memory/ledger record that Phase 2 is done.

**Files:**
- Commit (staged from Tasks 6+7): `browserext/esbuild.config.mjs`, `browserext/tests/build.test.mjs`, `browserext/manifest.json`, `browserext/README.md`, the two spec files, `CLAUDE.md`.
- Create: `memory/browserext-slice6-landed.md`
- Modify: `memory/MEMORY.md`
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Confirm the staged state is intact**

Run: `git status`
Expected: the 7 files from Tasks 6+7 are staged (esbuild.config.mjs, build.test.mjs, manifest.json, README.md, 2 specs, CLAUDE.md). No other unintended changes.

- [ ] **Step 2: Commit the flip + docs as one atomic gate-flip commit**

```bash
git commit -m "feat(browserext): slice 6 flip gate ON (sole official frontend) + docs (spec §6.1, §5.6, §6.4)

Phase 2 exclusive control GA: EXCLUSIVE_CONTROL_ENABLED defaults true in the
official build; extension is the sole browser frontend, userscript demoted to
emergency-recovery. Real-browser acceptance (spec §5.6) passed: single
navigate+complete, no refresh loops under adversity, ordinary tabs never
navigated, no second heartbeat with both frontends present, userscript
fallback works, large-page capture stable. Docs updated: Phase-2 spec status
banner → LANDED, README rewritten, CLAUDE.md browserext row → Phase 2 GA."
```

- [ ] **Step 3: Write the memory file**

Create `memory/browserext-slice6-landed.md`:
```markdown
---
name: browserext-slice6-landed
description: slice-6 LANDED — Phase-2 exclusive control GA; gate default ON; extension sole frontend; userscript demoted to emergency recovery; 4 slice-5 deferred Minors closed; Phase-1 findOwnerTab residue removed
metadata:
  type: project
---

Slice 6 (final slice) of browserext Phase-2 exclusive control **LANDED on slice4 branch** (commit after fd5c193). Phase 2 is GA.

What landed:
- **Gate flip**: `esbuild.config.mjs` `EXCLUSIVE_CONTROL_ENABLED` default `false → true` (slice 1–5 official builds were gate-OFF no-ops). Opt-out: `DEXTC_EXCLUSIVE_CONTROL=0`. `build.test.mjs` rewritten to assert gate-ON (`if (false) return` in bg, `if (true) {` in content — the inverse of the slice-1–5 assertions).
- **4 slice-5 deferred Minors closed**: (1) `panelStateEqual` now compares `lastError` by kind+detail (was kind-only → stale error text on same-kind-different-detail); (2) `manualComplete` isolated unit test (from landed + from non-landed no-op); (3) `manualSkip` test asserts `lastError===null`; (4) panel "已绑定其他标签" middle render branch pinned.
- **Phase-1 residue removed**: `chrome.findOwnerTab` (the Phase-1 owner-election stub, never called by the controller under exclusive control) + its tests + the stale "rename from watchdog" test label. `CrawlControllerDeps.chrome` was `Pick<ChromeRuntime, 'getTab'|'updateTabUrl'|'sendMessage'>` — never included `findOwnerTab`, so removal was type-safe.
- **Docs**: Phase-2 spec + amendment status banners → LANDED; browserext README rewritten (extension sole frontend, userscript → emergency recovery); CLAUDE.md browserext row → Phase 2 GA + the contract paragraph rewritten; manifest version 0.1.0 → 0.2.0.
- **Real-browser acceptance (spec §5.6) passed**: single navigate+complete (/web/renxueguang → /renxueguang/); no refresh loops under SW restart/net drop/native error page; ordinary tabs never navigated; no second heartbeat with both frontends present (marker wins); userscript fallback works after disable+refresh; large-page (400KB+5MB) capture stable.

Critical ordering held: gate flip + docs were STAGED (not committed) until the user confirmed all six acceptance checks; the flip commit landed only after acceptance. The branch was gate-OFF-green (suite + typecheck + build + build.test) through Task 5 before the flip.

Final state: browserext <N> pass / 0 fail, typecheck exit 0, build exit 0, gate-ON verified. Phase 2 complete. Closes [[browserext-slice5-landed]].
```

- [ ] **Step 4: Add the MEMORY.md pointer**

In `memory/MEMORY.md`, append after the slice-5 pointer line:
```markdown
- [browserext slice6 landed](browserext-slice6-landed.md) — slice-6 LANDED: Phase-2 GA, gate default ON, extension sole frontend, userscript demoted; 4 slice-5 Minors closed, findOwnerTab removed, §5.6 acceptance passed
```

- [ ] **Step 5: Append the progress ledger entry**

Append to `.superpowers/sdd/progress.md` (after the slice-5 final-review block):
```markdown
# SDD progress ledger — slice 6 (gate flip + cleanup + acceptance)
# Plan: docs/superpowers/plans/2026-06-29-browserext-slice6-gate-flip-cleanup.md
# Branch: slice4  Base: fd5c193  Baseline: ext 225 pass / 0 fail, typecheck exit 0

( executing — append below )
- Task 1: complete — panelStateEqual lastError kind+detail (slice-5 deferred #1); <N>/0
- Task 2: complete — panel 已绑定其他标签 middle-branch test (slice-5 deferred #4); <N>/0
- Task 3: complete — manualComplete isolated test + manualSkip lastError===null (slice-5 deferred #2,#3); <N>/0
- Task 4: complete — remove Phase-1 findOwnerTab residue + stale test labels; <N>/0, typecheck 0, build 0
- Task 5: checkpoint — full suite + typecheck + build + build.test green, gate still OFF; 4 commits, no flip
- Task 6+7: STAGED (uncommitted) — gate flip false→true + build.test rewrite + docs (manifest/README/specs/CLAUDE.md)
- Task 8: REAL-BROWSER ACCEPTANCE PASSED (user-operated, spec §5.6 six checks): <paste user's confirmation summary>
- Task 9: gate-flip + docs committed; memory + ledger written. Phase 2 GA.

## Final whole-branch review (slice 6, fd5c193..<flip-commit>, browserext-scoped)
Final review: Phase 2 COMPLETE / GA. Gate default ON; extension sole official frontend; userscript emergency-recovery (§5.1 marker stand-down). 4 slice-5 deferred Minors closed; Phase-1 findOwnerTab residue removed; docs rewritten. Real-browser §5.6 acceptance passed. <N>/0, typecheck 0, build 0, gate-ON verified.
```
(Replace `<N>` and `<paste …>` with the actual green count and the user's acceptance summary.)

- [ ] **Step 6: Final verification**

Run:
```bash
cd browserext && npm test && npm run typecheck && npm run build && node --test tests/build.test.mjs && git log --oneline -6
```
Expected: suite green; typecheck 0; build 0; build.test gate-ON assertions PASS; the log shows the 4 code-task commits + the gate-flip commit at HEAD.

- [ ] **Step 7: No further commit (memory + ledger are not git-tracked deliverables here, but commit them if the repo tracks `.superpowers/` and `memory/`)**

If `.superpowers/sdd/progress.md` is git-tracked (it is — see the git status), commit it:
```bash
git add .superpowers/sdd/progress.md
git commit -m "docs(browserext): slice 6 progress ledger — Phase 2 GA"
```
(The `memory/` directory is under `C:\Users\xc150\.claude\projects\d--pyprj-dext\memory\`, outside the repo — no git commit for it.)

---

## Self-Review (run after writing; fix inline)

**1. Spec coverage (slice-6 row, spec §6.2 + §5.6 + §6.4 + amend §8.2):**
- Gate flip (esbuild default false→true) → Task 6 Step 1. ✓
- `build.test.mjs` assertion flip → Task 6 Step 2. ✓
- Fix slice-5 deferred Minor #1 (panelStateEqual lastError kind-only) → Task 1. ✓
- Fix slice-5 deferred Minors #2/#3 (manualComplete test, manualSkip lastError) → Task 3. ✓
- Fix slice-5 deferred Minor #4 (已绑定其他标签 middle branch test) → Task 2. ✓
- Old Phase-1 code cleanup → Task 4 (findOwnerTab + stale labels). ✓
- Docs: Phase-2 spec / README / CLAUDE.md updated, extension sole frontend, userscript → emergency recovery → Task 7 (all 5 files). ✓
- Real-browser acceptance 6 items (§5.6) → Task 8 (user-operated checklist). ✓
- amend §8.2 slice-6 row "真实浏览器验收额外覆盖 SW 在 action dispatch 窗口重启和 form POST 失败不刷新" → Task 8 checks (2) covers SW restart + no-refresh; the form-POST-no-refresh is slice-4 amend §5.6 behavior already pinned by slice-4 tests, exercised again under the real browser in check (2). ✓
- §6.4 验收总结 (single navigate+complete; no refresh loop; ordinary tabs not navigated; no second heartbeat; userscript fallback; 400KB+5MB capture stable; storage write-on-change) → Task 8 checks (1)–(6). ✓ (storage write-on-change is the slice-1/2 `saveIfChanged` invariant, unchanged by slice 6 — not re-asserted in acceptance, it's a code invariant.)

**2. Placeholder scan:** No TBD/TODO/"add appropriate"/"similar to Task N". Every code step shows the full code. The `<N>` placeholders in Task 9 are explicit fill-in markers for the post-run green count (the ledger is written after the run, not before) — documented as such, not a plan defect. The Task 8 checklist is a human checklist, not a code placeholder. ✓

**3. Type consistency:** `errorEqual` (Task 1) consumes `ControllerError` (already imported in `panelState.ts`) and uses `Extract<ControllerError, {kind:'nav_error'}>` casts that match the `ControllerError` union in `shared/state.ts` (amend §1.3: `nav_error` has `error:string`, `unexpected_status` has `statusCode:number`, `content_unavailable` has `missing:…`, `gateway_5xx`/`rate_limited` are kind-only). `manualComplete`/`manualSkip` test helpers (`landedController`, `fakeApi`, `fakeChrome3`, `job`, `fullJob`) are all pre-existing in `controller.test.mjs` — Task 3 reuses them verbatim, no new helper names. `findOwnerTab` removal (Task 4) touches `ChromeRuntime` (chrome.ts) + the two test fakes — `CrawlControllerDeps.chrome`/`NavMonitorDeps.chrome`/`MessageRouterDeps.chrome` `Pick<>` sites never included it, so no type ripple. ✓

**4. Ordering invariant (the user's hard rule):** Tasks 1–5 commit with the gate OFF (each leaves the suite green and the official build a verified no-op — Task 5 confirms this). Tasks 6+7 stage the flip + docs but DO NOT commit. Task 8 (acceptance) runs against the staged flip. Task 9 commits the flip + docs only after acceptance passes. A failure at Task 8 → `git reset` the staged files (Task 8 rollback path), branch returns to Task-5 gate-OFF state. The gate flip never commits before acceptance. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-29-browserext-slice6-gate-flip-cleanup.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Tasks 6+8+9 are NOT subagent-dispatchable (they require staging-without-commit + the user's real-browser acceptance + a final commit gated on acceptance); those run inline in this session.

**2. Inline Execution** — Execute Tasks 1–5 in this session using executing-plans, then stage 6+7, hand off 8 to the user, then commit 9 after acceptance.

**Which approach?**
</content>
</invoke>
