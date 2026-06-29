# Browserext exclusive control — Slice 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land slice 4 of the Phase-2 exclusive-control design: port the **capture** (`htmlCleanup.ts`), **formActions** (`formPagination.ts`), and **pageDetect** (`utils.ts`'s `isErrorPage`/`terminalUnavailableReason`) modules from `userscripts/src/` into `browserext/src/content/` **byte-aligned** (the `synthetic_url` bug trap is real — ported tests assert same-input/same-output), then wire the **RPC contract** — `PREPARE_ACTION`/`PERFORM_ACTION`/`CAPTURE` (SW→CS) and `ACTION_PREPARED`/`ACTION_RESULT`/`CAPTURE_RESULT` (CS→SW), the **receipt-only protocol** (amend §1.1, §4.1, §4.4), the **CS rpcId ledger** (amend §4.3), **precise-`documentId` delivery** (`sendMessage(..., { documentId: pendingRpc.sourceDocumentId })`, amend §4.1) with full `MessageSender` validation, the **capture-before-second-detection** rule (amend §2.3 — `CAPTURE_RESULT.detection`), and the **form-action controlled exception** (prepare→persist→perform→confirm, amend §5.1–§5.6). The Controller dispatches `CAPTURE` on `landed` and `POST /jobs/{id}/complete` on a successful `CAPTURE_RESULT`; the RPC-recovery budget (amend §6.1) re-sends the **same rpcId** for `capture`/`prepare_action`/`perform_action` only. **No panel UI / PanelState-sync** (slice 5). The whole slice stays gated OFF (`EXCLUSIVE_CONTROL_ENABLED = false` in the official build), so the runtime is still a no-op and all Phase-1/2/3 tests stay green.

**Architecture:** Slice 4 adds four **pure** content modules + one pure shared helper so the byte-aligned logic is unit-testable without a live DOM, then adds one stateful CS message-router and one batch of Controller changes. New content modules: `content/pageDetect.ts` (pure `detectPage(input)` — a parameterized, dependency-injected port of userscript `utils.isErrorPage`/`terminalUnavailableReason` that reads from a `{title, bodyText, readyState, linksLength, bodyNull}` struct instead of the `document` global, so node tests can drive it), `content/capture.ts` (pure `stripCaptureNoise` + `serializeWithoutOverlay(root, options)` + the stability-wait logic, byte-aligned with `htmlCleanup.ts`; the stability wait is split into a pure `isCaptureStable(signatureFn, opts)` decider + a DOM-driving wrapper), `content/formActions.ts` (byte-aligned `performFetchAction`/`collectFormPaginationStates` from `formPagination.ts`, plus the **new** side-effect-free `prepareFormAction(action, doc)` returning `{ ok, targetUrl, method, expectedEffect, preparationFingerprint }` and `canonicalizeActionSnapshot(form, action)` — amend §5.1), and `content/fingerprint.ts` (the `sha256Hex(canonical)` over the canonical snapshot using `crypto.subtle`; pure `canonicalizeActionSnapshot` is testable synchronously, `sha256Hex` is async and injectable). New CS stateful module: `content/rpcRouter.ts` (consumes `PREPARE_ACTION`/`PERFORM_ACTION`/`CAPTURE`, drives the ported modules + ledger, emits `ACTION_PREPARED`/`ACTION_RESULT`/`CAPTURE_RESULT` with second-detection). New shared: `shared/rpcLedger.ts` (the per-document in-memory ledger — pure, amend §4.3). Controller changes: `landed`→dispatch `CAPTURE` (persist `pendingRpc.delivery='prepared'` THEN `sendMessage({documentId})`, set `'received'` on receipt — amend §1.1); `CAPTURE_RESULT` handler (validate rpcId/jobId/tab.id/documentId per amend §4.1; second-detection-fail→clear rpc + route skip/fail, never complete; amend §2.3); `PREPARE_ACTION`→`ACTION_PREPARED`→persist-new-`NavigationState`(form_action)→`PERFORM_ACTION`→`ACTION_RESULT` flow (amend §5.2–§5.4); same-document effect confirmation (amend §5.5); form-action failure routing (no `tabs.update` funnel, amend §5.6); RPC-recovery budget in `tick` (amend §6.1); `POST /jobs/{id}/complete` on success. `api.ts` gains `completeJob` (POST /complete, best-effort swallow — the late-/complete idempotency bug trap). The CS still does NOT call the backend, does NOT navigate (`form.submit()` is the controlled exception, dispatched only after the SW persists navigation intent — amend §5.2), and does NOT hold authoritative job state.

**Task ordering rationale (each task leaves the full suite green):** Tasks 1–6 (pure modules: pageDetect, capture, formActions-logic, fingerprint, ledger, rpcRouter) depend only on `shared/*` types + each other and the already-present `shared/hostPolicy`/`shared/urlGate` — each is green independently (no Controller/chrome.ts coupling). Task 7 (api.completeJob) is additive and isolated. Task 8 (Controller: capture dispatch + CAPTURE_RESULT + recovery + complete) depends on Tasks 1–7 but NOT on the form-action path — it lands the capture half. Task 9 (Controller: form-action prepare/perform/confirm + failure routing) depends on Task 8 and adds the form_action path on top. Task 10 is acceptance. This keeps "one green commit per step" true at every boundary; no commit folding is needed. The CS `rpcRouter` (Task 6) is testable in isolation with a fake `chrome.runtime`/document before the Controller consumes it.

**Tech Stack:** TypeScript 5.7, MV3 Chrome/Edge (≥110), esbuild, node:test, `chrome.tabs.sendMessage` (with `{ documentId }`), `chrome.runtime.onMessage`/`MessageSender`, `crypto.subtle.digest` (SHA-256, available in both SW and content-script DOM globals), DOM lib (content config).

## Global Constraints

Copied verbatim from the spec/amend + slice-1/2/3 plans so every task implicitly inherits them:

- **Gate default false.** Slice 1–5 official builds default `EXCLUSIVE_CONTROL_ENABLED = false`; every Controller `tick`/`bind`/`deliver*`/new-slice-4-dispatch body short-circuits on `if (!EXCLUSIVE_CONTROL_ENABLED) return;` (positive form `if (GATE) {...}`). Only unit tests (harness injects `true`) enable it. (spec §6.1)
- **`/status.current_job` is the sole job-state truth.** `ControllerState.currentJob` is a client cache only; reconcile (slice 2) discards stale `navigation`/`pendingRpc` when the job id changes. The CS holds NO authoritative job state — `PanelState` is a mirror only (slice 5). (spec §1.3, amend §9)
- **Controller is the sole `chrome.tabs.update` caller.** Form-action `form.submit()` is the ONE controlled exception: it runs in the CS, but ONLY after the SW has persisted a **new** `navigation` record (`kind: 'form_action'`) — amend §5.2. The CS never calls `tabs.update` and never mutates `window.location`. (spec §3.6, amend §5.2)
- **`pendingRpc` carries a stable rpcId + sourceDocumentId + delivery stage** (amend §1.1). The Controller persists `delivery: 'prepared'` BEFORE `sendMessage`; on transport receipt (`{ received: true }`) it persists `delivery: 'received'`. SW restart between send and `received` → re-send the **same rpcId**; never generate a new rpcId. `CAPTURE_RESULT`/`ACTION_PREPARED`/`ACTION_RESULT` are operation results, NOT transport receipts — receiving a valid result transitions phase or clears `pendingRpc`; there is NO second `acked` boolean. (amend §1.1)
- **Precise `documentId` delivery (amend §4.1).** Every work RPC (`PREPARE_ACTION`/`PERFORM_ACTION`/`CAPTURE`) is sent with `chrome.tabs.sendMessage(boundTabId, message, { documentId: pendingRpc.sourceDocumentId })` — never just `{ frameId: 0 }`, never re-derived from "current navigation.commit". `sourceDocumentId` comes from an already-verified main-frame commit. Result validation (amend §4.1): `sender.tab?.id === boundTabId && sender.frameId === 0 && sender.documentId === pendingRpc.sourceDocumentId && message.rpcId === pendingRpc.id && message.jobId === pendingRpc.jobId`. A result whose `documentId`/`rpcId`/`jobId`/`tabId` doesn't match is logged + dropped (late/stale RPC, CLAUDE.md bug trap) — never crashes.
- **sendMessage reject → typed recovery (amend §4.2), never the nav funnel.** `prepare_action`/`capture` reject (target doc gone) → `content_unavailable`. `perform_action` reject with an observed requestId/commit → keep waiting (source-doc loss is expected for a new-document form action). `perform_action` reject with neither receipt nor requestId/commit → `content_unavailable/action_result`, same-rpcId recovery only.
- **CS rpcId ledger (amend §4.3).** Per document, in-memory: insert `{ stage: 'received' }` before returning the transport receipt; first `PERFORM_ACTION` for an rpcId executes the action + caches a small `ACTION_RESULT`; repeats for that rpcId NEVER re-execute the form — they re-emit the cached result or just confirm receipt. `PREPARE_ACTION` is side-effect-free and re-runnable. `CAPTURE` is side-effect-free and may be re-sent on result-timeout (large HTML is NOT cached in the ledger).
- **Landing 5-condition rule (amend §2.3) is already implemented in slice 3** (`controller/landing.ts`) and is NOT modified this slice. Slice 4 adds only what happens AFTER `landed` (dispatch CAPTURE) and the capture-time second detection.
- **Capture-before-second-detection (amend §2.3).** `PAGE_READY.detection` is the first detection; before the CS returns `CAPTURE_RESULT.html` it re-runs the SAME detection and includes it as `CAPTURE_RESULT.detection`. If the second detection fails (terminal/error), the CS omits `html` and the Controller (after §4.1 validation) atomically clears the capture `pendingRpc`, exits `capturing`, then routes: `not_found|content_removed|empty_page`→terminal skip; `errorPage` + `navigate`→gateway funnel (clear signals, re-navigate); `errorPage` + `form_action`→fail `form_action_navigation_failed` (no `tabs.update`, no re-submit). A failed capture never reaches `complete`.
- **Form-action controlled exception (amend §5.1–§5.6):**
  - **Prepare** is side-effect-free (no control mutation, no click, no submit); it parses `form.action`/`method`, judges `expectedEffect`, computes `targetUrl` (GET forms merge successful controls + `FetchAction.fields` into a query URL so §2.1 requestId URL-match holds; POST forms use the parsed `form.action`), and computes `preparationFingerprint` = SHA-256 of a canonical snapshot. `targetUrl` must pass allowed-host + same-crawl-site gate or the action is NOT performed (offsite skip). `ACTION_PREPARED.ok===false` → no action; retry prepare within budget, else fail `form_action_prepare_failed:<error>`.
  - **Persist-before-perform (amend §5.2):** on a valid `ACTION_PREPARED`, the Controller persists a **new** `NavigationState` (`kind:'form_action'`, `requestedUrl: prepared.targetUrl`, `sourceDocumentId: <old commit.documentId>`, `action:{expectedEffect,method,preparationFingerprint,invocationReported:false,effectConfirmed:false}`, all of `requestId`/`commit`/`http`/`pageReady`/`acceptedUrl` ABSENT) + a new stable perform rpcId + `pendingRpc.delivery:'prepared'`. ONLY THEN does it send `PERFORM_ACTION`.
  - **Perform (amend §5.3):** the CS re-validates the fingerprint; mismatch → `ACTION_RESULT{ok:false,error:'form_action_prepare_changed'}`, no side effect. `ACTION_RESULT.ok && invoked` only proves the call returned; `navigationExpected:true` only means "expect a new document" (NOT a capture condition); `effectApplied:true && navigationExpected:false` is the same-document effect-confirmation. Fingerprint mismatch or `invoked===false` → fail `form_action_prepare_changed|form_action_invoke_failed`, never the nav funnel.
  - **New-document completion (amend §5.4):** `navigationExpected:true` → wait the §2.3 five conditions (the new main-frame `onBeforeRequest` binds a fresh `requestId` in `phase==='acting'`). Once a correlated requestId or new commit appears, NEVER send a new perform rpcId. On full landing, clear the perform `pendingRpc`, go `landed`, create a fresh CAPTURE rpcId.
  - **Same-document completion (amend §5.5):** effect confirmed by `ACTION_RESULT.effectApplied===true && navigationExpected===false`, OR `onHistoryStateUpdated.documentId === sourceDocumentId` (URL still passes same-site gate), OR a cached `ACTION_RESULT` re-reporting `effectApplied`. Bare `ACTION_RESULT.ok` / bare receipt / bare `navigationExpected:false` is NOT enough. After effect confirmation, re-run page detection, then `landed → capturing`.
  - **Form-action failure never hits `tabs.update` (amend §5.6):** 30s with no correlated commit/history/effect, or a new document that's gateway/nav_error/unexpected_status/soft-error, or source-doc destroyed without completion signals → fail `form_action_navigation_failed:<detail>` (client `attempt` fixed at 1; backend retry owns the next job). Terminal pages still skip (`not_found`/`content_removed`/`empty_page`); 429 still fails `rate_limited`. The form is NEVER re-submitted and the tab is NEVER re-navigated.
- **RPC-recovery budget (amend §6.1, §6.2).** Distinct from the landing-signals deadline (slice 3). `capture`/`prepare_action`/`perform_action` results: 30s result-deadline from send; on timeout, if the target source document still matches, re-send the **same rpcId** up to 3 times, ≥5s apart, gated by `nextRecoveryAt`. `perform_action` recovery is ONLY allowed while there is no requestId/commit/effect yet — once any of those appears, the perform budget expires and the Controller only waits for correlated signals. `PAGE_READY`/HTTP-outcome are NOT RPC-recoverable (slice 3 set `recoveryExhausted=true` immediately; slice 4 does not change that). On budget exhaustion set `recoveryExhausted=true`, stop auto RPC + navigation, keep panel manual ops; if `/status` shows the job released, reconcile to idle.
- **HTTP contract is FIXED.** No new/changed endpoints, no DB schema change. `POST /jobs/{id}/complete` (body `{ html, url, title, pagination_states }`) already exists (userscript `completeJob` uses it); slice 4 adds a Controller `completeJob` that calls it. Late `/complete` after the 60s job timeout is idempotent — `completeJob` swallows errors and the backend no-ops a stale id (CLAUDE.md bug trap). (CLAUDE.md, spec §1.3, amend §9)
- **Byte-aligned ports.** `collectFormPaginationStates`/`performFetchAction`/`buildSyntheticUrl`/`stripCaptureNoise`/`detectPage` are copied from `userscripts/src/` with only the dependency-injection changes needed to run without the `document`/`window` globals (read the same fields via an injected `doc`). Ported tests assert same-input→same-output; the `synthetic_url` bug trap is pinned by a ported test. (CLAUDE.md, spec §4.1)
- **2s TICK does not unconditionally write storage** — slice-2/3 `saveIfChanged(prev, state)` is reused; slice-4 dispatch/recovery paths persist only on change. (spec §2.2)
- **One conventional commit per green step.** `feat(browserext)`/`test(browserext)`/`chore(browserext)`/`docs(browserext)`. (CLAUDE.md)
- **browserext tests run with `npm test` (node:test, no pytest).** Relative imports in `src/*.ts` carry `.js`. The harness bundles via esbuild and injects `EXCLUSIVE_CONTROL_ENABLED = true` so gated-ON paths are exercised. (CLAUDE.md)
- **`dist/` is gitignored.**
- **Baseline before starting: `npm test` reports `pass 112 fail 0` and `npm run typecheck` exits 0 on branch `slice3`.**

---

## File Structure

New/modified files in this slice. Decomposition: the byte-aligned ports are pure functions over an injected `doc` struct (not the `document` global) so they are unit-testable in node with a fake DOM; the new form-action prepare logic and fingerprint are pure too. The CS `rpcRouter` is the single stateful CS module; the Controller gains the dispatch/result/recovery methods under its existing mutex.

```
browserext/
  src/content/pageDetect.ts            # CREATE (Task 1): pure detectPage(input) — port of utils.isErrorPage/terminalUnavailableReason (byte-aligned patterns), reads {title,bodyText,readyState,linksLength,bodyNull}
  src/content/capture.ts                # CREATE (Task 2): port stripCaptureNoise + serializeWithoutOverlay + isCaptureStable decider (byte-aligned with htmlCleanup.ts); DOM-driving capture runPageCapture is a thin wrapper, not unit-tested
  src/content/formActions.ts            # CREATE (Task 3): byte-aligned performFetchAction + collectFormPaginationStates (port of formPagination.ts) + NEW prepareFormAction/canonicalizeActionSnapshot (amend §5.1, side-effect-free)
  src/content/fingerprint.ts            # CREATE (Task 4): sha256Hex(canonicalJson) via crypto.subtle + canonicalizeActionSnapshot re-export; amend §5.1 fingerprint
  src/shared/rpcLedger.ts               # CREATE (Task 5): pure per-document in-memory ledger (amend §4.3) — createRpcLedger(){markReceived/markDone/get/has}
  src/content/rpcRouter.ts              # CREATE (Task 6): stateful CS message router — consumes PREPARE_ACTION/PERFORM_ACTION/CAPTURE, drives ledger+ported modules, emits ACTION_PREPARED/ACTION_RESULT/CAPTURE_RESULT (+ second detection)
  src/content/index.ts                  # MODIFY (Task 6): wire rpcRouter into bootstrapContent's onAllowedHost + emit PAGE_READY (detection via pageDetect) — still gated
  src/api.ts                            # MODIFY (Task 7): + completeJob (POST /complete, best-effort swallow)
  src/controller/controller.ts          # MODIFY (Tasks 8–9): landed→dispatch CAPTURE (receipt-only) + CAPTURE_RESULT handler (second-detection routing) + RPC-recovery budget in tick + complete; + form-action PREPARE/PERFORM/ACTION_PREPARED/ACTION_RESULT flow + same-document confirm + failure routing
  src/shared/state.ts                   # NO CHANGE (PendingRpc/NavigationState/ControllerError already match amend §1.1–§1.3 — slice 1 placed them)
  src/shared/rpc.ts                     # NO CHANGE (SwToCs/CsToSw already carry PREPARE_ACTION/ACTION_PREPARED — slice 1 placed them)
  docs/superpowers/specs/...            # no change this slice
```

Boundary notes:
- `content/pageDetect.ts` + `content/capture.ts` + `content/formActions.ts` + `content/fingerprint.ts` + `shared/rpcLedger.ts` are pure (no `chrome`, no `document` global — `document`/`window` are passed in as injected `doc` structs). Both tsconfigs already `include` `src/shared/**/*.ts`; `src/content/**/*.ts` is in the content config only. `content/fingerprint.ts` uses `crypto.subtle` (in the DOM lib) — it lives under `content/` so the background config does not need Web Crypto.
- `content/rpcRouter.ts` is stateful but dependency-injected: it takes `{ runtime, document, sender, ledgerFactory, hashFn }`-style deps so node tests drive it with fakes. It imports the ported modules + `shared/rpcLedger` + `shared/types` + `shared/rpc` (types). It does NOT import `api`/`chrome` (background) — it talks to the SW only via `chrome.runtime.sendMessage`/`onMessage` injected as `runtime`.
- `controller/controller.ts` grows: `landed` dispatch (a new `dispatchCapture` under the lock), `CAPTURE_RESULT`/`ACTION_PREPARED`/`ACTION_RESULT` result handlers (each re-acquires the mutex, validates per amend §4.1, transitions state), the prepare→persist→perform form-action path, the RPC-recovery budget in `tick`, and `complete` on success. It imports `shared/rpcLedger`? No — the ledger is CS-side only; the Controller tracks `pendingRpc` in `ControllerState`. The Controller's `CrawlControllerDeps` gains `chrome.tabs.sendMessage` (a new `sendMessage` method on `ChromeRuntime`) so the SW can dispatch work RPCs to a precise `documentId`.
- `chrome.ts` gains `sendMessage(tabId, message, options): Promise<unknown>` (wraps `chrome.tabs.sendMessage` with `{ documentId }`) — needed by the Controller dispatch. (Task 8 modifies `chrome.ts` + its test, consistent with slice-3's pattern of widening chrome.ts alongside controller changes.)

---

## Task 1: content/pageDetect.ts — pure byte-aligned page detection

**Files:**
- Create: `browserext/src/content/pageDetect.ts`
- Create: `browserext/tests/content/pageDetect.test.mjs`

**Interfaces:**
- Consumes: `TerminalUnavailableReason`, `PageDetection` (types) from `../shared/state.js`.
- Produces (pure, byte-aligned with userscript `utils.ts`):
  - `PageDetectInput = { title: string; bodyText: string; readyState: 'loading'|'interactive'|'complete'; linksLength: number; bodyNull: boolean }` — the fields the userscript reads off `document`.
  - `detectPage(input: PageDetectInput): PageDetection` → `{ errorPage, terminalReason }`. Byte-aligned with `utils.isErrorPage` + `terminalUnavailableReason`: same `ERROR_PATTERNS`/`NOT_FOUND_PATTERNS`/`REMOVED_PATTERNS` regexes, same `<1500`/`<4000` length thresholds, same `empty_page` rule (`readyState==='complete' && bodyNull===false && bodyText.trim()==='' && linksLength===0`).
  - `ERROR_PATTERNS`/`NOT_FOUND_PATTERNS`/`REMOVED_PATTERNS` exported (so ported tests + future reuse see them).

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/pageDetect.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function input(o = {}) {
  return { title: '', bodyText: '', readyState: 'complete', linksLength: 0, bodyNull: false, ...o };
}

test('errorPage: short body + 502 keyword → errorPage true (byte-aligned with userscript)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    assert.equal(mod.detectPage(input({ title: '502 Bad Gateway', bodyText: 'x'.repeat(200) })).errorPage, true);
    assert.equal(mod.detectPage(input({ title: '503 Service', bodyText: 'server error' })).errorPage, true);
  } finally { await cleanup(); }
});

test('errorPage: long body (>1500) is NOT an error page', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    assert.equal(mod.detectPage(input({ title: '502 Bad Gateway', bodyText: 'x'.repeat(2000) })).errorPage, false);
  } finally { await cleanup(); }
});

test('terminalReason not_found: short body + 404 keyword', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ title: '页面不存在', bodyText: '您访问的页面不存在' }));
    assert.equal(d.terminalReason, 'not_found');
  } finally { await cleanup(); }
});

test('terminalReason content_removed: 已下线 keyword', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ bodyText: '该内容已下线' }));
    assert.equal(d.terminalReason, 'content_removed');
  } finally { await cleanup(); }
});

test('terminalReason empty_page: complete + empty body + no links', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ readyState: 'complete', bodyText: '   ', linksLength: 0 }));
    assert.equal(d.terminalReason, 'empty_page');
    // body present but null (no <body>) is NOT empty_page (guard mirrors userscript: document.body !== null)
    assert.equal(mod.detectPage(input({ bodyNull: true, bodyText: '', linksLength: 0 })).terminalReason, null);
  } finally { await cleanup(); }
});

test('terminalReason null on a normal long page', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ title: '教师列表', bodyText: '张三 李四 王五 '.repeat(500), linksLength: 40 }));
    assert.equal(d.terminalReason, null);
    assert.equal(d.errorPage, false);
  } finally { await cleanup(); }
});

test('terminalReason precedence: not_found before empty_page (matches userscript order)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    // a not_found-pattern page that also has an empty body: userscript checks NOT_FOUND first
    const d = mod.detectPage(input({ title: '404', bodyText: 'Not Found' }));
    assert.equal(d.terminalReason, 'not_found');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='errorPage:|terminalReason' 2>&1 | tail -15`
Expected: FAIL — `../src/content/pageDetect.ts` does not exist (ENOENT).

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/pageDetect.ts`:
```ts
/** Pure page detection — a dependency-injected port of userscripts/src/utils.ts
 *  isErrorPage / terminalUnavailableReason (spec §4.1). Reads the same fields the
 *  userscript reads off `document`, but from an injected input struct so it is
 *  unit-testable in node without a DOM. The regexes and length thresholds are
 *  byte-aligned with the userscript; the empty_page guard mirrors the userscript's
 *  `document.body !== null` check via the `bodyNull` field. Slice 4's content
 *  script calls this on PAGE_READY (first detection) and again before CAPTURE_RESULT
 *  (second detection — amend §2.3). */

import type { PageDetection, TerminalUnavailableReason } from '../shared/state.js';

export interface PageDetectInput {
  title: string;
  bodyText: string;
  readyState: 'loading' | 'interactive' | 'complete';
  linksLength: number;
  bodyNull: boolean;        // true when document.body === null (userscript guards on this)
}

export const ERROR_PATTERNS = /502 bad gateway|503 service|504 gateway|500 internal|error occurred|server error|nginx/i;
export const NOT_FOUND_PATTERNS = /\b404\b|not found|page not found|页面不存在|网页不存在|未找到页面|找不到页面|访问的页面不存在|您访问的页面不存在|信息不存在|该信息不存在|文章不存在/i;
export const REMOVED_PATTERNS = /内容已撤销|内容被撤销|该内容已被删除|内容已被删除|文章已被删除|信息已被删除|该信息已删除|已下线|页面已下线|内容已失效/i;

/** Byte-aligned with userscript isErrorPage + terminalUnavailableReason. Returns
 *  { errorPage, terminalReason }. errorPage is true only for short (<1500 char)
 *  bodies matching an error keyword. terminalReason precedence mirrors the
 *  userscript: not_found → content_removed → empty_page → null. */
export function detectPage(input: PageDetectInput): PageDetection {
  const title = input.title || '';
  const bodyText = input.bodyText || '';

  const errorPage = bodyText.length < 1500 && ERROR_PATTERNS.test(`${title} ${bodyText}`);

  let terminalReason: TerminalUnavailableReason | null = null;
  const haystack = `${title} ${bodyText}`;
  if (bodyText.length < 4000 && NOT_FOUND_PATTERNS.test(haystack)) {
    terminalReason = 'not_found';
  } else if (bodyText.length < 4000 && REMOVED_PATTERNS.test(haystack)) {
    terminalReason = 'content_removed';
  } else if (
    input.readyState === 'complete'
    && input.bodyNull === false
    && bodyText.trim().length === 0
    && input.linksLength === 0
  ) {
    terminalReason = 'empty_page';
  }

  return { errorPage, terminalReason };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern='errorPage:|terminalReason' 2>&1 | tail -15`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0 (both configs); full suite `pass 119 fail 0` (112 baseline + 7 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/content/pageDetect.ts browserext/tests/content/pageDetect.test.mjs
git commit -m "feat(browserext): pure byte-aligned page detection (port of utils.isErrorPage/terminalUnavailableReason)"
```

---

## Task 2: content/capture.ts — port stripCaptureNoise + capture stability

**Files:**
- Create: `browserext/src/content/capture.ts`
- Create: `browserext/tests/content/capture.test.mjs`

**Interfaces:**
- Consumes: nothing (pure; the DOM-driving wrapper receives a `ParentNode`-shaped root + options).
- Produces (byte-aligned with userscript `htmlCleanup.ts`):
  - `CaptureCleanupOptions = { stripHeader?: boolean }`.
  - `stripCaptureNoise(root: { querySelectorAll(sel: string): Array<{ remove(): void }> }, options?: CaptureCleanupOptions): void` — byte-aligned with `htmlCleanup.stripCaptureNoise` (removes `#ycl-panel,#ycl-toast,[data-yanclaw-overlay]`, `svg,style,canvas`, then footer structural noise, then header if `stripHeader`). The structural-noise decision is delegated to `shouldStripElementDescriptor`.
  - `shouldStripElementDescriptor(kind: 'footer'|'header', descriptor: ElementDescriptor): boolean` — byte-aligned with `htmlCleanup.shouldStripElementDescriptor`.
  - `serializeWithoutOverlay(root: { cloneNode(deep: true): any }, options: { stripHeader: boolean }): string` — clones the root, runs `stripCaptureNoise`, returns `outerHTML`. (The userscript version reads `document.documentElement`; here the root is injected so it is testable.)
  - `isCaptureStable(prev: string, curr: string, stableRounds: number, threshold: number): boolean` — the pure decider half of `waitForCaptureReady`: returns true when `prev === curr && stableRounds >= threshold`. (The DOM-driving `runPageCapture` wrapper that polls `document.readyState`/`innerText`/scrolls is NOT unit-tested — it is a thin IIFE around `isCaptureStable` + `serializeWithoutOverlay`.)

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/capture.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// A minimal fake DOM node: holds children by selector, supports remove + cloneNode.
function makeNode(tagName = 'div', attrs = {}, children = []) {
  const node = {
    tagName,
    _attrs: { ...attrs },
    _children: [...children],
    _removed: false,
    getAttribute(n) { return n in this._attrs ? this._attrs[n] : null; },
    remove() { this._removed = true; },
    cloneNode(deep) {
      const c = makeNode(this.tagName, { ...this._attrs }, deep ? this._children.map((k) => k.cloneNode(true)) : []);
      return c;
    },
    querySelectorAll(sel) {
      const out = [];
      const walk = (n) => {
        for (const ch of n._children) {
          if (matches(ch, sel)) out.push(ch);
          out.push(...ch.querySelectorAll(sel));
        }
      };
      walk(this);
      return out;
    },
    get outerHTML() {
      const a = Object.entries(this._attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
      const inner = this._children.map((c) => c.outerHTML ?? String(c)).join('');
      return `<${this.tagName}${a}>${inner}</${this.tagName}>`;
    },
  };
  return node;
}
function matches(node, sel) {
  // support only the simple selectors used by stripCaptureNoise: `#id`, `tag`, `[attr]`, `tag[attr]`, comma lists
  for (const part of sel.split(',')) {
    const p = part.trim();
    let ok = true;
    const idM = p.match(/^#([\w-]+)$/);
    const tagM = p.match(/^([a-z]+)(\[([\w-]+)\])?$/);
    const attrM = p.match(/^\[([\w-]+)\]$/);
    if (idM) ok = ok && node._attrs.id === idM[1];
    else if (attrM) ok = ok && (attrM[1] in node._attrs);
    else if (tagM) ok = ok && node.tagName === tagM[1] && (!tagM[3] || (tagM[3] in node._attrs));
    else ok = false;
    if (ok) return true;
  }
  return false;
}

test('stripCaptureNoise removes ycl-panel, svg, style, canvas', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [
      makeNode('div', { id: 'ycl-panel' }),
      makeNode('svg'),
      makeNode('style'),
      makeNode('canvas'),
      makeNode('p', {}, ['keep']),
    ]);
    mod.stripCaptureNoise(root, {});
    const removed = root.querySelectorAll('#ycl-panel,svg,style,canvas');
    assert.equal(removed.length, 0, 'noise nodes removed');
    assert.equal(root._children.filter((c) => !c._removed).length, 1, 'only the <p> survives');
  } finally { await cleanup(); }
});

test('stripCaptureNoise removes a <footer role=contentinfo>', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [makeNode('footer', { role: 'contentinfo' })]);
    mod.stripCaptureNoise(root, {});
    assert.equal(root._children[0]._removed, true);
  } finally { await cleanup(); }
});

test('stripCaptureNoise does NOT strip header when stripHeader omitted (byte-aligned default)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [makeNode('header', { class: 'site-header' })]);
    mod.stripCaptureNoise(root, {});
    assert.equal(root._children[0]._removed, false, 'header kept by default');
  } finally { await cleanup(); }
});

test('stripCaptureNoise strips header when stripHeader: true', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [makeNode('header', { class: 'site-header' })]);
    mod.stripCaptureNoise(root, { stripHeader: true });
    assert.equal(root._children[0]._removed, true);
  } finally { await cleanup(); }
});

test('shouldStripElementDescriptor: only first class segment identifies (NEU wrapper header regression)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    // class="wrapper header" → first segment "wrapper" is not a header name → NOT stripped
    assert.equal(mod.shouldStripElementDescriptor('header', { tagName: 'div', className: 'wrapper header', role: null, id: null }), false);
    // class="header" → stripped
    assert.equal(mod.shouldStripElementDescriptor('header', { tagName: 'div', className: 'header', role: null, id: null }), true);
  } finally { await cleanup(); }
});

test('serializeWithoutOverlay clones, strips, returns outerHTML', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('html', {}, [makeNode('body', {}, [makeNode('svg'), makeNode('p', {}, ['hi'])])]);
    const html = mod.serializeWithoutOverlay(root, { stripHeader: false });
    assert.ok(html.includes('<p>hi</p>'), 'content kept');
    assert.ok(!html.includes('<svg>'), 'svg stripped from clone (original untouched)');
  } finally { await cleanup(); }
});

test('isCaptureStable: true only when signature equal AND rounds met', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    assert.equal(mod.isCaptureStable('a', 'a', 3, 3), true);
    assert.equal(mod.isCaptureStable('a', 'a', 2, 3), false);
    assert.equal(mod.isCaptureStable('a', 'b', 3, 3), false);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='stripCaptureNoise|shouldStripElementDescriptor|serializeWithoutOverlay|isCaptureStable' 2>&1 | tail -15`
Expected: FAIL — `../src/content/capture.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/capture.ts`:
```ts
/** HTML capture cleanup + serialization — a byte-aligned port of
 *  userscripts/src/htmlCleanup.ts (spec §4.1). Pure: takes an injected root
 *  (a ParentNode-shaped object) instead of `document`/`documentElement`, so it is
 *  unit-testable. The structural-noise stripping (footer/header by name/role/class)
 *  is byte-aligned with the userscript, including the "only the FIRST class segment
 *  identifies a structural role" guard (the NEU `wrapper header` regression) and the
 *  "header that swallows the page body is left intact" guard (the LZU regression).
 *
 *  The stability-wait logic (waitForCaptureReady) is split into a pure
 *  isCaptureStable decider + a DOM-driving wrapper; only the decider is unit-tested. */

export interface CaptureCleanupOptions {
  stripHeader?: boolean;
}

type StructuralNoiseKind = 'footer' | 'header';

export interface ElementDescriptor {
  tagName?: string | null;
  role?: string | null;
  id?: string | null;
  className?: string | null;
}

const FOOTER_NAMES = new Set(['footer', 'foot', 'copyright', 'copy-right', 'site-footer', 'page-footer']);
const FOOTER_BOUNDARY_NAMES = new Set(['footer', 'foot', 'copyright', 'copy-right']);
const HEADER_NAMES = new Set(['header', 'head', 'site-header', 'page-header', 'topbar', 'top-bar', 'nav-header']);
const HEADER_BOUNDARY_NAMES = new Set(['header', 'topbar', 'top-bar']);

const FOOTER_ROLES = new Set(['contentinfo']);
const HEADER_ROLES = new Set(['banner']);

const HEADER_SWALLOW_RATIO = 0.5;

/** Minimal injected-root shape — only what stripCaptureNoise touches. */
interface CaptureRoot {
  querySelectorAll(selector: string): Array<{ remove(): void; querySelectorAll(selector: string): Array<unknown> }>;
}

export function stripCaptureNoise(root: CaptureRoot, options: CaptureCleanupOptions = {}): void {
  root.querySelectorAll('#ycl-panel,#ycl-toast,[data-yanclaw-overlay]').forEach((node) => node.remove());
  root.querySelectorAll('svg,style,canvas').forEach((node) => node.remove());
  stripStructuralNoise(root, 'footer');
  if (options.stripHeader) {
    stripStructuralNoise(root, 'header');
  }
}

export function shouldStripElementDescriptor(kind: StructuralNoiseKind, descriptor: ElementDescriptor): boolean {
  const tagName = (descriptor.tagName ?? '').toLowerCase();
  const role = (descriptor.role ?? '').toLowerCase();
  if (kind === 'footer') {
    return (
      tagName === 'footer'
      || FOOTER_ROLES.has(role)
      || attributeHasStructuralName(descriptor.id, FOOTER_NAMES, FOOTER_BOUNDARY_NAMES)
      || attributeHasStructuralName(descriptor.className, FOOTER_NAMES, FOOTER_BOUNDARY_NAMES)
    );
  }
  return (
    tagName === 'header'
    || HEADER_ROLES.has(role)
    || attributeHasStructuralName(descriptor.id, HEADER_NAMES, HEADER_BOUNDARY_NAMES)
    || attributeHasStructuralName(descriptor.className, HEADER_NAMES, HEADER_BOUNDARY_NAMES)
  );
}

function stripStructuralNoise(root: CaptureRoot, kind: StructuralNoiseKind): void {
  const rootDescendantCount = root.querySelectorAll('*').length;
  root.querySelectorAll('*').forEach((node) => {
    if (shouldStripElementNode(kind, node)) {
      if (kind === 'header' && wouldSwallowPageBody(node, rootDescendantCount)) {
        return;
      }
      node.remove();
    }
  });
}

function wouldSwallowPageBody(header: { querySelectorAll(sel: string): Array<unknown> }, rootDescendantCount: number): boolean {
  if (rootDescendantCount === 0) return false;
  const headerDescendantCount = header.querySelectorAll('*').length;
  return headerDescendantCount / rootDescendantCount >= HEADER_SWALLOW_RATIO;
}

function shouldStripElementNode(kind: StructuralNoiseKind, element: Element | { tagName?: string; getAttribute(n: string): string | null }): boolean {
  return shouldStripElementDescriptor(kind, {
    tagName: typeof (element as Element).tagName === 'string' ? (element as Element).tagName : undefined,
    role: element.getAttribute('role'),
    id: element.getAttribute('id'),
    className: element.getAttribute('class'),
  });
}

function attributeHasStructuralName(
  value: string | null | undefined,
  names: Set<string>,
  boundaryNames: Set<string>,
): boolean {
  if (!value) return false;
  const segments = value.trim().split(/\s+/);
  if (segments.length === 0) return false;
  return segmentMatchesName(segments[0] ?? '', names, boundaryNames);
}

function segmentMatchesName(segment: string, names: Set<string>, boundaryNames: Set<string>): boolean {
  const canonical = canonicalizeIdentifier(segment);
  if (!canonical) return false;
  if (matchesName(canonical, names)) return true;
  return canonical.split('-').some((token) => matchesName(token, boundaryNames));
}

function matchesName(value: string, names: Set<string>): boolean {
  return names.has(value) || names.has(value.replaceAll('-', ''));
}

function canonicalizeIdentifier(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/** Serialize the (cloned) root with overlay/noise stripped — byte-aligned with the
 *  userscript serializePageWithoutOverlay. The caller passes the real
 *  document.documentElement (or a fake root in tests). */
export function serializeWithoutOverlay(
  root: CaptureRoot & { cloneNode(deep: boolean): CaptureRoot & { outerHTML: string } },
  options: { stripHeader: boolean },
): string {
  const clone = root.cloneNode(true);
  stripCaptureNoise(clone, options);
  return clone.outerHTML;
}

/** Pure decider for the capture-stability loop (waitForCaptureReady). The DOM-driving
 *  wrapper computes a signature (textLength:nodeCount:imageCount) each round and calls
 *  this; stable when the signature is unchanged AND stableRounds >= threshold. */
export function isCaptureStable(prev: string, curr: string, stableRounds: number, threshold: number): boolean {
  return prev === curr && stableRounds >= threshold;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern='stripCaptureNoise|shouldStripElementDescriptor|serializeWithoutOverlay|isCaptureStable' 2>&1 | tail -15`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 126 fail 0` (119 + 7 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/content/capture.ts browserext/tests/content/capture.test.mjs
git commit -m "feat(browserext): byte-aligned capture cleanup + serialization (port of htmlCleanup)"
```

---

## Task 3: content/formActions.ts — byte-aligned form logic + side-effect-free prepare

**Files:**
- Create: `browserext/src/content/formActions.ts`
- Create: `browserext/tests/content/formActions.test.mjs`

**Interfaces:**
- Consumes: `FetchAction`, `PaginationState` (types) from `../shared/types.js`; `canonicalizeActionSnapshot`, `PreparationSnapshot` from `./fingerprint.js` (Task 4 — NOTE: this task defines `prepareFormAction` which *calls* `canonicalizeActionSnapshot`, so Task 3 depends on Task 4; the **byte-aligned** `collectFormPaginationStates`/`performFetchAction`/`buildSyntheticUrl` do NOT depend on Task 4). To keep Task 3 independently testable for the byte-aligned half, the test for `prepareFormAction` is added in Task 4's test file alongside `canonicalizeActionSnapshot`. **Task 3's own test file covers only `collectFormPaginationStates`/`performFetchAction`/`buildSyntheticUrl`** (the byte-aligned ports), so Task 3 is green on its own; the `prepareFormAction` impl is written in Task 3 but its test lands in Task 4.
- Produces:
  - `FormDoc` — injected document shape (the fields `formPagination.ts` reads off `document`): `{ forms: { namedItem(name: string): FormEl | null; [index: number]: FormEl }; querySelectorAll(sel: string): Element[]; location: { href: string }; links: { length: number }; querySelector(sel: string): { textContent: string } | null }`.
  - `collectFormPaginationStates(currentUrl: string, doc: FormDoc): PaginationState[]` — byte-aligned port of `formPagination.collectFormPaginationStates` (same `PAGE_ASSIGN_RE`/`GOTO_FIELD_RE`, same `buildSyntheticUrl` `__ycl_*` query params, same sort). **The `synthetic_url` must byte-match the userscript.**
  - `buildSyntheticUrl(url: string, formName: string, fieldName: string, pageIndex: number): string` — byte-aligned (exported for direct test).
  - `performFetchAction(action: FetchAction, doc: FormDoc): boolean` — byte-aligned port (mutates controls + `form.submit()`). The injected `doc.forms.namedItem(...).submit()` is called when `action.submit !== false`.
  - `prepareFormAction(action: FetchAction, doc: FormDoc): PrepareResult` — NEW (amend §5.1), side-effect-free: finds the form, parses `action`/`method`, computes `expectedEffect`, computes `targetUrl` (GET merges successful controls + fields into a query URL; POST uses the parsed absolute `form.action`), computes `preparationFingerprint` via `canonicalizeActionSnapshot` + `sha256Hex`. Returns `{ ok, targetUrl?, method?, expectedEffect?, preparationFingerprint?, error? }`. `ok===false` if form not found or `targetUrl` fails the allowed-host + same-crawl-site gate (offsite → `ok:false, error:'offsite_redirect'`, do NOT perform).

> **Dependency note:** Because `prepareFormAction` calls `canonicalizeActionSnapshot` (Task 4) and `sha256Hex` (Task 4), Task 3's `formActions.ts` imports from `./fingerprint.js`. To avoid a forward-dependency that breaks Task 3's standalone green, **implement Task 4 before Task 3** at execution time, OR — equivalently — treat Tasks 3 and 4 as a pair where Task 4 (fingerprint) is built first. The plan numbers them 3 then 4 for narrative order, but the executor MUST land Task 4's `fingerprint.ts` + its `canonicalizeActionSnapshot` before Task 3's `prepareFormAction`. The byte-aligned `collectFormPaginationStates`/`performFetchAction`/`buildSyntheticUrl` have NO dependency on Task 4 and are fully testable in Task 3. The clean execution order is: **Task 4 first, then Task 3.** (The self-review confirms this.)

- [ ] **Step 1: Write the failing test (byte-aligned half only)**

Create `browserext/tests/content/formActions.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// Byte-aligned synthetic_url regression trap: must match userscript buildSyntheticUrl exactly.
test('buildSyntheticUrl byte-matches userscript (__ycl_* params; hash cleared; __ycl_ stripped first)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const u = mod.buildSyntheticUrl('https://xjtu.edu.cn/list?__ycl_kind=stale&page=2#frag', 'pageForm', 'PAGENUM', 3);
    assert.equal(u, 'https://xjtu.edu.cn/list?page=2&__ycl_kind=form&__ycl_form=pageForm&__ycl_field=PAGENUM&__ycl_page=3');
  } finally { await cleanup(); }
});

test('buildSyntheticUrl drops explicit-port URLs (returns input unchanged shape)', async () => {
  // The userscript collectFormPaginationStates early-returns [] on explicit-port URLs;
  // buildSyntheticUrl itself still constructs via URL (which keeps the port). We assert
  // the __ycl_* params are present and the port is preserved (mirrors userscript).
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const u = mod.buildSyntheticUrl('http://x.edu.cn:8080/list', 'f', 'p', 1);
    assert.ok(u.includes('__ycl_kind=form'));
    assert.ok(u.includes(':8080'));
  } finally { await cleanup(); }
});

// collectFormPaginationStates: drive it with a fake document mirroring the userscript's reads.
function fakeDoc({ forms = [], anchors = [], inputs = [], currentPageText = '0', locationHref = 'https://xjtu.edu.cn/list' } = {}) {
  return {
    forms: makeFormsCollection(forms),
    querySelectorAll(sel) {
      if (sel === 'a[href^="javascript:"]') return anchors;
      if (sel === 'input[name]') return inputs;
      if (sel === '*') return [];
      return [];
    },
    querySelector(sel) {
      if (sel === '.this-page') return currentPageText ? { textContent: currentPageText } : null;
      return null;
    },
    links: { length: 10 },
    location: { href: locationHref },
  };
}
function makeFormsCollection(forms) {
  // document.forms supports namedItem + numeric index
  const col = Object.assign((i) => forms[i], {
    namedItem(name) { return forms.find((f) => f.name === name) ?? null; },
    length: forms.length,
  });
  for (let i = 0; i < forms.length; i++) col[i] = forms[i];
  return col;
}
function makeForm(name, { action = '', method = 'get', elements = [], submit = () => {} } = {}) {
  const elts = makeElementsCollection(elements);
  return { name, action, method, elements: elts, _submit: submit, submit() { this._submit(); } };
}
function makeElementsCollection(elements) {
  const col = Object.assign((i) => elements[i], {
    namedItem(name) { return elements.find((e) => e.name === name) ?? null; },
    length: elements.length,
  });
  for (let i = 0; i < elements.length; i++) col[i] = elements[i];
  return col;
}
function makeInput(name, { type = 'text', value = '', form = null, checked = true } = {}) {
  const input = { name, type, value, form, _value: value };
  Object.defineProperty(input, 'value', { get() { return this._value; }, set(v) { this._value = v; }, configurable: true });
  return input;
}

test('collectFormPaginationStates builds one state per javascript: page-assignment anchor', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const anchors = [
      { getAttribute: () => 'javascript:document.forms[\'pageForm\'].PAGENUM.value=2' },
      { getAttribute: () => 'javascript:document.forms[\'pageForm\'].PAGENUM.value=3' },
    ];
    const doc = fakeDoc({ forms: [makeForm('pageForm')], anchors, inputs: [] });
    const states = mod.collectFormPaginationStates('https://xjtu.edu.cn/list', doc);
    assert.equal(states.length, 2);
    assert.equal(states[0].page_index, 2);
    assert.equal(states[0].form_name, 'pageForm');
    assert.equal(states[0].fields.PAGENUM, '2');
    assert.equal(states[0].submit, true);
    assert.equal(states[0].synthetic_url, 'https://xjtu.edu.cn/list?__ycl_kind=form&__ycl_form=pageForm&__ycl_field=PAGENUM&__ycl_page=2');
    assert.equal(states[1].synthetic_url, 'https://xjtu.edu.cn/list?__ycl_kind=form&__ycl_form=pageForm&__ycl_field=PAGENUM&__ycl_page=3');
  } finally { await cleanup(); }
});

test('collectFormPaginationStates expands GOPAGE field to 1..max when a *GOPAGE input exists', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const anchors = [{ getAttribute: () => 'javascript:document.forms[\'pageForm\'].currentGOPAGE.value=3' }];
    const form = makeForm('pageForm');
    const doc = fakeDoc({ forms: [form], anchors, inputs: [makeInput('currentGOPAGE', { form })] });
    const states = mod.collectFormPaginationStates('https://xjtu.edu.cn/list', doc);
    // expandPageIndexes returns 1..3; pageIndex<=1 skipped → states for page 2 and 3
    const pages = states.map((s) => s.page_index).sort((a, b) => a - b);
    assert.deepEqual(pages, [2, 3]);
  } finally { await cleanup(); }
});

test('performFetchAction sets control values and submits when submit !== false', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    let submitted = false;
    const fld = makeInput('PAGENUM', { value: '1' });
    const form = makeForm('pageForm', { elements: [fld], submit: () => { submitted = true; } });
    const doc = fakeDoc({ forms: [form] });
    const ok = mod.performFetchAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '5' }, submit: true }, doc);
    assert.equal(ok, true);
    assert.equal(fld.value, '5');
    assert.equal(submitted, true);
  } finally { await cleanup(); }
});

test('performFetchAction returns false when the form is missing', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const doc = fakeDoc({ forms: [] });
    assert.equal(mod.performFetchAction({ kind: 'form_submit', form_name: 'nope', fields: {}, submit: true }, doc), false);
  } finally { await cleanup(); }
});

test('performFetchAction does NOT submit when action.submit === false', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    let submitted = false;
    const fld = makeInput('PAGENUM', { value: '1' });
    const form = makeForm('pageForm', { elements: [fld], submit: () => { submitted = true; } });
    const doc = fakeDoc({ forms: [form] });
    mod.performFetchAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '5' }, submit: false }, doc);
    assert.equal(submitted, false);
    assert.equal(fld.value, '5', 'control still set');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='buildSyntheticUrl|collectFormPaginationStates|performFetchAction' 2>&1 | tail -15`
Expected: FAIL — `../src/content/formActions.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/formActions.ts`:
```ts
/** Form-action logic — a byte-aligned port of userscripts/src/formPagination.ts
 *  (spec §4.1) PLUS the new side-effect-free prepare stage (amend §5.1). The
 *  byte-aligned collectFormPaginationStates / performFetchAction / buildSyntheticUrl
 *  read the same fields the userscript reads off `document`, but from an injected
 *  `doc` so they are unit-testable. The synthetic_url construction MUST byte-match
 *  the userscript (the duplicate-graph-node bug trap).
 *
 *  prepareFormAction (amend §5.1) is NEW: it finds the form, parses action/method,
 *  computes targetUrl (GET merges successful controls + FetchAction.fields into a
 *  query URL so §2.1 requestId URL-match holds; POST uses the parsed absolute
 *  form.action), and computes preparationFingerprint = sha256Hex(canonical snapshot).
 *  It mutates NOTHING. targetUrl must pass allowed-host + same-crawl-site gate or
 *  the action is NOT performed (offsite skip). */

import type { FetchAction, PaginationState } from '../shared/types.js';
import { isAllowedFetchHost } from '../shared/hostPolicy.js';
import { sameCrawlSite } from '../shared/urlGate.js';
import { canonicalizeActionSnapshot } from './fingerprint.js';
import { sha256Hex } from './fingerprint.js';

const PAGE_ASSIGN_RE =
  /document\.forms\[['"]([^'"]+)['"]\]\.([A-Za-z0-9_]+)\.value\s*=\s*['"]?(\d+)['"]?/i;
const GOTO_FIELD_RE = /\b([A-Za-z0-9_]*?)GOPAGE\b/i;

export interface FormElement {
  name: string;
  type: string;
  value: string;
  form?: { name: string } | null;
  disabled?: boolean;
  checked?: boolean;
  // for multiple-select/checkbox we read `.value` like the userscript does
}
export interface FormEl {
  name: string;
  action: string;
  method: string;
  enctype?: string;
  target?: string;
  elements: { namedItem(name: string): FormElement | FormElementCollection | null; length: number; [index: number]: FormElement | FormElementCollection };
  submit(): void;
}
export interface FormElementCollection {
  value: string;
  // RadioNodeList-like; the userscript reads `.value`
}
export interface FormDoc {
  forms: { namedItem(name: string): FormEl | null; length: number; [index: number]: FormEl };
  querySelectorAll(selector: string): Array<{ getAttribute?(n: string): string | null; name?: string; form?: { name: string } | null; textContent?: string }>;
  querySelector(selector: string): { textContent: string } | null;
  links: { length: number };
  location: { href: string };
}

export function collectFormPaginationStates(currentUrl: string, doc: FormDoc): PaginationState[] {
  if (hasExplicitPort(currentUrl)) return [];
  const anchors = doc.querySelectorAll('a[href^="javascript:"]');
  const byFormField = new Map<string, { formName: string; fieldName: string; pages: Set<number> }>();

  for (const anchor of anchors) {
    const parsed = parsePageAssignment(anchor.getAttribute?.('href') ?? '');
    if (!parsed) continue;
    const key = `${parsed.formName} ${parsed.fieldName}`;
    const existing = byFormField.get(key) ?? { formName: parsed.formName, fieldName: parsed.fieldName, pages: new Set<number>() };
    existing.pages.add(parsed.pageIndex);
    byFormField.set(key, existing);
  }

  const currentPage = detectCurrentPage(currentUrl, doc);
  const states: PaginationState[] = [];
  const seen = new Set<string>();
  for (const item of byFormField.values()) {
    const pageIndexes = expandPageIndexes(item.formName, item.fieldName, item.pages, doc);
    const totalPages = Math.max(...pageIndexes, ...item.pages);
    for (const pageIndex of pageIndexes) {
      if (pageIndex <= 1 || pageIndex === currentPage) continue;
      const syntheticUrl = buildSyntheticUrl(currentUrl, item.formName, item.fieldName, pageIndex);
      if (seen.has(syntheticUrl)) continue;
      seen.add(syntheticUrl);
      states.push({
        kind: 'form_submit',
        state_id: `form:${item.formName}:${item.fieldName}:${pageIndex}`,
        label: `${item.formName} 第 ${pageIndex} 页`,
        page_index: pageIndex,
        total_pages: totalPages,
        form_name: item.formName,
        fields: { [item.fieldName]: String(pageIndex) },
        submit: true,
        synthetic_url: syntheticUrl,
        url: currentUrl,
      });
    }
  }

  return states.sort((a, b) => a.page_index - b.page_index || a.synthetic_url.localeCompare(b.synthetic_url));
}

function hasExplicitPort(url: string): boolean {
  const raw = url.trim();
  const scheme = /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.exec(raw);
  let rest = '';
  if (scheme) {
    rest = raw.slice(scheme[0].length);
  } else if (raw.startsWith('//')) {
    rest = raw.slice(2);
  } else {
    return false;
  }
  const authority = rest.split(/[/?#]/, 1)[0] ?? '';
  const hostport = authority.split('@').at(-1) ?? '';
  if (hostport.startsWith('[')) {
    const closing = hostport.indexOf(']');
    return closing !== -1 && hostport.slice(closing + 1).startsWith(':');
  }
  return hostport.includes(':');
}

export function buildSyntheticUrl(url: string, formName: string, fieldName: string, pageIndex: number): string {
  const parsed = new URL(url);
  for (const key of [...parsed.searchParams.keys()]) {
    if (key.startsWith('__ycl_')) parsed.searchParams.delete(key);
  }
  parsed.searchParams.set('__ycl_kind', 'form');
  parsed.searchParams.set('__ycl_form', formName);
  parsed.searchParams.set('__ycl_field', fieldName);
  parsed.searchParams.set('__ycl_page', String(pageIndex));
  parsed.hash = '';
  return parsed.toString();
}

export function performFetchAction(action: FetchAction | null | undefined, doc: FormDoc): boolean {
  if (!action || action.kind !== 'form_submit') return false;
  const formName = action.form_name || '';
  const form = doc.forms.namedItem(formName);
  if (!form) return false;
  for (const [name, value] of Object.entries(action.fields ?? {})) {
    const control = form.elements.namedItem(name);
    if (!control) continue;
    setControlValue(control, value);
  }
  if (action.submit !== false) {
    form.submit();
  }
  return true;
}

function setControlValue(control: FormElement | FormElementCollection, value: string): void {
  if ('value' in control) {
    (control as FormElement).value = value;
  }
}

function parsePageAssignment(href: string): { formName: string; fieldName: string; pageIndex: number } | null {
  const match = PAGE_ASSIGN_RE.exec(href);
  if (!match) return null;
  const pageIndex = Number(match[3]);
  if (!Number.isFinite(pageIndex) || pageIndex <= 0) return null;
  return { formName: match[1] ?? '', fieldName: match[2] ?? '', pageIndex };
}

function expandPageIndexes(formName: string, fieldName: string, pages: Set<number>, doc: FormDoc): number[] {
  const inputs = doc.querySelectorAll('input[name]');
  const hasGoto = inputs.some((input) => {
    const name = input.name || '';
    const match = GOTO_FIELD_RE.exec(name);
    if (!match) return false;
    const prefix = match[1] || '';
    const form = input.form;
    return (!form || form.name === formName) && (!prefix || fieldName.toLowerCase().startsWith(prefix.toLowerCase()));
  });
  if (!hasGoto) return [...pages].sort((a, b) => a - b);
  const maxPage = Math.max(...pages);
  return Array.from({ length: maxPage }, (_unused, index) => index + 1);
}

function detectCurrentPage(currentUrl: string, doc: FormDoc): number {
  const current = Number(doc.querySelector('.this-page')?.textContent?.trim() || '0');
  if (Number.isFinite(current) && current > 0) return current;
  try {
    const url = new URL(currentUrl);
    for (const key of ['PAGENUM', 'page', 'p', 'pn', 'fromWenNOWPAGE']) {
      const value = Number(url.searchParams.get(key) || '0');
      if (Number.isFinite(value) && value > 0) return value;
    }
  } catch {
    // ignore
  }
  return 1;
}

// ---- NEW: side-effect-free prepare stage (amend §5.1) ----

export interface PrepareResult {
  ok: boolean;
  targetUrl?: string;
  method?: string;
  expectedEffect?: 'new_document' | 'same_document' | 'unknown';
  preparationFingerprint?: string;
  error?: string;
}

/** Resolve a form action's absolute target URL from the parsed form.action + the
 *  document's base URL (mirrors how the browser resolves a relative form action). */
function resolveActionUrl(formAction: string, baseUrl: string): string {
  try {
    return new URL(formAction, baseUrl).toString();
  } catch {
    return formAction;
  }
}

/** Compute the query URL a GET form would actually request after merging
 *  FetchAction.fields into the successful controls (no DOM mutation). */
function buildGetTargetUrl(baseUrl: string, form: FormEl, action: FetchAction): string {
  const target = resolveActionUrl(form.action || baseUrl, baseUrl);
  const u = new URL(target);
  // start from the form's existing successful controls (virtual apply)
  for (const [name, value] of virtualSuccessfulControls(form, action)) {
    u.searchParams.set(name, value);
  }
  return u.toString();
}

/** Virtual successful-controls snapshot WITHOUT mutating the DOM (amend §5.1):
 *  applies FetchAction.fields on top, ignores disabled/unnamed/unchecked, keeps
 *  duplicate names and multiple-select values. Returns [name, value] tuples in DOM order. */
function virtualSuccessfulControls(form: FormEl, action: FetchAction): Array<[string, string]> {
  const fieldOverrides = new Map<string, string>();
  for (const [k, v] of Object.entries(action.fields ?? {})) fieldOverrides.set(k, v);
  const out: Array<[string, string]> = [];
  for (let i = 0; i < form.elements.length; i++) {
    const el = form.elements[i] as FormElement | undefined;
    if (!el || !el.name) continue;
    if (el.disabled) continue;
    const type = (el.type || '').toLowerCase();
    if (type === 'checkbox' || type === 'radio') {
      if (el.checked === false) continue;
    }
    const override = fieldOverrides.get(el.name);
    const value = override !== undefined ? override : el.value;
    out.push([el.name, value]);
  }
  return out;
}

/** Side-effect-free prepare (amend §5.1). Returns the parsed action + fingerprint.
 *  Mutates NOTHING. ok===false when the form is missing, the target fails the
 *  host/same-site gate (offsite_redirect — do NOT perform), or the form cannot be
 *  parsed. expectedEffect: 'new_document' for POST / non-trivial method, 'same_document'
 *  heuristic otherwise (the CS may refine via navigationExpected at perform time). */
export async function prepareFormAction(action: FetchAction, doc: FormDoc): Promise<PrepareResult> {
  if (!action || action.kind !== 'form_submit') return { ok: false, error: 'not_form_submit' };
  const formName = action.form_name || '';
  const form = doc.forms.namedItem(formName);
  if (!form) return { ok: false, error: 'form_not_found' };
  const baseUrl = doc.location.href;
  const method = (form.method || 'get').toUpperCase();
  let targetUrl: string;
  if (method === 'GET') {
    targetUrl = buildGetTargetUrl(baseUrl, form, action);
  } else {
    targetUrl = resolveActionUrl(form.action || baseUrl, baseUrl);
  }
  // allowed-host + same-crawl-site gate (amend §5.1): offsite target → do not perform.
  try {
    const host = new URL(targetUrl).hostname.toLowerCase();
    if (!isAllowedFetchHost(host)) return { ok: false, error: 'offsite_redirect' };
    if (!sameCrawlSite(targetUrl, baseUrl)) return { ok: false, error: 'offsite_redirect' };
  } catch {
    return { ok: false, error: 'invalid_target_url' };
  }
  const snapshot = canonicalizeActionSnapshot(form, action, doc);
  const preparationFingerprint = await sha256Hex(snapshot);
  const expectedEffect: 'new_document' | 'same_document' | 'unknown' =
    method === 'GET' ? 'same_document' : 'new_document';
  return { ok: true, targetUrl, method, expectedEffect, preparationFingerprint };
}
```

- [ ] **Step 4: Run the byte-aligned half to verify it passes**

Run: `npm test -- --test-name-pattern='buildSyntheticUrl|collectFormPaginationStates|performFetchAction' 2>&1 | tail -15`
Expected: This task depends on Task 4's `fingerprint.ts` (`canonicalizeActionSnapshot`/`sha256Hex`) for the `prepareFormAction` import to resolve. **If Task 4 is not yet landed, this import fails and the module won't bundle.** Execute Task 4 first (see dependency note). After Task 4 lands, all 8 tests here pass.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite green (count = baseline-before-Task-3 + Task-4-new + 8 new here). The exact number is not load-bearing — `fail 0` is.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/content/formActions.ts browserext/tests/content/formActions.test.mjs
git commit -m "feat(browserext): byte-aligned form pagination port + side-effect-free prepare (amend §5.1)"
```

---

## Task 4: content/fingerprint.ts — canonical snapshot + SHA-256

> **Execute this task BEFORE Task 3** (Task 3's `prepareFormAction` imports from here).

**Files:**
- Create: `browserext/src/content/fingerprint.ts`
- Create: `browserext/tests/content/fingerprint.test.mjs`

**Interfaces:**
- Consumes: `FetchAction` (type) from `../shared/types.js`; the `FormEl`/`FormDoc` shapes from `./formActions.js` (Task 3 — type-only import). To avoid a circular *value* import, `canonicalizeActionSnapshot` takes a structural `ActionSnapshotInput` (re-declared locally, structurally compatible with `FormEl`) rather than importing the `FormEl` type — keeping `fingerprint.ts` importable before `formActions.ts` exists. (Type-only structural compatibility; no runtime cycle.)
- Produces (pure):
  - `canonicalizeActionSnapshot(form: ActionSnapshotInput, action: FetchAction, doc?: { location: { href: string } }): string` — a stable UTF-8 JSON string of the canonical snapshot (amend §5.1): form identity (index in `document.forms`, `name`, `id`), submission attributes (resolved absolute `action`, uppercased `method`, `enctype`, `target`), action input (`FetchAction.fields` sorted by key + `submit`), successful controls as `(name, type, value)` tuples in DOM order with the virtual apply of `FetchAction.fields` (disabled/unnamed/unchecked ignored; duplicate names + multiple-select kept; file inputs record name/size/type/lastModified only; submit buttons NOT counted — `form.submit()` has no submitter). **Must be deterministic and order-stable.**
  - `sha256Hex(text: string, hashFn?): Promise<string>` — SHA-256 of the UTF-8 bytes → lowercase hex. Default `hashFn` uses `crypto.subtle.digest`. Injectable so tests use a deterministic fake.
  - `PreparationSnapshot` (the structured object, for inspection/tests).

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/fingerprint.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function form(o = {}) {
  return {
    _index: 0, name: 'pageForm', id: null, action: '', method: 'get', enctype: '', target: '',
    elements: [],
    ...o,
  };
}
function elt(name, type, value, extra = {}) { return { name, type, value, ...extra }; }

test('canonicalizeActionSnapshot is deterministic + keys are sorted', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const f = form({ elements: [elt('b', 'text', '2'), elt('a', 'text', '1')] });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { b: '5' }, submit: true };
    const s1 = mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } });
    const s2 = mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } });
    assert.equal(s1, s2, 'deterministic');
    // action.fields sorted by key (a before b would only apply if both present; here fields has only 'b')
    assert.ok(s1.includes('"fields":{"b":"5"}'));
  } finally { await cleanup(); }
});

test('fingerprint is sensitive to action target/method change (amend §8.1 #19)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fakeHash = async (t) => `h(${t.length})`;
    const f = form({ action: 'https://x.edu.cn/search', method: 'get' });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { q: 'a' } };
    const fpGet = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    const fpPost = await mod.sha256Hex(mod.canonicalizeActionSnapshot({ ...f, method: 'post' }, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    assert.notEqual(fpGet, fpPost, 'method change → different fingerprint');
  } finally { await cleanup(); }
});

test('fingerprint is sensitive to a control value change (amend §8.1 #19)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fakeHash = async (t) => `h(${t.length})`;
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: {} };
    const f1 = form({ elements: [elt('page', 'text', '2')] });
    const f2 = form({ elements: [elt('page', 'text', '3')] });
    const a = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f1, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    const b = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f2, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    assert.notEqual(a, b, 'control value change → different fingerprint');
  } finally { await cleanup(); }
});

test('fingerprint is sensitive to FetchAction.fields change (amend §8.1 #19)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fakeHash = async (t) => `h(${t.length})`;
    const f = form({ elements: [elt('page', 'text', '1')] });
    const a = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f, { kind: 'form_submit', form_name: 'pageForm', fields: { page: '2' } }, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    const b = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f, { kind: 'form_submit', form_name: 'pageForm', fields: { page: '9' } }, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    assert.notEqual(a, b, 'fields change → different fingerprint');
  } finally { await cleanup(); }
});

test('virtual apply: FetchAction.fields override control value; disabled/unchecked ignored (amend §5.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const f = form({ elements: [
      elt('page', 'text', '1'),
      elt('cb', 'checkbox', 'on', { checked: false }),   // unchecked → ignored
      elt('cb2', 'checkbox', 'on', { checked: true }),
      elt('dis', 'text', 'x', { disabled: true }),        // disabled → ignored
    ] });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { page: '5' } };
    const snap = mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } });
    // page overridden to '5'; cb ignored; cb2 kept; dis ignored
    assert.ok(snap.includes('"page","text","5"'), 'field override applied');
    assert.ok(!snap.includes('"dis"'), 'disabled ignored');
    assert.ok(snap.includes('"cb2"'), 'checked checkbox kept');
    assert.ok(!snap.includes('"cb"'), 'unchecked checkbox ignored');
  } finally { await cleanup(); }
});

test('sha256Hex default uses crypto.subtle (real digest, 64 hex chars)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fp = await mod.sha256Hex('hello');
    assert.equal(fp.length, 64);
    assert.match(fp, /^[0-9a-f]{64}$/);
    // known SHA-256 of "hello"
    assert.equal(fp, '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='canonicalizeActionSnapshot|fingerprint is sensitive|virtual apply|sha256Hex default' 2>&1 | tail -15`
Expected: FAIL — `../src/content/fingerprint.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/fingerprint.ts`:
```ts
/** Form-action preparation fingerprint (amend §5.1). The fingerprint is SHA-256
 *  over a canonical JSON snapshot of the form's identity + submission attributes +
 *  the FetchAction input + the successful controls (virtual-applied, no DOM mutation).
 *  prepareFormAction and the CS perform step MUST use the SAME implementation, so a
 *  mismatch between prepare-time and perform-time proves the form changed (amend §5.3).
 *
 *  canonicalizeActionSnapshot is pure + synchronous + deterministic (sorted keys, DOM
 *  order for controls). sha256Hex is async (crypto.subtle) and injectable for tests. */

import type { FetchAction } from '../shared/types.js';

export interface ActionSnapshotControl {
  name: string;
  type: string;
  value: string;
}
export interface ActionSnapshotFileMeta {
  name: string;
  size: number;
  type: string;
  lastModified: number;
}
export interface ActionSnapshotInputElement {
  name: string;
  type: string;
  value: string;
  disabled?: boolean;
  checked?: boolean;
  // file inputs expose files (a list of {name,size,type,lastModified}); we record meta only
  files?: Array<{ name: string; size: number; type: string; lastModified: number }>;
  form?: { name: string } | null;
}
export interface ActionSnapshotForm {
  _index: number;          // index in document.forms
  name: string;
  id: string | null;
  action: string;
  method: string;
  enctype: string;
  target: string;
  elements: { length: number; [index: number]: ActionSnapshotInputElement | { value: string } };
}
export interface ActionSnapshotDoc {
  location: { href: string };
}

export interface PreparationSnapshot {
  form: { index: number; name: string; id: string | null };
  submission: { action: string; method: string; enctype: string; target: string };
  actionInput: { fields: Array<[string, string]>; submit: boolean };
  successfulControls: Array<[string, string, string]>;   // [name, type, value]
}

/** Build the canonical snapshot object (deterministic). */
function buildSnapshot(form: ActionSnapshotForm, action: FetchAction, doc: ActionSnapshotDoc): PreparationSnapshot {
  const baseUrl = doc.location.href;
  const resolvedAction = resolveUrl(form.action || baseUrl, baseUrl);
  const method = (form.method || 'get').toUpperCase();
  const enctype = form.enctype || '';
  const target = form.target || '';

  const fields: Array<[string, string]> = Object.entries(action.fields ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const fieldOverrides = new Map<string, string>(fields);

  const successfulControls: Array<[string, string, string]> = [];
  for (let i = 0; i < form.elements.length; i++) {
    const el = form.elements[i] as ActionSnapshotInputElement | undefined;
    if (!el || !el.name) continue;
    if (el.disabled) continue;
    const type = (el.type || '').toLowerCase();
    if (type === 'checkbox' || type === 'radio') {
      if (el.checked === false) continue;
    }
    const override = fieldOverrides.get(el.name);
    const value = override !== undefined ? override : el.value;
    successfulControls.push([el.name, type, value]);
  }

  return {
    form: { index: form._index, name: form.name, id: form.id },
    submission: { action: resolvedAction, method, enctype, target },
    actionInput: { fields, submit: action.submit !== false },
    successfulControls,
  };
}

function resolveUrl(ref: string, base: string): string {
  try {
    return new URL(ref, base).toString();
  } catch {
    return ref;
  }
}

/** Canonicalize to a stable UTF-8 JSON string (sorted top-level keys; fields sorted;
 *  controls in DOM order). The exact key ordering is part of the contract — prepare
 *  and perform must serialize identically. */
export function canonicalizeActionSnapshot(form: ActionSnapshotForm, action: FetchAction, doc: ActionSnapshotDoc): string {
  const snap = buildSnapshot(form, action, doc);
  return stableJson(snap);
}

/** Stable JSON: object keys emitted in insertion order EXCEPT actionInput.fields which
 *  is pre-sorted; arrays preserve order. No replacer reordering needed because
 *  buildSnapshot already sorted fields and kept DOM order for controls. */
function stableJson(value: unknown): string {
  return JSON.stringify(value);
}

export type { PreparationSnapshot };

/** SHA-256(text) → lowercase hex. Uses crypto.subtle by default (available in both the
 *  content-script DOM global and MV3 SW). Injectable for deterministic tests. */
export async function sha256Hex(text: string, hashFn?: (data: Uint8Array) => Promise<ArrayBuffer>): Promise<string> {
  const data = new TextEncoder().encode(text);
  const digest = hashFn
    ? await hashFn(data)
    : await crypto.subtle.digest('SHA-256', data);
  const bytes = new Uint8Array(digest);
  let hex = '';
  for (const b of bytes) hex += b.toString(16).padStart(2, '0');
  return hex;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern='canonicalizeActionSnapshot|fingerprint is sensitive|virtual apply|sha256Hex default' 2>&1 | tail -15`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 125 fail 0` (119 + 6 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/content/fingerprint.ts browserext/tests/content/fingerprint.test.mjs
git commit -m "feat(browserext): canonical action snapshot + SHA-256 fingerprint (amend §5.1)"
```

> After this commit, return to Task 3 (`formActions.ts`): its `prepareFormAction` import now resolves, and Task 3's tests pass. Land Task 3's commit after this one.

---

## Task 5: shared/rpcLedger.ts — per-document rpcId ledger

**Files:**
- Create: `browserext/src/shared/rpcLedger.ts`
- Create: `browserext/tests/shared/rpcLedger.test.mjs`

**Interfaces:**
- Consumes: `ActionPrepared`/`ActionResult` (types) from `../shared/rpc.js`.
- Produces (pure, amend §4.3):
  - `RpcLedgerEntry = { stage: 'received' } | { stage: 'done'; result: ActionResult }`.
  - `RpcLedger` interface: `has(rpcId): boolean` / `get(rpcId): RpcLedgerEntry | undefined` / `markReceived(rpcId): void` (insert `{stage:'received'}` if absent — idempotent) / `markDone(rpcId, result: ActionResult): void` (set `{stage:'done', result}`) / `clear(rpcId): void`.
  - `createRpcLedger(): RpcLedger` — an in-memory `Map<string, RpcLedgerEntry>`. Per-document (the CS creates one per document lifecycle); no persistence.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/shared/rpcLedger.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function actionResult(o = {}) {
  return { op: 'ACTION_RESULT', rpcId: 'r1', jobId: 'j1', ok: true, invoked: true, navigationExpected: true, effectApplied: false, ...o };
}

test('markReceived inserts received; idempotent on repeat', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    assert.equal(l.has('r1'), false);
    l.markReceived('r1');
    assert.equal(l.get('r1').stage, 'received');
    l.markReceived('r1');   // repeat → still received, no throw
    assert.equal(l.get('r1').stage, 'received');
  } finally { await cleanup(); }
});

test('markDone stores the cached ActionResult', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    const r = actionResult();
    l.markDone('r1', r);
    assert.equal(l.get('r1').stage, 'done');
    assert.equal(l.get('r1').result, r);
  } finally { await cleanup(); }
});

test('clear removes an entry', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    l.markReceived('r1');
    l.clear('r1');
    assert.equal(l.has('r1'), false);
    assert.equal(l.get('r1'), undefined);
  } finally { await cleanup(); }
});

test('a done entry is still received-safe: get returns done (amend §4.3 repeat re-emits cached)', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    const r = actionResult();
    l.markDone('r1', r);
    // a repeat PERFORM_ACTION for r1 must NOT re-execute; the router checks stage==='done' and re-emits
    const entry = l.get('r1');
    assert.equal(entry.stage, 'done');
    assert.equal(entry.result.ok, true);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='markReceived inserts|markDone stores|clear removes|done entry' 2>&1 | tail -15`
Expected: FAIL — `../src/shared/rpcLedger.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/shared/rpcLedger.ts`:
```ts
/** Per-document in-memory rpcId ledger (amend §4.3). The content script keeps one
 *  per document lifecycle. For PERFORM_ACTION: insert {stage:'received'} before
 *  returning the transport receipt; the first message for an rpcId executes the action
 *  and caches a small ActionResult as {stage:'done'}; repeats for that rpcId NEVER
 *  re-execute the form — they re-emit the cached result (or just confirm receipt if
 *  no result yet). PREPARE_ACTION is side-effect-free and re-runnable (the router may
 *  cache or recompute). CAPTURE is side-effect-free; large HTML is NOT cached here.
 *  Pure — no chrome, no DOM, no persistence. */

import type { ActionResult } from './rpc.js';

export type RpcLedgerEntry =
  | { stage: 'received' }
  | { stage: 'done'; result: ActionResult };

export interface RpcLedger {
  has(rpcId: string): boolean;
  get(rpcId: string): RpcLedgerEntry | undefined;
  markReceived(rpcId: string): void;
  markDone(rpcId: string, result: ActionResult): void;
  clear(rpcId: string): void;
}

export function createRpcLedger(): RpcLedger {
  const entries = new Map<string, RpcLedgerEntry>();
  return {
    has(rpcId) { return entries.has(rpcId); },
    get(rpcId) { return entries.get(rpcId); },
    markReceived(rpcId) {
      if (!entries.has(rpcId)) entries.set(rpcId, { stage: 'received' });
    },
    markDone(rpcId, result) { entries.set(rpcId, { stage: 'done', result }); },
    clear(rpcId) { entries.delete(rpcId); },
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern='markReceived inserts|markDone stores|clear removes|done entry' 2>&1 | tail -15`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 129 fail 0` (125 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/shared/rpcLedger.ts browserext/tests/shared/rpcLedger.test.mjs
git commit -m "feat(browserext): per-document rpcId ledger (amend §4.3)"
```

---

## Task 6: content/rpcRouter.ts — CS work-RPC handler (prepare/perform/capture + second detection)

**Files:**
- Create: `browserext/src/content/rpcRouter.ts`
- Create: `browserext/tests/content/rpcRouter.test.mjs`
- Modify: `browserext/src/content/index.ts` (wire router into `onAllowedHost` + emit `PAGE_READY` — still gated)

**Interfaces:**
- Consumes: `prepareFormAction` from `./formActions.js`, `performFetchAction`/`collectFormPaginationStates` from `./formActions.js`, `detectPage` from `./pageDetect.js`, `serializeWithoutOverlay`/`isCaptureStable` from `./capture.js`, `createRpcLedger` from `../shared/rpcLedger.js`, `SwToCs`/`CsToSw`/`MessageReceipt` (types) from `../shared/rpc.js`, `FetchAction`/`PaginationState` (types) from `../shared/types.js`.
- Produces: `createRpcRouter(deps)` returning `{ handle(message, sender): Promise<MessageReceipt> }` where `deps = { document, runtime, ledgerFactory?, now? }`:
  - `runtime` = `{ sendMessage(msg): Promise<unknown>; onMessage(cb): void }` (the injected `chrome.runtime` surface — `sendMessage` posts to the SW; `onMessage` registers the CS listener). The router registers its listener on construction.
  - `document` = the `FormDoc`+pageDetect-input shape (a single injected object exposing `forms`/`querySelectorAll`/`querySelector`/`links`/`location`/`title`/`body`/`readyState`).
  - **Routing:**
    - `PREPARE_ACTION{rpcId, jobId, action}` → `prepareFormAction(action, doc)` → emit `ACTION_PREPARED{rpcId, jobId, ok, targetUrl?, method?, expectedEffect?, preparationFingerprint?, error?}`. Return `{ received: true }`.
    - `PERFORM_ACTION{rpcId, jobId, action, preparationFingerprint}` → check ledger: if `done`, re-emit cached `ACTION_RESULT` (no re-execute); else if `received`, confirm receipt only; else mark `received`, re-validate fingerprint by re-running `prepareFormAction` and comparing `preparationFingerprint` — if mismatch, emit `ACTION_RESULT{ok:false, error:'form_action_prepare_changed', invoked:false, ...}` (no side effect); else `performFetchAction(action, doc)` (mutates + submits), set `invoked:true`, emit `ACTION_RESULT{ok:true, invoked:true, navigationExpected:<expectedEffect!=='same_document'>, effectApplied: expectedEffect==='same_document'}` and cache it as `done`. Return `{ received: true }`.
    - `CAPTURE{rpcId, jobId}` → run capture (serialize without overlay) + **second detection** via `detectPage` on the current document; if second-detection fails (terminal/error), emit `CAPTURE_RESULT{rpcId, jobId, ok:false, error:'<reason>', detection}` with NO `html`; else emit `CAPTURE_RESULT{rpcId, jobId, ok:true, url, html, title, paginationStates: collectFormPaginationStates(href, doc), detection}`. Return `{ received: true }`.
  - `PAGE_READY` emission is owned by `content/index.ts` (Task 6's index.ts edit), not the router — the router handles only SW→CS work RPCs.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/rpcRouter.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function cleanDoc(o = {}) {
  return {
    title: 'Faculty',
    bodyText: 'a'.repeat(3000),
    readyState: 'complete',
    linksLength: 20,
    bodyNull: false,
    forms: { namedItem: () => null, length: 0 },
    querySelectorAll: () => [],
    querySelector: () => null,
    links: { length: 20 },
    location: { href: 'https://xjtu.edu.cn/faculty' },
    documentElement: { cloneNode: () => ({ outerHTML: '<html><body>captured</body></html>' }) },
    ...o,
  };
}
function fakeRuntime() {
  const sent = [];
  return {
    sent,
    sendMessage(msg) { sent.push(msg); return Promise.resolve({ received: true }); },
    onMessage() {},
  };
}

test('CAPTURE emits CAPTURE_RESULT with html + detection when page is clean (amend §2.3 second detection passes)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const router = mod.createRpcRouter({ document: cleanDoc(), runtime: rt });
    const receipt = await router.handle({ op: 'CAPTURE', rpcId: 'r1', jobId: 'j1' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.deepEqual(receipt, { received: true });
    assert.equal(rt.sent.length, 1);
    const m = rt.sent[0];
    assert.equal(m.op, 'CAPTURE_RESULT');
    assert.equal(m.ok, true);
    assert.ok(m.html.includes('captured'));
    assert.equal(m.detection.errorPage, false);
    assert.equal(m.detection.terminalReason, null);
  } finally { await cleanup(); }
});

test('CAPTURE second-detection fails (errorPage) → CAPTURE_RESULT ok:false, NO html (amend §2.3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const doc = cleanDoc({ title: '502 Bad Gateway', bodyText: 'x'.repeat(200) });   // errorPage true
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    await router.handle({ op: 'CAPTURE', rpcId: 'r1', jobId: 'j1' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const m = rt.sent[0];
    assert.equal(m.op, 'CAPTURE_RESULT');
    assert.equal(m.ok, false);
    assert.equal(m.html, undefined, 'no HTML on second-detection failure');
    assert.equal(m.detection.errorPage, true);
  } finally { await cleanup(); }
});

test('CAPTURE second-detection terminal not_found → CAPTURE_RESULT ok:false detection.terminalReason=not_found', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const doc = cleanDoc({ title: '404', bodyText: 'Not Found' });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    await router.handle({ op: 'CAPTURE', rpcId: 'r1', jobId: 'j1' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const m = rt.sent[0];
    assert.equal(m.ok, false);
    assert.equal(m.detection.terminalReason, 'not_found');
  } finally { await cleanup(); }
});

test('PERFORM_ACTION first call executes + caches; repeat does NOT re-execute (re-emits cached) (amend §4.3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    let submitCount = 0;
    const form = { name: 'pageForm', action: 'https://xjtu.edu.cn/list', method: 'get', enctype: '', target: '',
      elements: { namedItem: () => ({ value: '', type: 'text' }), length: 1, 0: { name: 'page', value: '1', type: 'text' } },
      submit() { submitCount += 1; } };
    const doc = cleanDoc({ forms: { namedItem: () => form, length: 1, 0: form }, querySelectorAll: () => [], location: { href: 'https://xjtu.edu.cn/list' } });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { page: '3' }, submit: true };
    // prepare first to get a fingerprint
    await router.handle({ op: 'PREPARE_ACTION', rpcId: 'r1', jobId: 'j1', action }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const prepared = rt.sent.at(-1);
    assert.equal(prepared.op, 'ACTION_PREPARED');
    assert.equal(prepared.ok, true);
    const fp = prepared.preparationFingerprint;
    // perform #1
    await router.handle({ op: 'PERFORM_ACTION', rpcId: 'r2', jobId: 'j1', action, preparationFingerprint: fp }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.equal(submitCount, 1, 'submitted once');
    // perform repeat (same rpcId) → no re-execute, re-emit cached
    await router.handle({ op: 'PERFORM_ACTION', rpcId: 'r2', jobId: 'j1', action, preparationFingerprint: fp }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.equal(submitCount, 1, 'NOT re-submitted on repeat');
    const last = rt.sent.at(-1);
    assert.equal(last.op, 'ACTION_RESULT');
  } finally { await cleanup(); }
});

test('PERFORM_ACTION fingerprint mismatch → ok:false error form_action_prepare_changed, NO submit (amend §5.3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    let submitCount = 0;
    const form = { name: 'pageForm', action: 'https://xjtu.edu.cn/list', method: 'get', enctype: '', target: '',
      elements: { namedItem: () => ({ value: '', type: 'text' }), length: 1, 0: { name: 'page', value: '1', type: 'text' } },
      submit() { submitCount += 1; } };
    const doc = cleanDoc({ forms: { namedItem: () => form, length: 1, 0: form }, location: { href: 'https://xjtu.edu.cn/list' } });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { page: '3' }, submit: true };
    await router.handle({ op: 'PERFORM_ACTION', rpcId: 'r2', jobId: 'j1', action, preparationFingerprint: 'WRONG-FP' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.equal(submitCount, 0, 'no submit on fingerprint mismatch');
    const m = rt.sent.at(-1);
    assert.equal(m.op, 'ACTION_RESULT');
    assert.equal(m.ok, false);
    assert.equal(m.error, 'form_action_prepare_changed');
    assert.equal(m.invoked, false);
  } finally { await cleanup(); }
});

test('PREPARE_ACTION offsite target → ACTION_PREPARED ok:false error offsite_redirect (amend §5.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const form = { name: 'pageForm', action: 'https://attacker.edu.cn/steal', method: 'post', enctype: '', target: '',
      elements: { namedItem: () => null, length: 0 } };
    const doc = cleanDoc({ forms: { namedItem: () => form, length: 1, 0: form }, location: { href: 'https://xjtu.edu.cn/list' } });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    await router.handle({ op: 'PREPARE_ACTION', rpcId: 'r1', jobId: 'j1', action: { kind: 'form_submit', form_name: 'pageForm', fields: {}, submit: true } }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const m = rt.sent.at(-1);
    assert.equal(m.op, 'ACTION_PREPARED');
    assert.equal(m.ok, false);
    assert.equal(m.error, 'offsite_redirect');
  } finally { await cleanup(); }
});

test('handle always returns { received: true } (transport receipt; amend §1.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const router = mod.createRpcRouter({ document: cleanDoc(), runtime: rt });
    for (const op of ['PREPARE_ACTION', 'PERFORM_ACTION', 'CAPTURE']) {
      const msg = op === 'CAPTURE' ? { op, rpcId: 'r', jobId: 'j' } : { op, rpcId: 'r', jobId: 'j', action: { kind: 'form_submit', form_name: 'x', fields: {} } };
      if (op === 'PERFORM_ACTION') msg.preparationFingerprint = 'x';
      const receipt = await router.handle(msg, { tab: { id: 1 }, frameId: 0, documentId: 'D' });
      assert.deepEqual(receipt, { received: true });
    }
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='CAPTURE emits|CAPTURE second-detection|PERFORM_ACTION first|PERFORM_ACTION fingerprint|PREPARE_ACTION offsite|handle always returns' 2>&1 | tail -15`
Expected: FAIL — `../src/content/rpcRouter.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/rpcRouter.ts`:
```ts
/** Content-script work-RPC router (amend §4.1, §4.3, §5.1, §5.3, §2.3). Receives
 *  PREPARE_ACTION / PERFORM_ACTION / CAPTURE from the SW, drives the ported form/capture/
 *  pageDetect modules + the rpcId ledger, and emits ACTION_PREPARED / ACTION_RESULT /
 *  CAPTURE_RESULT (with capture-before-second-detection). It returns a transport receipt
 *  { received: true } synchronously-ish (amend §1.1) — the operation RESULT is a separate
 *  later message. Dependency-injected so node tests drive it with a fake document + runtime.
 *
 *  The router does NOT call the backend, does NOT navigate (form.submit() runs only inside
 *  performFetchAction, dispatched by the SW's PERFORM_ACTION which the SW persisted-before),
 *  and holds NO authoritative job state. */

import { prepareFormAction, performFetchAction, collectFormPaginationStates } from './formActions.js';
import type { FormDoc } from './formActions.js';
import { detectPage } from './pageDetect.js';
import { serializeWithoutOverlay } from './capture.js';
import { createRpcLedger } from '../shared/rpcLedger.js';
import type { RpcLedger } from '../shared/rpcLedger.js';
import type { SwToCs, CsToSw, MessageReceipt } from '../shared/rpc.js';
import type { ActionPrepared, ActionResult } from './rpcTypes.js';

// `ActionPrepared` / `ActionResult` are the named aliases Task 6 Step 3b adds to
// shared/rpc.ts (Extract<CsToSw, {op:'ACTION_PREPARED'|'ACTION_RESULT'}>) and re-exports
// via content/rpcTypes.ts. If ACTION_PREPARED is absent from CsToSw the typecheck in
// Step 5 fails loudly — that is the grep/Step-3b gate's job to catch first.

export interface RpcRouterDoc extends FormDoc {
  title: string;
  bodyText: string;
  readyState: 'loading' | 'interactive' | 'complete';
  linksLength: number;
  bodyNull: boolean;
  documentElement: { cloneNode(deep: boolean): { outerHTML: string } };
}
export interface RpcRouterRuntime {
  sendMessage(message: CsToSw): Promise<unknown>;
  onMessage(cb: (message: SwToCs, sender: { tab?: { id: number }; frameId?: number; documentId?: string }) => void | Promise<MessageReceipt>): void;
}
export interface RpcRouterDeps {
  document: RpcRouterDoc;
  runtime: RpcRouterRuntime;
  now?: () => number;
}

export interface RpcRouter {
  handle(message: SwToCs, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<MessageReceipt>;
}

export function createRpcRouter(deps: RpcRouterDeps): RpcRouter {
  const doc = deps.document;
  const runtime = deps.runtime;
  const ledger: RpcLedger = createRpcLedger();

  async function emit(message: CsToSw): Promise<void> {
    await runtime.sendMessage(message);
  }

  async function handlePrepare(msg: Extract<SwToCs, { op: 'PREPARE_ACTION' }>): Promise<MessageReceipt> {
    const prepared = await prepareFormAction(msg.action, doc);
    const out: ActionPrepared = {
      op: 'ACTION_PREPARED',
      rpcId: msg.rpcId,
      jobId: msg.jobId,
      ok: prepared.ok,
      targetUrl: prepared.targetUrl,
      method: prepared.method,
      expectedEffect: prepared.expectedEffect,
      preparationFingerprint: prepared.preparationFingerprint,
      error: prepared.error,
    };
    await emit(out);
    return { received: true };
  }

  async function handlePerform(msg: Extract<SwToCs, { op: 'PERFORM_ACTION' }>): Promise<MessageReceipt> {
    // amend §4.3: repeats never re-execute.
    const existing = ledger.get(msg.rpcId);
    if (existing?.stage === 'done') {
      await emit(existing.result);
      return { received: true };
    }
    if (existing?.stage === 'received') {
      // already executing or receipted; just confirm receipt (result will come)
      return { received: true };
    }
    ledger.markReceived(msg.rpcId);
    // amend §5.3: re-validate fingerprint by recomputing prepare.
    const recomputed = await prepareFormAction(msg.action, doc);
    if (!recomputed.ok || recomputed.preparationFingerprint !== msg.preparationFingerprint) {
      const mismatch: ActionResult = {
        op: 'ACTION_RESULT', rpcId: msg.rpcId, jobId: msg.jobId,
        ok: false, invoked: false, navigationExpected: false, effectApplied: false,
        error: 'form_action_prepare_changed',
      };
      ledger.markDone(msg.rpcId, mismatch);
      await emit(mismatch);
      return { received: true };
    }
    const invoked = performFetchAction(msg.action, doc);
    const navigationExpected = recomputed.expectedEffect !== 'same_document';
    const effectApplied = recomputed.expectedEffect === 'same_document';
    const result: ActionResult = {
      op: 'ACTION_RESULT', rpcId: msg.rpcId, jobId: msg.jobId,
      ok: invoked, invoked, navigationExpected, effectApplied,
      error: invoked ? undefined : 'form_action_invoke_failed',
    };
    ledger.markDone(msg.rpcId, result);
    await emit(result);
    return { received: true };
  }

  async function handleCapture(msg: Extract<SwToCs, { op: 'CAPTURE' }>): Promise<MessageReceipt> {
    // amend §2.3: second detection before returning HTML.
    const detection = detectPage({
      title: doc.title,
      bodyText: doc.bodyText,
      readyState: doc.readyState,
      linksLength: doc.linksLength,
      bodyNull: doc.bodyNull,
    });
    const failed = detection.errorPage || detection.terminalReason !== null;
    if (failed) {
      const result = {
        op: 'CAPTURE_RESULT', rpcId: msg.rpcId, jobId: msg.jobId, ok: false,
        url: doc.location.href, detection,
        error: detection.terminalReason ?? 'error_page',
      };
      await emit(result);
      return { received: true };
    }
    const html = serializeWithoutOverlay(doc.documentElement, { stripHeader: false });
    const paginationStates = collectFormPaginationStates(doc.location.href, doc);
    const result = {
      op: 'CAPTURE_RESULT', rpcId: msg.rpcId, jobId: msg.jobId, ok: true,
      url: doc.location.href, html, title: doc.title, paginationStates, detection,
    };
    await emit(result);
    return { received: true };
  }

  return {
    async handle(message, _sender) {
      switch (message.op) {
        case 'PREPARE_ACTION': return handlePrepare(message);
        case 'PERFORM_ACTION': return handlePerform(message);
        case 'CAPTURE': return handleCapture(message);
        default: return { received: true };
      }
    },
  };
}
```

> **shared/rpc.ts augmentation note:** `CsToSw` already includes `CAPTURE_RESULT`/`ACTION_RESULT` (slice 1). It must ALSO include `ACTION_PREPARED` for this task. Verify with `grep -n "ACTION_PREPARED" browserext/src/shared/rpc.ts` before running Task 6 Step 4. If `ACTION_PREPARED` is absent from `CsToSw`, add it (amend §5.1 shape) — this is a 6-line additive change to `shared/rpc.ts` and is part of Task 6. The `content/rpcTypes.ts` import above is a local shim re-exporting the `ActionPrepared`/`ActionResult`/`CaptureResult` types from `../shared/rpc.js` to avoid the `Extract`-on-absent-variant problem; create `content/rpcTypes.ts` as:
> ```ts
> export type { ActionPrepared, ActionResult, CaptureResult } from '../shared/rpc.js';
> ```
> IF those named types are not exported from `shared/rpc.ts`, instead define them locally here (copying the amend §5.1/§5.3 shapes). Run the grep first; the typecheck in Step 5 will catch any mismatch.

- [ ] **Step 3b: Ensure shared/rpc.ts carries ACTION_PREPARED**

Run: `grep -n "ACTION_PREPARED" browserext/src/shared/rpc.ts`
If absent, add to the `CsToSw` union in `browserext/src/shared/rpc.ts` (after the `ACTION_RESULT` variant):
```ts
  | {
      op: 'ACTION_PREPARED';
      rpcId: string; jobId: string; ok: boolean;
      targetUrl?: string; method?: string;
      expectedEffect?: 'new_document' | 'same_document' | 'unknown';
      preparationFingerprint?: string;
      error?: string;
    }
```
And export the named type aliases at the bottom of `shared/rpc.ts`:
```ts
export type ActionPrepared = Extract<CsToSw, { op: 'ACTION_PREPARED' }>;
export type ActionResult = Extract<CsToSw, { op: 'ACTION_RESULT' }>;
export type CaptureResult = Extract<CsToSw, { op: 'CAPTURE_RESULT' }>;
```
Then create `browserext/src/content/rpcTypes.ts`:
```ts
export type { ActionPrepared, ActionResult, CaptureResult } from '../shared/rpc.js';
```

- [ ] **Step 4: Run the router tests to verify they pass**

Run: `npm test -- --test-name-pattern='CAPTURE emits|CAPTURE second-detection|PERFORM_ACTION first|PERFORM_ACTION fingerprint|PREPARE_ACTION offsite|handle always returns' 2>&1 | tail -15`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Wire the router into content/index.ts (gated) + emit PAGE_READY**

Edit `browserext/src/content/index.ts` — extend `bootstrapContent` so that on an allowed host it constructs the router from the real `document`/`chrome.runtime`, registers it, waits for body + DOMContentLoaded, runs `detectPage`, and emits `PAGE_READY`. Keep the whole block inside `if (EXCLUSIVE_CONTROL_ENABLED)`. Replace the file body (keep the marker + host-gate from slice 1) with:
```ts
import { isAllowedFetchHost } from '../shared/hostPolicy.js';
import { detectPage } from './pageDetect.js';
import { createRpcRouter } from './rpcRouter.js';
import type { CsToSw, SwToCs } from '../shared/rpc.js';

declare const EXCLUSIVE_CONTROL_ENABLED: boolean;

const MARKER_ATTR = 'data-dext-extension-controller';
const MARKER_VALUE = 'v1';

export interface ContentDeps {
  hostname: string;
  documentElement: {
    setAttribute(name: string, value: string): void;
    getAttribute(name: string): string | null;
    removeAttribute(name: string): void;
  };
  now?: number;
  onAllowedHost?: () => void;
}

export async function bootstrapContent(deps: ContentDeps): Promise<void> {
  if (EXCLUSIVE_CONTROL_ENABLED) {
    deps.documentElement.setAttribute(MARKER_ATTR, MARKER_VALUE);
    if (!isAllowedFetchHost(deps.hostname)) return;
    deps.onAllowedHost?.();
    // slice 4: register the work-RPC router + emit PAGE_READY (real document only).
    if (typeof document !== 'undefined' && typeof chrome !== 'undefined' && chrome.runtime) {
      const doc = {
        title: () => document.title || '',
        bodyText: () => document.body?.innerText || '',
        readyState: () => document.readyState,
        linksLength: () => document.links?.length ?? 0,
        bodyNull: () => document.body === null,
        forms: document.forms as unknown as import('./formActions.js').FormDoc['forms'],
        querySelectorAll: (sel: string) => Array.from(document.querySelectorAll(sel)),
        querySelector: (sel: string) => document.querySelector(sel),
        links: document.links,
        location: location,
        documentElement: document.documentElement,
      };
      const router = createRpcRouter({
        document: doc as unknown as Parameters<typeof createRpcRouter>[0]['document'],
        runtime: {
          sendMessage: (m) => chrome.runtime.sendMessage(m),
          onMessage: (cb) => chrome.runtime.onMessage.addListener(cb as never),
        },
      });
      chrome.runtime.onMessage.addListener((msg: SwToCs, sender) => {
        if (msg && (msg.op === 'PREPARE_ACTION' || msg.op === 'PERFORM_ACTION' || msg.op === 'CAPTURE')) {
          void router.handle(msg, sender as { tab?: { id: number }; frameId?: number; documentId?: string });
          return true;   // async response (transport receipt)
        }
        return false;
      });
      // PAGE_READY: wait for body + DOMContentLoaded, then detect + emit (slice 4).
      const sendReady = () => {
        const detection = detectPage({
          title: document.title || '',
          bodyText: document.body?.innerText || '',
          readyState: document.readyState as 'loading' | 'interactive' | 'complete',
          linksLength: document.links?.length ?? 0,
          bodyNull: document.body === null,
        });
        void chrome.runtime.sendMessage({
          op: 'PAGE_READY', url: location.href, title: document.title, detection,
        } as CsToSw);
      };
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', sendReady, { once: true });
      } else if (document.body) {
        sendReady();
      } else {
        document.addEventListener('DOMContentLoaded', sendReady, { once: true });
      }
    }
  }
}

if (typeof document !== 'undefined' && document.documentElement) {
  void bootstrapContent({
    hostname: typeof location !== 'undefined' ? location.hostname : '',
    documentElement: document.documentElement,
    now: Date.now(),
  });
}
```

> The `doc` shim above adapts the real `document` (whose `bodyText`/`readyState` are live getters, not the snapshot fields `pageDetect` expects) — `createRpcRouter`'s `RpcRouterDoc` calls `detectPage` with snapshot values, so the router reads `doc.title`/`doc.bodyText` etc. as strings. The shim supplies plain string properties by reading them at call time. If the type mismatch is awkward, an alternative is to have `createRpcRouter` accept a `readDoc(): RpcRouterDoc` function that snapshots the live document. The executor may pick whichever keeps the types clean; the behavior (read live values at CAPTURE/PAGE_READY time) is what matters.

- [ ] **Step 6: Update content/index.test.mjs (the existing 3 slice-1 tests still pass; the real-`document` block is guarded)**

The 3 existing tests pass `onAllowedHost` and assert the marker + early-return; they do NOT provide a real `document`/`chrome.runtime`, so the new `typeof document !== 'undefined' && ... chrome.runtime` guard skips the router wiring in those tests. No test change needed — but verify:
Run: `npm test -- --test-name-pattern='gated ON' 2>&1 | tail -10`
Expected: PASS — the 3 slice-1 content tests still green.

- [ ] **Step 7: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `fail 0` (count = 129 + 7 router tests = 136; exact not load-bearing).

- [ ] **Step 8: Commit**

```bash
git add browserext/src/content/rpcRouter.ts browserext/src/content/rpcTypes.ts \
        browserext/src/content/index.ts browserext/src/shared/rpc.ts \
        browserext/tests/content/rpcRouter.test.mjs
git commit -m "feat(browserext): CS work-RPC router (prepare/perform/capture + second detection) + PAGE_READY"
```

---

## Task 7: api.completeJob — POST /complete (best-effort swallow)

**Files:**
- Modify: `browserext/src/api.ts` (add `completeJob`)
- Modify: `browserext/tests/api.test.mjs` (add `completeJob` cases)

**Interfaces:**
- Consumes: `FetchJob`, `PaginationState` (types).
- Produces: `completeJob(jobId, html, url, title, paginationStates?): Promise<void>` on `ApiClient`. Body `{ html, url, title, pagination_states }` (byte-aligned with userscript `completeJob`). Best-effort: swallows errors (late/stale `/complete` after the 60s job timeout is idempotent — CLAUDE.md bug trap; the script's per-request HTTP timeout is 10s).

- [ ] **Step 1: Write the failing test**

Append to `browserext/tests/api.test.mjs`:
```js
test('completeJob POSTs /complete with html/url/title/pagination_states', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let posted = null;
    const fetchFn = async (url, init) => {
      posted = { url, init };
      return { status: 200, ok: true, json: async () => ({}) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.completeJob('job-1', '<html/>', 'https://x.edu.cn/p', 'Title', [{ kind: 'form_submit', state_id: 's', label: 'l', page_index: 2, form_name: 'f', fields: { p: '2' }, submit: true, synthetic_url: 'syn', url: 'u' }]);
    assert.equal(posted.url, 'http://127.0.0.1:21520/api/jobs/job-1/complete');
    assert.equal(posted.init.method, 'POST');
    const body = JSON.parse(posted.init.body);
    assert.equal(body.html, '<html/>');
    assert.equal(body.url, 'https://x.edu.cn/p');
    assert.equal(body.title, 'Title');
    assert.equal(body.pagination_states.length, 1);
  } finally {
    await cleanup();
  }
});

test('completeJob swallows errors (late /complete idempotency — CLAUDE.md bug trap)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNRESET'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await assert.doesNotReject(async () => { await api.completeJob('job-1', 'x', 'u', 't'); });
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --test-name-pattern='completeJob' 2>&1 | tail -10`
Expected: FAIL — `api.completeJob is not a function`.

- [ ] **Step 3: Add completeJob to api.ts**

Edit `browserext/src/api.ts`:

1. Add to the `ApiClient` interface (after `skipJob`):
```ts
  completeJob(jobId: string, html: string, url: string, title: string, paginationStates?: PaginationState[]): Promise<void>;
```

2. Add the import (top of file, extend the existing type import):
```ts
import type { FetchJob, PaginationState } from './shared/types.js';
```

3. Add the impl inside `createFetchApi` (after `skipJob`, before `sendHeartbeat`):
```ts
  async function completeJob(jobId: string, html: string, url: string, title: string, paginationStates?: PaginationState[]): Promise<void> {
    try {
      await fetch(`${base}/jobs/${jobId}/complete`, {
        method: 'POST', headers,
        body: JSON.stringify({ html, url, title, pagination_states: paginationStates ?? [] }),
      });
    } catch {
      // late/stale /complete after the 60s job timeout is idempotent (backend no-ops a stale id);
      // the script's 10s per-request timeout can also surface here. Swallow — never crash.
    }
  }
```

4. Update the final `return`:
```ts
  return { getStatus, claimNextJob, completeJob, failJob, skipJob, sendHeartbeat };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- --test-name-pattern='completeJob' 2>&1 | tail -8`
Expected: PASS — both `completeJob` tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `fail 0` (136 + 2 = 138; exact not load-bearing).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/api.ts browserext/tests/api.test.mjs
git commit -m "feat(browserext): api.completeJob (POST /complete, best-effort swallow)"
```

---

## Task 8: Controller — landed→dispatch CAPTURE + CAPTURE_RESULT + RPC recovery + complete

**Files:**
- Modify: `browserext/src/chrome.ts` (add `sendMessage(tabId, message, options): Promise<unknown>`)
- Modify: `browserext/tests/chrome.test.mjs` (widen fake + assert `sendMessage`)
- Modify: `browserext/src/controller/controller.ts` (landed dispatch + CAPTURE_RESULT handler + RPC recovery in tick + complete)
- Modify: `browserext/tests/controller/controller.test.mjs` (+ capture/complete/recovery tests)
- Modify: `browserext/src/background.ts` (no change expected — `WireDeps.chrome` already passed through; verify)

**Interfaces:**
- Consumes: `ApiClient.completeJob` (Task 7), `ChromeRuntime.sendMessage` (new this task), `PendingRpc`/`ControllerError` (state, slice 1), `CaptureResult` (type, Task 6's `shared/rpc.ts` augmentation).
- Produces:
  - `chrome.ts`: `ChromeRuntime` gains `sendMessage(tabId: number, message: unknown, options?: { documentId?: string }): Promise<unknown>` — wraps `chrome.tabs.sendMessage(tabId, message, options)`. The real impl passes `{ documentId }` so the message lands on the precise document (amend §4.1).
  - `CrawlControllerDeps.chrome` widens to `Pick<ChromeRuntime, 'getTab' | 'updateTabUrl' | 'sendMessage'>`.
  - Controller:
    - On `landed` (in `applyLandingVerdict`): dispatch `CAPTURE` — generate a stable `rpcId` (e.g. `crypto.randomUUID()`), persist `pendingRpc = { id, jobId, op:'capture', sourceDocumentId: navigation.commit.documentId, delivery:'prepared', issuedAt, resultDeadlineAt: issuedAt+30s }` + `phase:'capturing'` BEFORE `chrome.sendMessage(boundTabId, {op:'CAPTURE', rpcId, jobId}, { documentId: sourceDocumentId })`; on the promise resolving with `{ received: true }`, persist `delivery:'received'`.
    - New `deliverCaptureResult(result: CaptureResult)` (re-acquires mutex): amend §4.1 validation (`sender.tab?.id===boundTabId && sender.frameId===0 && sender.documentId===pendingRpc.sourceDocumentId && result.rpcId===pendingRpc.id && result.jobId===currentJob.id`); on mismatch → log + no-op (late/stale). On valid: if `result.ok===false` (second-detection failure) → amend §2.3 routing (clear `pendingRpc`, exit `capturing`, then: terminal skip / navigate gateway funnel / form_action fail). If `result.ok===true` → `await api.completeJob(jobId, result.html, result.url, result.title, result.paginationStates)`, clear `pendingRpc`, `navigation`, set `phase:'assigned'` if `/status` still has a job else `idle` (reconcile owns this — simplest: set `phase:'idle'`, `currentJob=null`; the next tick's reconcile confirms). Actually keep `currentJob` cached until reconcile clears it (slice-2 pattern) — set `phase:'idle'` so the next tick re-claims; the reconcile step will clear `currentJob` if the backend released it.
    - RPC-recovery budget in `tick` (amend §6.1): if `phase==='capturing'` and `pendingRpc` exists and `now >= pendingRpc.resultDeadlineAt` and `now >= nextRecoveryAt` and `recoveryAttempts < 3`: re-send the **same** `CAPTURE` rpcId, bump `recoveryAttempts` (+via a content_unavailable-style counter? NO — capture recovery is its own counter; amend §6.1 says "increase recoveryAttempts" only on actual re-send). Store `recoveryAttempts` + `nextRecoveryAt` on the capture `pendingRpc`? The state shape has `PendingRpc` without recovery fields; amend §6.1's recovery counter lives on `ControllerError.content_unavailable`. For the capture RPC (not yet an error), track recovery inline: add `recoveryAttempts` + `nextRecoveryAt` to `PendingRpc`? That changes `shared/state.ts`. **Decision:** add `recoveryAttempts: number` and `nextRecoveryAt: number | null` to `PendingRpc` (amend §1.1 already lists `resultDeadlineAt`; the recovery fields are the natural companion and amend §6.1 implies them). Update `shared/state.ts` `PendingRpc` accordingly (additive, slice-1-placed type). On budget exhaustion (3 re-sends) → set `lastError = content_unavailable{ missing:'capture_result', sourceDocumentId, since:issuedAt, recoveryAttempts:3, nextRecoveryAt:null, recoveryExhausted:true }`, `phase:'error'`, clear `pendingRpc`, keep `navigation` (landing page retained).
    - `complete` is called inside `deliverCaptureResult` on success.

> **State change:** `shared/state.ts` `PendingRpc` gains `recoveryAttempts: number` + `nextRecoveryAt: number | null` (defaults 0 / null). This is additive and consistent with amend §6.1. Update `initialControllerState`? `pendingRpc` defaults `null` so no initial-state change; the fields are set when a `pendingRpc` is created. Add the two fields to every `pendingRpc` literal the Controller constructs (capture dispatch, and Task 9's perform dispatch).

- [ ] **Step 1: Widen chrome.ts — add sendMessage**

Edit `browserext/src/chrome.ts`:
1. Add to the `ChromeRuntime` interface (after `getTab`):
```ts
  sendMessage(tabId: number, message: unknown, options?: { documentId?: string; frameId?: number }): Promise<unknown>;
```
2. Add the real impl in `createRealChromeRuntime` (after `getTab`, before `registerAlarm`):
```ts
    async sendMessage(tabId, message, options) {
      return await chrome.tabs.sendMessage(tabId, message, options ?? {});
    },
```

- [ ] **Step 2: Update chrome.test.mjs fake + add assertion**

Edit `browserext/tests/chrome.test.mjs` — in `fakeRuntime()` add:
```js
    async sendMessage(tabId, message, options) { sentMessages.push({ tabId, message, options }); return { received: true }; },
```
(declare `const sentMessages = [];` at the top of `fakeRuntime` and return it on the helper object as `sentMessages`.) Add the assertion to the existing `createRealChromeRuntime exposes nav listeners` test's method list: add `'sendMessage'` to the for-loop array.

- [ ] **Step 3: Write the failing controller tests**

Append to `browserext/tests/controller/controller.test.mjs` (after the slice-3 block):
```js
// ---- slice 4: capture dispatch + CAPTURE_RESULT + recovery + complete ----

function fakeChrome4() {
  const updates = [];
  const sent = [];
  return {
    calls: { updates, sent },
    async getTab() { return { id: 42, url: 'https://xjtu.edu.cn/job-1' }; },
    async updateTabUrl(t, url) { updates.push({ tabId: t, url }); },
    async sendMessage(tabId, message, options) { sent.push({ tabId, message, options }); return { received: true }; },
  };
}
function fakeApi4({ statusResponse, completeOk = true }) {
  const calls = { getStatus: 0, claimNextJob: 0, complete: [], fail: [], skip: [], sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async claimNextJob() { return null; },
    async completeJob(id, html, url, title, ps) { calls.complete.push({ id, html, url, title, ps }); },
    async failJob(id, msg) { calls.fail.push({ id, msg }); },
    async skipJob(id, reason) { calls.skip.push({ id, reason }); },
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}
function landedController(mod, area) {
  // build a controller already navigated to landed on job-1, doc DOC-1
  const api = fakeApi4({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
  const chr = fakeChrome4();
  const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
  return { c, api, chr, area };
}

test('landed → dispatches CAPTURE to commit.documentId with a stable rpcId; pendingRpc.delivery=prepared BEFORE send, received AFTER (amend §1.1, §4.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const s = await c.getState();
    assert.equal(s.phase, 'capturing', 'landed → capturing');
    assert.ok(s.pendingRpc, 'pendingRpc persisted');
    assert.equal(s.pendingRpc.op, 'capture');
    assert.equal(s.pendingRpc.sourceDocumentId, 'DOC-1');
    assert.equal(s.pendingRpc.delivery, 'received', 'delivery promoted to received after send resolves');
    assert.equal(chr.calls.sent.length, 1);
    assert.equal(chr.calls.sent[0].options.documentId, 'DOC-1', 'delivered to precise documentId');
    assert.equal(chr.calls.sent[0].message.op, 'CAPTURE');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT ok:true → complete + clear pendingRpc → idle (capture success)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, api } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: true, url: 'https://xjtu.edu.cn/job-1', html: '<html/>', title: 'T', paginationStates: [], detection: { errorPage: false, terminalReason: null } }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-1' });
    assert.deepEqual(api.calls.complete, [{ id: 'job-1', html: '<html/>', url: 'https://xjtu.edu.cn/job-1', title: 'T', ps: [] }]);
    const s = await c.getState();
    assert.equal(s.pendingRpc, null);
    assert.equal(s.navigation, null);
    assert.equal(s.phase, 'idle');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT second-detection fail (errorPage, navigate kind) → gateway funnel re-navigate (amend §2.3, §8.1 #18)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: false, url: 'u', detection: { errorPage: true, terminalReason: null }, error: 'error_page' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-1' });
    const s = await c.getState();
    assert.equal(s.pendingRpc, null, 'rpc cleared');
    assert.equal(s.phase, 'navigating', 'exited capturing → gateway funnel re-navigate');
    assert.equal(s.navigation.attempt, 2);
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT second-detection terminal not_found → skip not_found (amend §8.1 #18)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, api } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: false, url: 'u', detection: { errorPage: false, terminalReason: 'not_found' }, error: 'not_found' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-1' });
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'not_found' }]);
    const s = await c.getState();
    assert.equal(s.phase, 'error');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT with mismatched documentId → discarded (late/stale RPC, no complete) (amend §4.1, §8.1 #10)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, api } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    // wrong documentId (a new document's stale result)
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: true, url: 'u', html: 'x', title: 't', detection: { errorPage: false, terminalReason: null } }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-OTHER' });
    assert.deepEqual(api.calls.complete, [], 'mismatched documentId result discarded, no complete');
    assert.equal((await c.getState()).phase, 'capturing', 'still capturing');
  } finally { await cleanup(); }
});

test('capture RPC recovery: result deadline expired + source doc still matches → re-send SAME rpcId (amend §6.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpcBefore = (await c.getState()).pendingRpc;
    assert.equal(chr.calls.sent.length, 1);
    // advance past resultDeadlineAt (issuedAt≈5400, +30s = 35400) and past 5s recovery gap
    await c.tick(40000);
    const s = await c.getState();
    assert.equal(chr.calls.sent.length, 2, 're-sent CAPTURE');
    assert.equal(s.pendingRpc.id, rpcBefore.id, 'SAME rpcId re-used');
    assert.equal(s.pendingRpc.recoveryAttempts, 1);
  } finally { await cleanup(); }
});

test('capture RPC recovery exhausted (3 re-sends) → content_unavailable/capture_result, no more auto RPC (amend §6.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr } = landedController(mod, area);
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const sentBefore = chr.calls.sent.length;
    // three recovery windows (each must clear the 5s gap): issueAt≈5400+30s=35400; gaps at 35400+5, then +35, then +65
    await c.tick(40000);  // re-send #1
    await c.tick(45000);  // re-send #2
    await c.tick(50000);  // re-send #3 (now recoveryAttempts==3, exhausted)
    await c.tick(55000);  // NO further send
    const s = await c.getState();
    assert.equal(chr.calls.sent.length, sentBefore + 3, 'exactly 3 re-sends');
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError.kind, 'content_unavailable');
    assert.equal(s.lastError.missing, 'capture_result');
    assert.equal(s.lastError.recoveryExhausted, true);
  } finally { await cleanup(); }
});
```

- [ ] **Step 4: Run controller tests to verify they fail**

Run: `npm test -- --test-name-pattern='landed → dispatches|CAPTURE_RESULT ok:true|CAPTURE_RESULT second-detection|CAPTURE_RESULT with mismatched|capture RPC recovery' 2>&1 | tail -20`
Expected: FAIL — `c.deliverCaptureResult` does not exist / `chrome.sendMessage` not on the fake type.

- [ ] **Step 5: Modify controller.ts — capture dispatch + CAPTURE_RESULT + recovery + complete**

Edit `browserext/src/shared/state.ts` — add recovery fields to `PendingRpc`:
```ts
export interface PendingRpc {
  id: string;
  jobId: string;
  op: RpcOperation;
  sourceDocumentId: string;
  delivery: 'prepared' | 'received';
  issuedAt: number;
  resultDeadlineAt: number;
  recoveryAttempts: number;       // amend §6.1 — only incremented on actual re-send
  nextRecoveryAt: number | null;   // amend §6.1 — ≥5s gap between re-sends
}
```

Edit `browserext/src/controller/controller.ts`:

1. Widen the deps + controller interface. Change `CrawlControllerDeps.chrome` to:
```ts
  chrome?: Pick<ChromeRuntime, 'getTab' | 'updateTabUrl' | 'sendMessage'>;
```
Add `deliverCaptureResult` to the `CrawlController` interface (after `deliverPageReady`):
```ts
  deliverCaptureResult(result: CaptureResult, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void>;
```
Add the import:
```ts
import type { CaptureResult } from '../shared/rpc.js';
```

2. In `applyLandingVerdict`, the `landed` branch currently sets `phase='landed'` and returns. Replace it to dispatch capture:
```ts
    if (verdict.kind === 'landed') {
      s.navigation!.acceptedUrl = verdict.acceptedUrl;
      s.phase = 'landed';
      s.phaseStartedAt = now;
      await dispatchCapture(s, now);
      return;
    }
```
Add the `dispatchCapture` helper (after `navigateNow`):
```ts
  /** Dispatch CAPTURE on a landed page (amend §1.1, §4.1). Persist pendingRpc
   *  delivery='prepared' BEFORE sendMessage; promote to 'received' on transport receipt. */
  async function dispatchCapture(s: ControllerState, now: number): Promise<void> {
    if (!s.currentJob || s.boundTabId === null || !deps.chrome?.sendMessage) return;
    if (!s.navigation?.commit) return;
    const rpcId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `cap-${now}-${Math.random().toString(36).slice(2)}`;
    const sourceDocumentId = s.navigation.commit.documentId;
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
```

3. Add `deliverCaptureResult` (after `deliverPageReady`):
```ts
  async function deliverCaptureResult(result: CaptureResult, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const rpc = s.pendingRpc;
      if (!rpc || rpc.op !== 'capture') return;
      // amend §4.1 validation
      if (sender.tab?.id !== s.boundTabId) return;
      if (sender.frameId !== 0) return;
      if (sender.documentId !== rpc.sourceDocumentId) return;
      if (result.rpcId !== rpc.id || result.jobId !== s.currentJob?.id) return;
      // second-detection failure → amend §2.3 routing (clear rpc, exit capturing)
      if (!result.ok) {
        s.pendingRpc = null;
        const detection = result.detection;
        if (detection?.terminalReason === 'not_found' || detection?.terminalReason === 'content_removed' || detection?.terminalReason === 'empty_page') {
          // terminal skip from capturing (amend §8.1 #18)
          const reason = detection.terminalReason;
          s.phase = 'error'; s.phaseStartedAt = Date.now(); s.lastError = null; s.navigation = null;
          if (deps.api) await deps.api.skipJob(s.currentJob!.id, reason);
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
          s.phase = 'error'; s.phaseStartedAt = Date.now();
          s.lastError = { kind: 'nav_error', error: `form_action_navigation_failed:${result.error ?? 'soft_error'}` };
          s.navigation = null;
          if (deps.api) await deps.api.failJob(jobId, `form_action_navigation_failed:${result.error ?? 'soft_error'}`);
        }
        await persist();
        return;
      }
      // success → complete
      const jobId = s.currentJob!.id;
      if (deps.api) await deps.api.completeJob(jobId, result.html ?? '', result.url, result.title ?? '', result.paginationStates);
      s.pendingRpc = null;
      s.navigation = null;
      s.phase = 'idle';            // reconcile clears currentJob if backend released it
      s.phaseStartedAt = Date.now();
      s.lastError = null;
      await persist();
    } finally { release(); }
  }
```

4. In `tick`, after the slice-3 landing/deadline block (step 6) and before the `saveIfChanged` (step 7), add the capture RPC-recovery block:
```ts
        // 6.5. Capture RPC recovery (amend §6.1) — re-send same rpcId past result deadline, ≤3, ≥5s apart.
        if (s.phase === 'capturing' && s.pendingRpc && s.pendingRpc.op === 'capture') {
          const rpc = s.pendingRpc;
          if (now >= rpc.resultDeadlineAt && (rpc.nextRecoveryAt === null || now >= rpc.nextRecoveryAt) && rpc.recoveryAttempts < 3) {
            rpc.recoveryAttempts += 1;
            rpc.nextRecoveryAt = now + 5_000;
            rpc.resultDeadlineAt = now + 30_000;
            rpc.delivery = 'prepared';
            rpc.issuedAt = now;
            await persist();
            if (s.boundTabId !== null && deps.chrome?.sendMessage) {
              try {
                const receipt = await deps.chrome.sendMessage(s.boundTabId, { op: 'CAPTURE', rpcId: rpc.id, jobId: rpc.jobId }, { documentId: rpc.sourceDocumentId });
                if (receipt && (receipt as { received?: boolean }).received) { rpc.delivery = 'received'; await persist(); }
              } catch {
                // target doc gone → content_unavailable
                s.phase = 'error'; s.phaseStartedAt = now;
                s.lastError = { kind: 'content_unavailable', missing: 'capture_result', sourceDocumentId: rpc.sourceDocumentId, since: rpc.issuedAt, recoveryAttempts: rpc.recoveryAttempts, nextRecoveryAt: null, recoveryExhausted: true };
                s.pendingRpc = null;
              }
            }
          } else if (rpc.recoveryAttempts >= 3 && now >= (rpc.nextRecoveryAt ?? 0)) {
            // budget exhausted → content_unavailable/capture_result
            s.phase = 'error'; s.phaseStartedAt = now;
            s.lastError = { kind: 'content_unavailable', missing: 'capture_result', sourceDocumentId: rpc.sourceDocumentId, since: rpc.issuedAt, recoveryAttempts: rpc.recoveryAttempts, nextRecoveryAt: null, recoveryExhausted: true };
            s.pendingRpc = null;
          }
        }
```

5. Add `deliverCaptureResult` to the returned object (after `deliverPageReady`):
```ts
    deliverPageReady,
    deliverCaptureResult,
```

- [ ] **Step 6: Run controller + chrome tests to verify they pass**

Run: `npm test -- --test-name-pattern='landed → dispatches|CAPTURE_RESULT ok:true|CAPTURE_RESULT second-detection|CAPTURE_RESULT with mismatched|capture RPC recovery|createRealChromeRuntime exposes' 2>&1 | tail -20`
Expected: PASS — all new controller + chrome tests green.

- [ ] **Step 7: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `fail 0` (138 + 7 controller + 1 chrome = 146; exact not load-bearing).

- [ ] **Step 8: Commit**

```bash
git add browserext/src/chrome.ts browserext/tests/chrome.test.mjs \
        browserext/src/shared/state.ts \
        browserext/src/controller/controller.ts browserext/tests/controller/controller.test.mjs
git commit -m "feat(browserext): slice 4 capture — dispatch CAPTURE + CAPTURE_RESULT + recovery + complete (amend §1.1, §2.3, §4.1, §6.1)"
```

---

## Task 9: Controller — form-action prepare→persist→perform→confirm + failure routing

**Files:**
- Modify: `browserext/src/controller/controller.ts` (PREPARE_ACTION/ACTION_PREPARED/ACTION_RESULT handlers + same-document confirm + failure routing + form-action recovery)
- Modify: `browserext/tests/controller/controller.test.mjs` (+ form-action tests)

**Interfaces:**
- Consumes: `ActionPrepared`/`ActionResult` (types from `shared/rpc.ts`, Task 6), `FetchAction` (type), `ApiClient`.
- Produces:
  - `deliverActionPrepared(prepared: ActionPrepared, sender)` — on a valid `ACTION_PREPARED` (validate rpcId/jobId/tab.id/documentId per amend §4.1): if `ok===false` → retry prepare within budget (re-send same `PREPARE_ACTION` rpcId, ≤3, ≥5s), else fail `form_action_prepare_failed:<error>` (no `tabs.update`). If `ok===true` → **persist a new `NavigationState`** (amend §5.2: `kind:'form_action'`, `requestedUrl: prepared.targetUrl`, `sourceDocumentId: <old navigation.commit.documentId>`, `action:{expectedEffect, method, preparationFingerprint, invocationReported:false, effectConfirmed:false}`, all of `requestId`/`commit`/`http`/`pageReady`/`acceptedUrl` absent) + new stable perform rpcId + `pendingRpc.delivery:'prepared'`, `phase:'acting'`, THEN `sendMessage(boundTabId, {op:'PERFORM_ACTION', rpcId, jobId, action, preparationFingerprint}, {documentId: sourceDocumentId})`, promote to `'received'`.
  - `deliverActionResult(result: ActionResult, sender)` — validate (amend §4.1). `navigationExpected:true` → mark `action.invocationReported=true` (amend §5.3), keep `phase:'acting'`, wait §2.3 five conditions (the new main-frame `onBeforeRequest` binds a fresh requestId in `phase==='acting'` — already handled by slice-3 `deliverBeforeRequest`'s `phase==='acting'` guard). `effectApplied:true && navigationExpected:false` → same-document effect confirmed (amend §5.5): set `action.effectConfirmed=true`, re-run is NOT needed (the CS re-ran detection before CAPTURE_RESULT); transition `landed → dispatchCapture` for the source document. Budget: perform recovery only while no requestId/commit/effect yet (amend §6.1).
  - Failure routing (amend §5.6): 30s with no correlated commit/history/effect, or new-document gateway/nav_error/unexpected_status/soft-error, or source-doc destroyed without completion → fail `form_action_navigation_failed:<detail>` (client `attempt` fixed 1). The form is NEVER re-submitted; the tab is NEVER re-navigated. (429 still fails `rate_limited`; terminal still skips — these reuse the slice-3 `deliverHttpEvent`/`deliverCaptureResult` paths because the new navigation is a normal `acting`-phase navigation.)
  - `onHistoryStateUpdated` (SPA same-document): slice-3 navMonitor forwards `onHistoryStateUpdated` as a `deliverCommitted`. For a `form_action` with `effectApplied` pending, a history update whose `documentId === sourceDocumentId` and URL passes same-site gate confirms the effect (amend §5.5) → `landed → dispatchCapture`.

- [ ] **Step 1: Write the failing controller tests**

Append to `browserext/tests/controller/controller.test.mjs`:
```js
// ---- slice 4: form-action prepare→persist→perform→confirm + failure routing ----

async function landOnList(mod, area, api, chr) {
  const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
  await c.bind(42, 1000);
  await c.tick(5000);
  await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/list', requestId: 'REQ-1', timeStamp: 5100 });
  await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-LIST', url: 'https://xjtu.edu.cn/list', timeStamp: 5200 });
  await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/list', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-LIST', timeStamp: 5300 }, 'ok');
  await c.deliverPageReady({ documentId: 'DOC-LIST', url: 'https://xjtu.edu.cn/list', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
  return c;
}
function formActionJob(id = 'job-1') {
  const j = fullJob(id, 'https://xjtu.edu.cn/list');
  j.action = { kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true };
  return j;
}
function fakeApi4b({ statusResponse }) {
  const calls = { getStatus: 0, complete: [], fail: [], skip: [], sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async claimNextJob() { return null; },
    async completeJob(id, html, url, title, ps) { calls.complete.push({ id, html, url, title, ps }); },
    async failJob(id, msg) { calls.fail.push({ id, msg }); },
    async skipJob(id, reason) { calls.skip.push({ id, reason }); },
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}

test('ACTION_PREPARED ok:true → persists NEW form_action NavigationState (sourceDoc copied, slots absent) + PERFORM_ACTION dispatched (amend §5.2, §8.1 #15)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    // dispatch PREPARE_ACTION (Controller-initiated on landed? — for test we drive via a helper)
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const s0 = await c.getState();
    const prepRpc = s0.pendingRpc;
    assert.equal(prepRpc.op, 'prepare_action');
    assert.equal(prepRpc.sourceDocumentId, 'DOC-LIST');
    // CS returns ACTION_PREPARED ok:true
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list?PAGENUM=3', method: 'GET', expectedEffect: 'same_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const s = await c.getState();
    assert.equal(s.phase, 'acting');
    assert.equal(s.navigation.kind, 'form_action');
    assert.equal(s.navigation.requestedUrl, 'https://xjtu.edu.cn/list?PAGENUM=3');
    assert.equal(s.navigation.sourceDocumentId, 'DOC-LIST');
    assert.equal(s.navigation.requestId, undefined, 'requestId absent (new navigation)');
    assert.equal(s.navigation.commit, undefined, 'commit absent');
    assert.equal(s.navigation.http, undefined, 'http absent');
    assert.equal(s.navigation.action.preparationFingerprint, 'FP1');
    assert.equal(s.pendingRpc.op, 'perform_action');
    assert.equal(chr.calls.sent.at(-1).message.op, 'PERFORM_ACTION');
    assert.equal(chr.calls.sent.at(-1).options.documentId, 'DOC-LIST');
  } finally { await cleanup(); }
});

test('ACTION_PREPARED ok:false → retries prepare (same rpcId) then fails form_action_prepare_failed (no tabs.update) (amend §5.1, §6.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    for (let i = 0; i < 3; i++) {
      await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: false, error: 'form_not_found' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
      // advance past the 5s recovery gap between retries
      await c.tick(60000 + i * 10000);
    }
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'form_action_prepare_failed:form_not_found' }]);
    assert.equal(chr.calls.updates.length, 1, 'only the initial list navigate; no tabs.update for form failure');
  } finally { await cleanup(); }
});

test('same-document effect: ACTION_RESULT effectApplied+!navigationExpected → landed → capture (amend §5.5, §8.1 #5)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list?PAGENUM=3', method: 'GET', expectedEffect: 'same_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const perfRpc = (await c.getState()).pendingRpc;
    await c.deliverActionResult({ op: 'ACTION_RESULT', rpcId: perfRpc.id, jobId: 'job-1', ok: true, invoked: true, navigationExpected: false, effectApplied: true }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const s = await c.getState();
    assert.equal(s.phase, 'capturing', 'same-document effect confirmed → landed → capturing');
    assert.equal(s.navigation.action.effectConfirmed, true);
    assert.equal(s.pendingRpc.op, 'capture');
  } finally { await cleanup(); }
});

test('navigationExpected:true ACTION_RESULT does NOT capture (waits new-document landing) (amend §5.4, §8.1 #4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list', method: 'POST', expectedEffect: 'new_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const perfRpc = (await c.getState()).pendingRpc;
    await c.deliverActionResult({ op: 'ACTION_RESULT', rpcId: perfRpc.id, jobId: 'job-1', ok: true, invoked: true, navigationExpected: true, effectApplied: false }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const s = await c.getState();
    assert.equal(s.phase, 'acting', 'still acting, not capturing');
    assert.equal(s.navigation.action.invocationReported, true);
  } finally { await cleanup(); }
});

test('form-action 30s no commit/effect → fail form_action_navigation_failed (NO tabs.update, NO re-submit) (amend §5.6, §8.1 #11)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list', method: 'POST', expectedEffect: 'new_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const updatesBefore = chr.calls.updates.length;
    const sentBefore = chr.calls.sent.length;
    await c.tick(65000);   // issuedAt≈5500 +30s=35500 well past; no commit/effect
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError.kind, 'nav_error');
    assert.ok(String(s.lastError.error).startsWith('form_action_navigation_failed'));
    assert.deepEqual(api.calls.fail.map((f) => f.msg), [s.lastError.error]);
    assert.equal(chr.calls.updates.length, updatesBefore, 'NO tabs.update on form-action failure');
    assert.equal(chr.calls.sent.length, sentBefore, 'NO re-submit');
  } finally { await cleanup(); }
});

test('PERFORM_ACTION repeat (same rpcId) does NOT generate a new perform rpcId after a correlated requestId appears (amend §5.4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list', method: 'POST', expectedEffect: 'new_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const perfRpcId = (await c.getState()).pendingRpc.id;
    // a correlated main-frame request arrives (binds requestId in acting phase)
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/list', requestId: 'REQ-PERF', timeStamp: 5600 });
    // the perform-recovery tick must NOT create a new rpcId
    await c.tick(60000);
    const s = await c.getState();
    assert.equal(s.pendingRpc.id, perfRpcId, 'no new perform rpcId once requestId is bound');
    assert.equal(s.navigation.requestId, 'REQ-PERF');
  } finally { await cleanup(); }
});
```

> The tests reference two new public methods: `c.dispatchPrepareAction(action)` (Controller-initiated prepare, called when a `landed` page has a `job.action`) and `c.deliverActionPrepared(prepared, sender)` / `c.deliverActionResult(result, sender)`. The SW→CS `PREPARE_ACTION` is itself dispatched by the Controller (the Controller is the only `sendMessage` caller for work RPCs), so `dispatchPrepareAction` is the entry point: it persists a `prepare_action` `pendingRpc.delivery:'prepared'`, sends `PREPARE_ACTION` to the source document, promotes to `received`. This mirrors `dispatchCapture`.

- [ ] **Step 2: Run controller tests to verify they fail**

Run: `npm test -- --test-name-pattern='ACTION_PREPARED ok:true|ACTION_PREPARED ok:false|same-document effect|navigationExpected:true ACTION_RESULT|form-action 30s|PERFORM_ACTION repeat' 2>&1 | tail -20`
Expected: FAIL — `c.dispatchPrepareAction`/`c.deliverActionPrepared`/`c.deliverActionResult` do not exist.

- [ ] **Step 3: Modify controller.ts — form-action handlers**

Edit `browserext/src/controller/controller.ts`:

1. Add imports:
```ts
import type { ActionPrepared, ActionResult } from '../shared/rpc.js';
import type { FetchAction } from '../shared/types.js';
```

2. Widen the `CrawlController` interface (add the three methods):
```ts
  dispatchPrepareAction(action: FetchAction): Promise<void>;
  deliverActionPrepared(prepared: ActionPrepared, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void>;
  deliverActionResult(result: ActionResult, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void>;
```

3. In `applyLandingVerdict`'s `landed` branch, dispatch capture OR prepare depending on whether `currentJob.action` is set:
```ts
    if (verdict.kind === 'landed') {
      s.navigation!.acceptedUrl = verdict.acceptedUrl;
      s.phase = 'landed';
      s.phaseStartedAt = now;
      if (s.currentJob?.action && s.currentJob.action.kind === 'form_submit') {
        await dispatchPrepareAction(s, now, s.currentJob.action);
      } else {
        await dispatchCapture(s, now);
      }
      return;
    }
```

4. Add `dispatchPrepareAction` (after `dispatchCapture`):
```ts
  /** Dispatch PREPARE_ACTION for a form_submit job (amend §5.1–§5.2). The prepare
   *  is side-effect-free; on ACTION_PREPARED ok:true we persist a NEW form_action
   *  NavigationState then dispatch PERFORM_ACTION. delivery: prepared→received. */
  async function dispatchPrepareAction(s: ControllerState, now: number, action: FetchAction): Promise<void> {
    if (!s.currentJob || s.boundTabId === null || !deps.chrome?.sendMessage) return;
    if (!s.navigation?.commit) return;
    const sourceDocumentId = s.navigation.commit.documentId;
    const rpcId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `prep-${now}-${Math.random().toString(36).slice(2)}`;
    s.pendingRpc = {
      id: rpcId, jobId: s.currentJob.id, op: 'prepare_action',
      sourceDocumentId, delivery: 'prepared', issuedAt: now, resultDeadlineAt: now + 30_000,
      recoveryAttempts: 0, nextRecoveryAt: null,
    };
    s.phase = 'acting';
    s.phaseStartedAt = now;
    await persist();
    try {
      const receipt = await deps.chrome.sendMessage(s.boundTabId, { op: 'PREPARE_ACTION', rpcId, jobId: s.currentJob.id, action }, { documentId: sourceDocumentId });
      if (receipt && (receipt as { received?: boolean }).received) { s.pendingRpc.delivery = 'received'; await persist(); }
    } catch {
      s.phase = 'error'; s.phaseStartedAt = now;
      s.lastError = { kind: 'content_unavailable', missing: 'action_prepare', sourceDocumentId, since: now, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: true };
      s.pendingRpc = null;
      await persist();
    }
  }
```

5. Add `deliverActionPrepared` (after `deliverCaptureResult`):
```ts
  async function deliverActionPrepared(prepared: ActionPrepared, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const rpc = s.pendingRpc;
      if (!rpc || rpc.op !== 'prepare_action') return;
      if (sender.tab?.id !== s.boundTabId || sender.frameId !== 0 || sender.documentId !== rpc.sourceDocumentId) return;
      if (prepared.rpcId !== rpc.id || prepared.jobId !== s.currentJob?.id) return;
      if (!prepared.ok) {
        // retry prepare within budget (amend §6.1), else fail form_action_prepare_failed
        if (rpc.recoveryAttempts < 3 && (rpc.nextRecoveryAt === null || Date.now() >= rpc.nextRecoveryAt)) {
          rpc.recoveryAttempts += 1; rpc.nextRecoveryAt = Date.now() + 5_000; rpc.resultDeadlineAt = Date.now() + 30_000; rpc.delivery = 'prepared';
          await persist();
          if (s.boundTabId !== null && deps.chrome?.sendMessage && s.currentJob?.action) {
            try {
              const receipt = await deps.chrome.sendMessage(s.boundTabId, { op: 'PREPARE_ACTION', rpcId: rpc.id, jobId: rpc.jobId, action: s.currentJob.action }, { documentId: rpc.sourceDocumentId });
              if (receipt && (receipt as { received?: boolean }).received) { rpc.delivery = 'received'; await persist(); }
            } catch { /* leave for next tick */ }
          }
          return;
        }
        const jobId = s.currentJob!.id;
        s.phase = 'error'; s.phaseStartedAt = Date.now();
        s.lastError = { kind: 'nav_error', error: `form_action_prepare_failed:${prepared.error ?? 'unknown'}` };
        s.navigation = null; s.pendingRpc = null;
        if (deps.api) await deps.api.failJob(jobId, `form_action_prepare_failed:${prepared.error ?? 'unknown'}`);
        await persist();
        return;
      }
      // amend §5.2: persist a NEW form_action NavigationState (source doc copied, slots absent).
      const sourceDocumentId = rpc.sourceDocumentId;
      const performRpcId = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `perf-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      s.navigation = {
        jobId: s.currentJob!.id,
        requestedUrl: prepared.targetUrl ?? s.currentJob!.url,
        issuedAt: Date.now(),
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
      s.phase = 'acting'; s.phaseStartedAt = Date.now();
      await persist();
      try {
        const receipt = await deps.chrome.sendMessage(s.boundTabId, { op: 'PERFORM_ACTION', rpcId: performRpcId, jobId: s.currentJob!.id, action: s.currentJob!.action!, preparationFingerprint: prepared.preparationFingerprint ?? '' }, { documentId: sourceDocumentId });
        if (receipt && (receipt as { received?: boolean }).received) { s.pendingRpc!.delivery = 'received'; await persist(); }
      } catch {
        // amend §4.2: perform reject with no receipt/requestId/commit → content_unavailable/action_result
        s.phase = 'error'; s.phaseStartedAt = Date.now();
        s.lastError = { kind: 'content_unavailable', missing: 'action_result', sourceDocumentId, since: Date.now(), recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: true };
        s.pendingRpc = null;
        await persist();
      }
    } finally { release(); }
  }
```

6. Add `deliverActionResult` (after `deliverActionPrepared`):
```ts
  async function deliverActionResult(result: ActionResult, sender: { tab?: { id: number }; frameId?: number; documentId?: string }): Promise<void> {
    const release = await mutex.acquire();
    try {
      const s = await ensureLoaded();
      if (!EXCLUSIVE_CONTROL_ENABLED) return;
      const rpc = s.pendingRpc;
      if (!rpc || rpc.op !== 'perform_action') return;
      if (sender.tab?.id !== s.boundTabId || sender.frameId !== 0 || sender.documentId !== rpc.sourceDocumentId) return;
      if (result.rpcId !== rpc.id || result.jobId !== s.currentJob?.id) return;
      const nav = s.navigation;
      if (!nav?.action) return;
      if (!result.ok || !result.invoked) {
        // amend §5.3: fingerprint mismatch or invoke failed → fail (never nav funnel)
        const jobId = s.currentJob!.id;
        const detail = result.error ?? (result.invoked ? 'invoke_failed' : 'not_invoked');
        s.phase = 'error'; s.phaseStartedAt = Date.now();
        s.lastError = { kind: 'nav_error', error: result.error === 'form_action_prepare_changed' ? 'form_action_prepare_changed' : `form_action_invoke_failed:${detail}` };
        s.navigation = null; s.pendingRpc = null;
        if (deps.api) await deps.api.failJob(jobId, s.lastError.error === 'form_action_prepare_changed' ? 'form_action_prepare_changed' : `form_action_invoke_failed:${detail}`);
        await persist();
        return;
      }
      nav.action.invocationReported = true;
      if (result.navigationExpected) {
        // wait the §2.3 five conditions (fresh requestId binds in acting phase). Keep pendingRpc? —
        // once a correlated requestId/commit appears we must NOT create a new perform rpcId (amend §5.4).
        // Clear the perform pendingRpc so the acting-phase landing aggregation owns the rest.
        s.pendingRpc = null;
        await persist();
        return;
      }
      if (result.effectApplied) {
        // same-document effect confirmed (amend §5.5) → landed → capture
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
```

7. Add the form-action failure deadline to `tick` (after the capture-recovery block, step 6.5). For `phase==='acting'` with a `form_action` navigation and no `requestId`/`commit`/`effectConfirmed`:
```ts
        // 6.6. Form-action failure deadline (amend §5.6): no commit/effect within 30s → fail (NO tabs.update).
        if (s.phase === 'acting' && s.navigation?.kind === 'form_action' && s.navigation.action) {
          const hasSignal = !!s.navigation.requestId || !!s.navigation.commit || s.navigation.action.effectConfirmed;
          if (!hasSignal && now >= s.navigation.issuedAt + 30_000) {
            const jobId = s.currentJob!.id;
            s.phase = 'error'; s.phaseStartedAt = now;
            s.lastError = { kind: 'nav_error', error: 'form_action_navigation_failed:timeout' };
            s.navigation = null; s.pendingRpc = null;
            if (deps.api) await deps.api.failJob(jobId, 'form_action_navigation_failed:timeout');
          }
        }
```

8. Add the three methods to the returned object (after `deliverCaptureResult`):
```ts
    deliverCaptureResult,
    dispatchPrepareAction: (action: FetchAction) => dispatchPrepareActionAction(action),
    deliverActionPrepared,
    deliverActionResult,
```
Wait — `dispatchPrepareAction` reads `state` under the mutex. Expose it as a thin public wrapper that acquires the lock:
```ts
    async dispatchPrepareAction(action: FetchAction): Promise<void> {
      const release = await mutex.acquire();
      try {
        const s = await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;
        if (s.phase !== 'landed') return;
        await dispatchPrepareAction(s, Date.now(), action);
      } finally { release(); }
    },
```
(Place this public method in the returned object literal alongside `bind`/`tick`. The internal `dispatchPrepareAction(s, now, action)` helper is the one defined above; rename the internal helper to `dispatchPrepareActionInternal` to avoid the name collision, and have the public method call it. OR keep the internal helper named `dispatchPrepareAction` and expose the public method under the same name via a closure — since the public method is a property of the returned object and the internal is a function declaration, there's no collision at the language level, but for clarity rename the internal one.) **Decision:** rename the internal helper to `prepareFormActionDispatch(s, now, action)`; update `applyLandingVerdict`'s landed branch + `deliverActionResult`'s same-document branch to call `prepareFormActionDispatch` and `dispatchCapture` respectively. The public method `dispatchPrepareAction(action)` acquires the lock and calls `prepareFormActionDispatch`.

- [ ] **Step 4: Run controller tests to verify they pass**

Run: `npm test -- --test-name-pattern='ACTION_PREPARED ok:true|ACTION_PREPARED ok:false|same-document effect|navigationExpected:true ACTION_RESULT|form-action 30s|PERFORM_ACTION repeat' 2>&1 | tail -20`
Expected: PASS — all 6 form-action tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `fail 0` (146 + 6 = 152; exact not load-bearing).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/controller.ts browserext/tests/controller/controller.test.mjs
git commit -m "feat(browserext): slice 4 form-action — prepare/persist/perform/confirm + failure routing (amend §5.1–§5.6)"
```

---

## Task 10: Slice-4 acceptance — full suite green, gate off, Phase-1/2/3 intact, Python unaffected

**Files:**
- No new files; verification gate.

- [ ] **Step 1: Clean build + full test suite + typecheck**

Run: `rm -rf dist && npm install && npm run typecheck && npm run build && npm test 2>&1 | tail -12`
Expected:
- `npm install` succeeds.
- typecheck exit 0 (both configs).
- build emits `dist/background.js` + `dist/content.js`.
- all tests pass, `fail 0`.

- [ ] **Step 2: Confirm the default build is still gated off (a true no-op)**

Run: `node -e "const fs=require('fs');const bg=fs.readFileSync('dist/background.js','utf8');console.log('bg if(false){:', /if \(false\) \{/.test(bg));const c=fs.readFileSync('dist/content.js','utf8');console.log('content if(false){:', /if \(false\) \{/.test(c));"`
Expected: `bg if(false){: true`, `content if(false){: true`. The Controller's new dispatch/recovery/deliver bodies are guarded; the CS router is inside the `if (EXCLUSIVE_CONTROL_ENABLED)` block in `bootstrapContent`. With the gate off the build is a no-op: no marker behavior change beyond slice 1, no CAPTURE/PERFORM dispatched, no `/complete` called.

- [ ] **Step 3: Confirm Phase-1/2/3 regression status**

Run: `npm test 2>&1 | grep -E "tests|pass|fail"`
Expected: `fail 0`. The slice-3 suites (`status`/`navMonitor`/`chrome`/`landing`/`funnel`/`deadline`/`urlGate`/`navEvents`/`controller`/`api`) all pass; the slice-1/2 suites (`hostPolicy`/`state`/`mutex`/`storage`/`backoff`/`reconcile`/`heartbeat`/`content`/`build`/`smoke`/`background`) pass; the new slice-4 suites (`pageDetect`/`capture`/`formActions`/`fingerprint`/`rpcLedger`/`rpcRouter` + `api.completeJob` + `controller` capture/form-action tests + `chrome.sendMessage`) pass.

- [ ] **Step 4: Confirm amend §8.1 must-pass slice-4 items are covered**

Run: `npm test -- --test-name-pattern='CAPTURE_RESULT second-detection|CAPTURE_RESULT with mismatched|PERFORM_ACTION first call executes|PERFORM_ACTION fingerprint mismatch|navigationExpected:true ACTION_RESULT|same-document effect|form-action 30s|capture RPC recovery|ACTION_PREPARED ok:true → persists|ACTION_PREPARED ok:false|PERFORM_ACTION repeat|fingerprint is sensitive|virtual apply|sha256Hex default' 2>&1 | tail -15`
Expected: all green — these pin items #4 (navigationExpected≠capture), #5 (effectApplied/history→capture), #8 (second-detection branches), #10 (mismatched documentId reject), #11 (form-action no tabs.update/re-submit), #18 (capture second-detection terminal/gateway/form-fail), #19 (fingerprint sensitivity). Item #13 (non-bound tab PanelState) is slice 5.

- [ ] **Step 5: Confirm Python suite is unaffected**

Run: `uv run pytest -q` (from repo root)
Expected: PASS (slice 4 touches only `browserext/`; no Python changed). If any Python test fails, it is pre-existing and unrelated — note it but do not fix in this slice.

- [ ] **Step 6: Update memory with the slice-4 landing fact**

Update the `browserext-slice3-landed` memory's "NOT in slice 3 (slice 4)" line, or add a `browserext-slice4-landed` memory recording: slice 4 landed (gated off) — byte-aligned ports of capture/formActions/pageDetect into `content/`; new `prepareFormAction`+`canonicalizeActionSnapshot`+`sha256Hex` (amend §5.1); CS `rpcRouter` + `shared/rpcLedger` (amend §4.3); precise-`documentId` delivery + `MessageSender` validation (amend §4.1); capture-before-second-detection (amend §2.3); form-action prepare→persist→perform→confirm + failure routing (amend §5.1–§5.6); RPC-recovery budget for capture/prepare/perform (amend §6.1); `api.completeJob` + Controller `landed→dispatchCapture→complete`; `chrome.sendMessage`; `PendingRpc` gained `recoveryAttempts`/`nextRecoveryAt`; no panel UI yet (slice 5); amend §8.1 items 4/5/8/10/11/18/19 pinned. This orients future sessions without re-deriving from git.

- [ ] **Step 7: Final commit (if any docs/memory artifacts were staged)**

```bash
git add browserext/README.md browserext/manifest.json 2>/dev/null
git commit -m "docs(browserext): note slice-4 capture/formActions/RPC landed (gate off)" 2>/dev/null || echo "nothing to commit"
```

---

## Self-Review (completed by plan author)

**1. Spec coverage (slice-4 row of spec §6.2 + amend §5.1–§5.6, §4.1–§4.4, §6.1–§6.2, §8.2):**
- Port capture/formActions/pageDetect byte-aligned (synthetic_url trap) → Task 3 (`collectFormPaginationStates`/`performFetchAction`/`buildSyntheticUrl`) + Task 2 (`stripCaptureNoise`/`serializeWithoutOverlay`) + Task 1 (`detectPage`). Ported tests assert same-input/same-output. ✓ (spec §4.1, CLAUDE.md bug trap)
- RPC CAPTURE/PERFORM_ACTION/PREPARE_ACTION + CAPTURE_RESULT/ACTION_PREPARED/ACTION_RESULT, receipt-only → Task 6 (`rpcRouter` emits the three result messages + returns `{received:true}`) + Task 8 (`deliverCaptureResult`) + Task 9 (`deliverActionPrepared`/`deliverActionResult` + `dispatchPrepareAction`). ✓ (amend §4.1, §4.4)
- CS rpcId ledger → Task 5 (`shared/rpcLedger`) + Task 6 (router uses it for perform dedup). ✓ (amend §4.3)
- Precise-documentId delivery + MessageSender validation → Task 8 (`chrome.sendMessage(...,{documentId})` + `deliverCaptureResult` validation) + Task 9 (`deliverActionPrepared`/`deliverActionResult` validation). ✓ (amend §4.1)
- Capture-before-second-detection → Task 6 (`handleCapture` runs `detectPage` before emitting `CAPTURE_RESULT.html`) + Task 8 (`deliverCaptureResult` routes second-detection failures: terminal skip / navigate gateway funnel / form_action fail). ✓ (amend §2.3, §8.1 #18)
- Form-action controlled exception (prepare→persist→perform→confirm) → Task 9 (`dispatchPrepareAction`→`deliverActionPrepared` persists new `NavigationState` then `PERFORM_ACTION`→`deliverActionResult` confirms; same-document effect; failure routing). ✓ (amend §5.1–§5.6)
- RPC-recovery budget (capture/prepare/perform same-rpcId re-send, ≤3, ≥5s; PAGE_READY/HTTP not recoverable) → Task 8 (capture recovery in tick) + Task 9 (prepare retry in `deliverActionPrepared`; perform budget). ✓ (amend §6.1, §6.2)
- Controller `landed`→dispatch CAPTURE + `complete` → Task 8 (`dispatchCapture` + `deliverCaptureResult` calls `api.completeJob`). ✓ (spec §6.2 slice-4 row)
- `api.completeJob` (POST /complete, idempotent swallow) → Task 7. ✓ (CLAUDE.md bug trap)
- Gate stays OFF → Task 10 Step 2 verifies gate-off no-op; every new dispatch/recovery/deliver step is inside the gate guard. ✓
- amend §8.1 items landing here: #4 (navigationExpected≠capture) → Task 9 test `navigationExpected:true ACTION_RESULT does NOT capture`; #5 (effectApplied/history→capture) → Task 9 test `same-document effect`; #8 (second-detection branches) → Task 6 + Task 8 tests; #10 (mismatched documentId reject) → Task 8 test `CAPTURE_RESULT with mismatched documentId`; #11 (form-action no tabs.update/re-submit) → Task 9 test `form-action 30s`; #18 (capture second-detection terminal/gateway/form-fail) → Task 8 tests; #19 (fingerprint sensitivity) → Task 4 tests. ✓ (all seven pinned; #1/#2/#3 are SW-restart items covered structurally by the stable-rpcId + `delivery`-stage design — `prepared`→`received` re-send uses the same rpcId; #13 is slice 5.)

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Every code block contains complete code. The `content/rpcTypes.ts` shim + the `shared/rpc.ts` `ACTION_PREPARED` augmentation are explicit additive steps (Task 6 Step 3b) with a verification grep — not placeholders. The `doc` shim in `content/index.ts` has a note acknowledging the live-getter vs snapshot-field adaptation with a fallback (`readDoc()` function) offered explicitly. Test-count math is hedged ("exact not load-bearing — `fail 0` is"). The Task 9 internal/public `dispatchPrepareAction` name collision is called out with an explicit rename decision (`prepareFormActionDispatch`). ✓

**3. Type consistency:**
- `PendingRpc` gains `recoveryAttempts` + `nextRecoveryAt` (Task 8 Step 5 modifies `shared/state.ts`); every `pendingRpc` literal (Task 8 capture dispatch + Task 9 prepare/perform dispatch + Task 8 capture recovery) sets both fields. ✓
- `CaptureResult`/`ActionPrepared`/`ActionResult` types: Task 6 Step 3b ensures `shared/rpc.ts` exports them (named aliases + the `ACTION_PREPARED` variant in `CsToSw`); Task 8 imports `CaptureResult`; Task 9 imports `ActionPrepared`/`ActionResult`. The `content/rpcTypes.ts` shim re-exports them for the CS side. ✓
- `ChromeRuntime.sendMessage(tabId, message, options)` (Task 8 Step 1) — `CrawlControllerDeps.chrome` widens to `Pick<..., 'getTab'|'updateTabUrl'|'sendMessage'>` (Task 8 Step 5); Controller calls `deps.chrome.sendMessage(boundTabId, msg, { documentId })`; the fakes (`fakeChrome4`) provide `sendMessage`. ✓
- `ApiClient.completeJob(jobId, html, url, title, paginationStates?)` (Task 7) — Controller `deliverCaptureResult` calls `api.completeJob(jobId, result.html ?? '', result.url, result.title ?? '', result.paginationStates)`. The userscript's `completeJob` body is `{ html, url, title, pagination_states }` — the extension's body uses `pagination_states` (snake_case, byte-aligned). ✓
- `FormDoc`/`FormEl`/`FormElement` (Task 3) — `prepareFormAction`/`performFetchAction`/`collectFormPaginationStates` consume them; `canonicalizeActionSnapshot` (Task 4) uses the structurally-compatible `ActionSnapshotForm` (declared in `fingerprint.ts` to avoid a value cycle); `rpcRouter` (Task 6) builds the `doc` from the real `document`. ✓
- `evaluateLanding` (slice 3, NOT modified) — `applyLandingVerdict` still calls it; the `landed` branch now dispatches capture/prepare. The slice-3 landing tests remain green (the `landed` verdict is unchanged; only its consumer action changed). ✓
- `deliverBeforeRequest` slice-3 guard includes `phase === 'acting'` (already present) → a form_action's new main-frame request binds `requestId` correctly in Task 9's flow. ✓

**4. Phase-1/2/3 regression check:**
- `content/index.ts` — extended (marker + host-gate preserved; router + PAGE_READY added inside the gate guard; the 3 slice-1 tests pass because they don't supply `chrome.runtime`/`document`). ✓
- `api.ts` — additive `completeJob`; existing assertions unaffected. ✓
- `chrome.ts` — additive `sendMessage`; chrome.test widens. ✓
- `shared/state.ts` — `PendingRpc` gains 2 fields (additive; `initialControllerState` unaffected since `pendingRpc:null`). ✓
- `shared/rpc.ts` — additive `ACTION_PREPARED` variant + named aliases (if absent). ✓
- `controller/controller.ts` — `applyLandingVerdict` landed branch extended; new helpers + 3 deliver methods + tick steps 6.5/6.6; slice-3 tests pass because the `landed` verdict itself is unchanged and slice-3 tests that asserted `phase==='landed'` now see `phase==='capturing'` (a capture dispatch follows). **Check:** the slice-3 test `deliverCommitted+Http+PageReady (same requestId/documentId, ok) → landed` asserts `s.phase === 'landed'` — but Task 8 makes `landed` immediately dispatch capture → `phase==='capturing'`. **This is a deliberate, expected regression of that one slice-3 assertion.** Resolution: Task 8 Step 3's `landedController` helper drives the same flow and asserts `phase==='capturing'`; the slice-3 test's `phase==='landed'` assertion must be UPDATED in Task 8 to `phase==='capturing'` (or the test left asserting `landed` only when `chrome.sendMessage` is absent). **Add to Task 8 Step 5:** update the slice-3 test `'deliverCommitted+Http+PageReady (same requestId/documentId, ok) → landed'` to assert `phase === 'capturing'` (since dispatchCapture now runs), OR guard `dispatchCapture` to no-op when `deps.chrome?.sendMessage` is absent so the slice-3 test (which injects `fakeChrome3` without `sendMessage`) still sees `landed`. **Decision (cleaner, preserves slice-3 tests verbatim):** `dispatchCapture` already returns early when `!deps.chrome?.sendMessage` — so in the slice-3 test (whose `fakeChrome3` has no `sendMessage`), `dispatchCapture` is a no-op and `phase` stays `'landed'`. The slice-3 test stays green unchanged. ✓ (verified: `dispatchCapture`'s first guard `if (!deps.chrome?.sendMessage) return;`).
- `status.ts`/`navMonitor.ts`/`landing.ts`/`funnel.ts`/`deadline.ts`/`urlGate.ts`/`navEvents.ts` — untouched. ✓
- `controller/reconcile.ts`/`backoff.ts`/`heartbeat.ts`/`mutex.ts`/`storage.ts`(controller) — untouched. ✓

**5. Gate-off invariant:** Official build (`EXCLUSIVE_CONTROL_ENABLED=false`) — `controller.tick` early-returns; every new `deliver*` early-returns after `ensureLoaded`; `dispatchCapture`/`dispatchPrepareAction` are reached only from `applyLandingVerdict` (which runs only when gated ON) and from the public `dispatchPrepareAction` (which early-returns when gated OFF); the capture-recovery + form-failure tick blocks run only inside the gated `tick` body. The CS `rpcRouter` is constructed inside `if (EXCLUSIVE_CONTROL_ENABLED)` in `bootstrapContent`. `chrome.sendMessage` is only called from gated Controller paths. True no-op. Task 10 Step 2 verifies the folded `if (false) {` in `dist/`. ✓

**6. Single-in-flight invariant:** The Controller claims at most once per tick (slice 3); capture/prepare/perform are sequential (one `pendingRpc` at a time, keyed by stable rpcId); re-sends reuse the same rpcId (no second in-flight). `complete` is single. ✓

**7. Task ordering (each task leaves the full suite green):** Tasks 1, 2, 4, 5 (pure, no cross-deps) → green independently. Task 3 depends on Task 4 (`prepareFormAction` imports `fingerprint`) — the plan explicitly orders Task 4 before Task 3 at execution time (the narrative numbering 3→4 is resolved by the bold note). Task 6 (rpcRouter) depends on Tasks 1–5 + the `shared/rpc.ts` augmentation. Task 7 (api.completeJob) is isolated. Task 8 (Controller capture) depends on Tasks 6–7. Task 9 (Controller form-action) depends on Task 8. Task 10 acceptance. "One green commit per step" holds at every boundary; no commit folding. ✓

**8. Byte-alignment risk:** `collectFormPaginationStates`/`buildSyntheticUrl`/`performFetchAction` are copied verbatim (only `document`→`doc` injection). `detectPage` copies the regexes + thresholds verbatim. `stripCaptureNoise` copies the structural-noise logic verbatim. The `synthetic_url` trap is pinned by the `buildSyntheticUrl byte-matches userscript` test. ✓

**9. Dependency-cycle risk:** `fingerprint.ts` imports only `FetchAction` (type) from `shared/types` — no import of `formActions`. `formActions.ts` imports `canonicalizeActionSnapshot`/`sha256Hex` from `fingerprint` — one-directional. `rpcRouter` imports `formActions` + `pageDetect` + `capture` + `rpcLedger` — no cycle. The `content/rpcTypes.ts` shim breaks the `Extract`-on-absent-variant problem. ✓

No blocking issues found. The plan is internally consistent, scoped to slice 4, preserves the gate-off + Phase-1/2/3-intact + Python-unaffected invariants, and the one slice-3 test that touches the `landed`→`capturing` transition is preserved verbatim by `dispatchCapture`'s `sendMessage`-absent early-return guard.
