# Browserext exclusive control — Slice 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land slice 3 of the Phase-2 exclusive-control design: the `/jobs/next` **claim** (Controller `assigned → navigating`), **persist-before-navigate** dispatch (the sole `chrome.tabs.update` caller), the **landing recognition** (the amend §2.3 five-condition rule: correlated main-frame commit + acceptable same-`requestId` HTTP outcome + same-crawl-site URL + same-documentId `PAGE_READY` + clean detection), the **three-signal aggregation** (commit/http/PAGE_READY keyed by documentId), the **retry funnel** (3-budget, signal-clear-on-retry, terminal skip/fail semantics), the **two-level deadline** (`navigationDeadlineAt` no-commit vs `landingSignalsDeadlineAt` post-commit), the **requestId binding + commit/http/documentId join**, the rewritten **`status.ts` classifier** (amend §3.1: `unexpected_status`, `500→gateway`, `204/205/401/403/301/302→unexpected_status`), and the **`navMonitor.ts` thin main-frame adapter** rewrite (amend §3.2: scope-filter only, deliver raw event + outcome, no own fail/skip/count/navigate; `onBeforeRedirect` delivered raw, no classifier call). **No capture / formActions / CS RPC ledger** (slice 4). The whole slice stays gated OFF (`EXCLUSIVE_CONTROL_ENABLED = false` in the official build), so the runtime is still a no-op and Phase-1 tests stay green except the two deliberately-rewritten suites (`status.ts`, `navMonitor.ts` — amend §8.2 says rewrite their tests, expected not regression).

**Architecture:** Slice 3 adds three new **pure** controller modules and two new **pure shared** modules so the correlation logic is unit-testable without chrome/DOM and so both tsconfigs (background + content) can see the shared types. New shared: `shared/navEvents.ts` (pure nav-event types — `BeforeRequestEvent`/`BeforeRedirectEvent`/`CommittedEvent` + widened `NavCompleted`/`NavError` carrying `requestId`/`documentId`/`timeStamp`, the `NavScope` filter struct, and the `NavController` interface the thin navMonitor forwards into) and `shared/urlGate.ts` (ports `urlMatches`/`hasExplicitPort`/`siteRoot`/`sameCrawlSite` from userscript `utils.ts` byte-aligned, plus `isWechatHost`/`redirectKind` from userscript `main.ts` — the same-crawl-site gate amend §3.1/§3.5 leans on). New pure controller: `controller/landing.ts` (`evaluateLanding` — the §2.3 five-condition check returning `landed|terminal_skip|error_retry|waiting`), `controller/funnel.ts` (`funnelDecision` — the §3.2 outcome→action table), and `controller/deadline.ts` (`navTimeoutKind` — the §2.4 two deadlines → `content_unavailable{missing}` or a never-landed funnel trigger). The existing `status.ts` becomes the amend §3.1 classifier (drops its duplicate 5-value `NavOutcome`; re-uses the canonical 6-value one from `shared/state.ts`). The existing `navMonitor.ts` is rewritten to a thin adapter: it no longer holds `api`/`storage`/`updateTabUrl`; it takes `{ chrome, controller }`, registers the six `webRequest`/`webNavigation` listeners, scope-filters main-frame + bound tab **lock-free** via `controller.getNavScope()`, and forwards raw events + outcome to the Controller (which owns all requestId/documentId/job/time/phase correlation under the mutex). The Controller grows the `claim` step + persist-before-navigate in `tick`, the five `deliver*` event handlers, and the two-deadline checks. `src/storage.ts` (the Phase-1 per-job counter/verdict/redirect store — its **only** consumer was the old navMonitor) is **deleted** alongside its test; `src/controller/storage.ts` (ControllerState persistence — a different file) stays.

**Task ordering rationale (each task leaves the full suite green):** the pure modules (Tasks 1–5) depend only on `shared/state.ts` (already present from slice 1) and each other — each is green independently. Task 6 (Controller + `api.claimNextJob`) depends on Tasks 1–5 but **not** on `status.ts` or `chrome.ts` (the Controller receives the outcome *from* navMonitor and uses `shared/navEvents` event types, not chrome.ts's types), so the Phase-1 `status.ts`/`navMonitor.ts` are untouched and green through Task 6. Task 7 is the **single coupled rewrite** of `status.ts` + `chrome.ts` + `navMonitor.ts` + `background.ts` (each breaks the others' Phase-1 tests, so they land in one commit) plus the `storage.ts` deletion — after it the full suite is green again. This keeps "one green commit per step" true at every task boundary; no commit folding is needed.

**Tech Stack:** TypeScript 5.7, MV3 Chrome/Edge (≥110), esbuild, node:test, `chrome.webRequest` (`onBeforeRequest`/`onBeforeRedirect`/`onCompleted`/`onErrorOccurred`), `chrome.webNavigation` (`onCommitted`/`onHistoryStateUpdated`), `chrome.tabs.update`, `chrome.storage.local`, WebWorker lib (background config).

## Global Constraints

Copied verbatim from the spec/amend + slice-1/2 plans so every task implicitly inherits them:

- **Gate default false.** Slice 1–5 official builds default `EXCLUSIVE_CONTROL_ENABLED = false`; the Controller's `tick` body short-circuits on `if (!EXCLUSIVE_CONTROL_ENABLED) return;` — no claim, no navigate, no event handling side-effect. Only unit tests (harness injects `true`) enable it. (spec §6.1)
- **`/status.current_job` is the sole job-state truth.** The Controller's `currentJob` is a client cache only; reconcile (slice 2) discards stale `navigation`/`pendingRpc` when the job id changes. `/jobs/next` returns 204 whenever a job is already assigned/in-flight — the Controller must NOT loop on a 204; one claim attempt per tick. (spec §2.3 step 3, amend §9)
- **Backend unreachable never navigates/refreshes.** On `/status` failure the slice-2 path keeps `connected=false` + backoff + current phase; slice 3 adds no navigation on top. (spec §2.3 step 2, §1.3)
- **Controller is the sole `chrome.tabs.update` caller.** Navigation intent is persisted to `ControllerState.navigation` **before** `chrome.tabs.update(boundTabId, {url})` is called — persist-before-navigate makes landing recognition recoverable across SW restarts. Initial `attempt = 1`. Once a job reaches `landed|acting|capturing|submitting`, the **same job is never re-navigated**; re-navigation only happens through the retry funnel. (spec §3.1, amend §9)
- **Landing = the amend §2.3 five conditions, all satisfied simultaneously:** (1) correlated main-frame `onCommitted` with `phase ∈ {navigating, acting}`; (2) acceptable HTTP outcome from the **same `requestId`** final `onCompleted`, and if that HTTP event carries a `documentId` it must equal `navigation.commit.documentId`; (3) `committedUrl` passes the same-crawl-site gate vs `requestedUrl`; (4) `PAGE_READY` whose `documentId` equals `navigation.commit.documentId`; (5) `PAGE_READY.detection.errorPage === false && terminalReason === null`. Backend failure never participates. (amend §2.3, spec §3.2)
- **`requestId` binding (amend §2.1).** Only an empty `navigation.requestId` may be bound, and only when: `tabId===boundTabId`, `frameId===0 && type==='main_frame'`, `phase ∈ {navigating, acting}`, `currentJob.id===navigation.jobId`, `event.timeStamp >= navigation.issuedAt`, the event URL matches `navigation.requestedUrl` per `urlMatches`, and the current attempt has no requestId yet. After binding, only the same `requestId`'s redirect/completed/error are accepted; a late old `requestId` or a non-matching main-frame request is discarded. (amend §2.1) — note `webNavigation.onCommitted` does NOT carry `requestId`; commit correlation is by bound-tab + main-frame + job + time-window only (amend §2.2).
- **commit / HTTP / documentId join (amend §2.2).** `onCommitted` filters bound tab + main frame + job + time-window, with the phase guard widened to `navigating|acting`. A successful `onCompleted` writes `navigation.http` **only if** `requestId === navigation.requestId`; if the event carries a `documentId`, landing additionally requires `navigation.http.documentId === navigation.commit.documentId`. `onBeforeRedirect` is **never** classified and **never** writes `navigation.http` — it is delivered raw so the Controller can run the same-crawl-site gate on `redirectUrl`. (amend §2.2, §3.1)
- **Retry funnel = amend §3.2 table (navigate kind only).** `not_found` → immediate skip (no budget). `rate_limited` → immediate `fail rate_limited`, no refresh. `gateway` → attempt 3 fail `gateway_5xx`, else re-navigate. `unexpected_status` → attempt 3 fail `unexpected_status:<code>`, else re-navigate. `nav_error` → attempt 3 fail `nav_error:<error>`, else re-navigate. **`content_unavailable` is NOT in this table** (it never re-navigates). Form-action failures are NOT in this table (slice 4 / amend §5.6). (amend §3.2)
- **Two-level deadline (amend §2.4).** `navigationDeadlineAt = issuedAt + 30s`: no commit yet → only a plain navigate may enter the funnel (re-navigate). `landingSignalsDeadlineAt = committedAt + 30s`: commit present but PAGE_READY or HTTP outcome missing → **never refresh** → `content_unavailable/page_ready` or `content_unavailable/http_outcome`. (amend §2.4)
- **Signal-clear-on-retry.** Every ordinary navigation retry clears `requestId`/`commit`/`http`/`pageReady`/`acceptedUrl` so a new attempt's signals are re-evaluated; these slots are never reused across attempts. (amend §1.2, spec §3.5)
- **Offsite/wechat terminal skip, no budget.** `onBeforeRedirect.redirectUrl` (or final commit URL) failing the same-crawl-site gate → immediate `skip` with `wechat_redirect` (if `isWechatHost`) or `offsite_redirect`; these do NOT consume the retry budget. `not_found`/`empty_page` (from PAGE_READY/capture detection) likewise skip, no budget. (amend §2.3, §3.1, spec §3.5)
- **429 → immediate fail `rate_limited`, no refresh, not skipped.** The backend's own retry throttle owns the rate-limit backoff; the extension must not hammer the tab. (spec §3.5, amend §3.2)
- **`status.ts` is the ONLY HTTP classifier.** No second status-code map anywhere — not in `navMonitor`, not in the Controller. `navMonitor` calls `classifyNavigation` for the final `onCompleted`/`onErrorOccurred` and forwards the outcome; it makes no decision from the outcome. (amend §3.1, §3.2, spec §1.4)
- **`navMonitor` is a side-effect-free adapter.** It does: main-frame + bound-tab scope filter; call the one pure classifier on the final completed/error; forward the raw event + outcome to the Controller. It does NOT: early-return on `rate_limited`; call fail/skip itself; accumulate gateway counts; call `tabs.update`. `onBeforeRedirect` is forwarded raw (no classifier). (amend §3.2)
- **HTTP contract is FIXED.** No new/changed endpoints, no DB schema change. `/jobs/next` (GET, 200 FetchJob | 204) already exists; slice 3 adds a Controller `claimNextJob` that calls it (the userscript's `fetchNextJob` already uses it). (CLAUDE.md, spec §1.3, amend §9)
- **Single in-flight + one bound tab.** `/jobs/next` returns 204 whenever a job is assigned/in-flight; the Controller claims at most once per tick and only when `phase==='idle' && currentJob===null && autoMode && !paused && boundTabId!==null`. (spec §2.3 step 3)
- **`paused` / `!autoMode` / unbound stop auto side-effects** (claim, navigate, retry) but NOT `/status` reconcile, heartbeat, or event observation. (spec §2.4)
- **2s TICK does not unconditionally write storage** — slice-2 `saveIfChanged(prev, state)` is reused; slice 3's tick/deliver paths persist only on change. (spec §2.2)
- **One conventional commit per green step.** `feat(browserext)`/`test(browserext)`/`chore(browserext)`/`docs(browserext)`. (CLAUDE.md)
- **browserext tests run with `cd browserext && npm test` (node:test, no pytest).** Relative imports in `src/*.ts` carry `.js`. The harness bundles via esbuild and injects `EXCLUSIVE_CONTROL_ENABLED = true` so gated-ON paths are exercised. (CLAUDE.md)
- **`dist/` is gitignored.**
- **Baseline before starting: `cd browserext && npm test` reports `pass 69 fail 0` on branch `slice3`.**

---

## File Structure

New/modified/deleted files in this slice. Decomposition: the correlation rules (requestId bind, commit/http/documentId join, landing, funnel, deadlines) are pure functions over `ControllerState` + an event — keep them in small focused files so each is unit-testable in isolation with no chrome/DOM. The two shared modules (`navEvents`, `urlGate`) are pure so both the background tsconfig (Controller, navMonitor, chrome) and the content tsconfig (slice-4 content will reuse `urlGate`) see them.

```
browserext/
  src/shared/navEvents.ts            # CREATE: pure nav-event types + NavScope + NavController interface (no chrome/DOM)
  src/shared/urlGate.ts              # CREATE: port urlMatches/hasExplicitPort/siteRoot/sameCrawlSite + isWechatHost/redirectKind (pure)
  src/controller/landing.ts          # CREATE: pure evaluateLanding (amend §2.3 five conditions → landed|terminal_skip|error_retry|waiting)
  src/controller/funnel.ts           # CREATE: pure funnelDecision (amend §3.2 table → fail|skip|retry-navigate)
  src/controller/deadline.ts        # CREATE: pure navTimeoutKind (amend §2.4 two deadlines → content_unavailable{missing}|never-landed-funnel|null)
  src/api.ts                         # MODIFY: + claimNextJob (GET /jobs/next; 204→null)
  src/controller/controller.ts       # MODIFY: + claim + persist-before-navigate in tick; + 5 deliver* handlers; + getNavScope(); + deadline checks
  src/status.ts                      # MODIFY (Task 7): amend §3.1 classifier — drop dup NavOutcome, import from shared/state; add unexpected_status; 500→gateway; 204/205/401/403/301/302→unexpected_status
  src/chrome.ts                      # MODIFY (Task 7): NavCompleted/NavError gain requestId?/documentId?/timeStamp; add onBeforeRequest/onBeforeRedirect/onCommitted/onHistoryStateUpdated (main-frame only)
  src/navMonitor.ts                  # REWRITE (Task 7): thin main-frame adapter — {chrome, controller}; scope filter; forward raw event+outcome; no api/storage/updateTabUrl
  src/background.ts                  # MODIFY (Task 7): wire thin navMonitor with {chrome, controller}; drop Storage dep
  src/storage.ts                     # DELETE (Task 7): Phase-1 per-job counter/verdict/redirect — sole consumer rewritten
  tests/status.test.mjs              # REWRITE (Task 7): amend §8.1 #16 mapping
  tests/navMonitor.test.mjs         # REWRITE (Task 7): amend §8.1 #17 (delivers raw event+outcome; no fail/skip/count/navigate); + #14 onBeforeRedirect raw
  tests/chrome.test.mjs              # MODIFY (Task 7): assert new listeners exist + NavCompleted carries requestId/documentId
  tests/background.test.mjs          # MODIFY (Task 7): navMonitor constructed with {chrome, controller}; Storage absent
  tests/storage.test.mjs             # DELETE (Task 7)
  tests/shared/navEvents.test.mjs    # CREATE (Task 1)
  tests/shared/urlGate.test.mjs      # CREATE (Task 2)
  tests/controller/landing.test.mjs  # CREATE (Task 3)
  tests/controller/funnel.test.mjs  # CREATE (Task 4)
  tests/controller/deadline.test.mjs # CREATE (Task 5)
  tests/controller/controller.test.mjs  # MODIFY (Task 6): + claim/navigate/landing/funnel/deadline/deliver/join tests (incl. amend §8.1 #6, #7, #12)
  tests/api.test.mjs                 # MODIFY (Task 6): + claimNextJob cases
docs/superpowers/specs/...           # no change this slice
```

Boundary notes:
- `shared/navEvents.ts` + `shared/urlGate.ts` are pure (no `chrome`, no DOM, no fetch) — import only other `shared/*` types. Both tsconfigs already `include` `src/shared/**/*.ts`, so no tsconfig change is needed.
- `src/status.ts` becomes type-only-import-dependent on `shared/state.js` (the canonical `NavOutcome`). It stays a single pure function; `DEAD_STATUSES`/`RATE_LIMITED_STATUS`/`GATEWAY_STATUSES` exports are kept (now `GATEWAY_STATUSES` includes 500).
- `controller/landing.ts` + `funnel.ts` + `deadline.ts` are pure — they import only `shared/state` + `shared/urlGate` types (and `funnel` reads `NavOutcome`). No chrome, no fetch.
- `navMonitor.ts` imports `NavController` (type) + the event types from `shared/navEvents.js`, `classifyNavigation` from `../status.js`, and `ChromeRuntime` (type) from `./chrome.js`. It calls `controller.deliver*` and `controller.getNavScope()`; it never imports `api`/`storage`/`status` decision logic.
- `controller/controller.ts` imports the three new pure modules + `shared/navEvents` + `shared/urlGate` + `ApiClient` (for `getStatus`/`claimNextJob`/`failJob`/`skipJob`). It already runs in the SW; the new `deliver*` handlers re-acquire the mutex and mutate state.
- `chrome.ts` widens `NavCompletedEvent`/`NavErrorEvent` (add optional `requestId`/`documentId`/`timeStamp`) and adds four new listener methods (`onBeforeRequest`/`onBeforeRedirect`/`onCommitted`/`onHistoryStateUpdated`), each filtering `frameId===0` (or `type==='main_frame'` for webRequest) before invoking the callback. The real impl binds the real `chrome.webNavigation.*`/`chrome.webRequest.*` listeners. Note: `webNavigation.onCommitted` details do NOT carry `requestId` — commit correlation is by bound-tab + main-frame + job + time-window, not requestId (amend §2.2).
- `src/storage.ts` deletion (Task 7): its only importer was the old `navMonitor.ts` (Phase-1). Confirm with `grep -rn "from './storage.js'\|from '../storage.js'" browserext/src` before deleting — the only hits must be `navMonitor.ts` (rewritten in Task 7) and `background.ts` (rewired in Task 7). `controller/storage.ts` is a DIFFERENT module (`createControllerStorage`) and STAYS.

---

## Task 1: shared/navEvents.ts — pure nav-event types + NavController interface

**Files:**
- Create: `browserext/src/shared/navEvents.ts`
- Create: `browserext/tests/shared/navEvents.test.mjs`

**Interfaces:**
- Consumes: `NavOutcome`, `PageDetection` (types) from `./state.js`.
- Produces (all pure types/values, no chrome/DOM):
  - `NavScope = { boundTabId: number | null }` — the lock-free filter struct `navMonitor` reads via `controller.getNavScope()`.
  - Event types mirroring the chrome surfaces the thin adapter forwards:
    - `BeforeRequestEvent = { tabId: number; frameId: number; type: string; url: string; requestId: string; timeStamp: number }`
    - `BeforeRedirectEvent = { tabId: number; frameId: number; type: string; url: string; redirectUrl: string; requestId: string; timeStamp: number }`
    - `CommittedEvent = { tabId: number; frameId: number; documentId: string; url: string; timeStamp: number }` — **no `requestId`** (webNavigation.onCommitted does not carry it; amend §2.2).
    - `NavCompletedEvent = { tabId: number; url: string; statusCode: number; frameId: number; requestId: string; documentId?: string; timeStamp: number }`
    - `NavErrorEvent = { tabId: number; url: string; error: string; frameId: number; requestId: string; timeStamp: number }`
    - `PageReadyEvent = { documentId: string; url: string; detection: PageDetection; timeStamp: number }`
  - `NavInput = { kind: 'completed'; statusCode: number } | { kind: 'error'; error: string }` (the canonical classifier input).
  - `NavController` — the interface the thin `navMonitor` forwards into:
    ```ts
    export interface NavController {
      getNavScope(): NavScope;
      deliverBeforeRequest(e: BeforeRequestEvent): Promise<void>;
      deliverBeforeRedirect(e: BeforeRedirectEvent): Promise<void>;
      deliverCommitted(e: CommittedEvent): Promise<void>;
      deliverHttpEvent(e: NavCompletedEvent, outcome: NavOutcome): Promise<void>;
      deliverError(e: NavErrorEvent, outcome: NavOutcome): Promise<void>;
      deliverPageReady(e: PageReadyEvent): Promise<void>;
    }
    ```
  - A pure helper `isMainFrame(e: { frameId?: number }): boolean` — `frameId === 0`.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/shared/navEvents.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('isMainFrame: true for frameId 0, false otherwise', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/navEvents.ts', 'navEvents.ts');
  try {
    assert.equal(mod.isMainFrame({ frameId: 0 }), true);
    assert.equal(mod.isMainFrame({ frameId: 1 }), false);
    assert.equal(mod.isMainFrame({ frameId: 2 }), false);
  } finally {
    await cleanup();
  }
});

test('navEvents module loads cleanly (type-only exports; isMainFrame is the value export)', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/navEvents.ts', 'navEvents.ts');
  try {
    assert.equal(typeof mod.isMainFrame, 'function');
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='isMainFrame|navEvents module loads' 2>&1 | tail -15`
Expected: FAIL — `../src/shared/navEvents.ts` does not exist (ENOENT on entry).

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/shared/navEvents.ts`:
```ts
/** Pure navigation-event types + the NavController forwarding interface the
 *  thin navMonitor adapter (amend §3.2) delivers into. NO chrome, NO DOM, NO
 *  fetch — only types + the one isMainFrame filter helper, so both the
 *  background tsconfig (navMonitor, controller, chrome) and the content
 *  tsconfig see them. The Controller owns all requestId/documentId/job/time/
 *  phase correlation under its mutex (amend §2.1–§2.2); navMonitor only
 *  scope-filters (main frame + bound tab) and forwards raw events + outcome.
 *
 *  NOTE: webNavigation.onCommitted does NOT carry requestId — commit correlation
 *  is by bound-tab + main-frame + job + time-window, NOT requestId (amend §2.2).
 *  CommittedEvent therefore has no requestId field. */

import type { NavOutcome, PageDetection } from './state.js';

/** Lock-free scope snapshot the thin navMonitor reads to filter bound-tab
 *  events without taking the Controller mutex (the Controller may be mid-tick). */
export interface NavScope {
  boundTabId: number | null;
}

export interface BeforeRequestEvent {
  tabId: number;
  frameId: number;
  type: string;        // webRequest resource type, e.g. 'main_frame'
  url: string;
  requestId: string;
  timeStamp: number;
}

export interface BeforeRedirectEvent {
  tabId: number;
  frameId: number;
  type: string;
  url: string;           // pre-redirect URL
  redirectUrl: string;  // target of the redirect (same-crawl-site gate target)
  requestId: string;
  timeStamp: number;
}

export interface CommittedEvent {
  tabId: number;
  frameId: number;
  documentId: string;
  url: string;
  timeStamp: number;
  // NO requestId — webNavigation.onCommitted does not carry it (amend §2.2).
}

export interface NavCompletedEvent {
  tabId: number;
  url: string;
  statusCode: number;
  frameId: number;
  requestId: string;
  documentId?: string;
  timeStamp: number;
}

export interface NavErrorEvent {
  tabId: number;
  url: string;
  error: string;
  frameId: number;
  requestId: string;
  timeStamp: number;
}

export interface PageReadyEvent {
  documentId: string;
  url: string;
  detection: PageDetection;
  timeStamp: number;
}

/** Canonical classifier input (status.ts/classifyNavigation consumes this). */
export type NavInput =
  | { kind: 'completed'; statusCode: number }
  | { kind: 'error'; error: string };

/** The forwarding target navMonitor calls after scope-filtering. Each method
 *  re-acquires the Controller mutex internally; navMonitor must NOT hold any
 *  lock when calling these (it has none). */
export interface NavController {
  getNavScope(): NavScope;
  deliverBeforeRequest(e: BeforeRequestEvent): Promise<void>;
  deliverBeforeRedirect(e: BeforeRedirectEvent): Promise<void>;
  deliverCommitted(e: CommittedEvent): Promise<void>;
  deliverHttpEvent(e: NavCompletedEvent, outcome: NavOutcome): Promise<void>;
  deliverError(e: NavErrorEvent, outcome: NavOutcome): Promise<void>;
  deliverPageReady(e: PageReadyEvent): Promise<void>;
}

/** Main-frame filter: frameId === 0 for both webNavigation and main-frame
 *  webRequest events. Sub-frame errors/commits are noise (spec §3.4, §1.4). */
export function isMainFrame(e: { frameId?: number }): boolean {
  return e.frameId === 0;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='isMainFrame|navEvents module loads' 2>&1 | tail -15`
Expected: PASS — both tests green.

- [ ] **Step 5: Typecheck + full suite green (no other file touched)**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 71 fail 0` (69 baseline + 2 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/shared/navEvents.ts browserext/tests/shared/navEvents.test.mjs
git commit -m "feat(browserext): pure nav-event types + NavController forwarding interface (slice 3)"
```

---

## Task 2: shared/urlGate.ts — port same-crawl-site gate + wechat/offsite detection

**Files:**
- Create: `browserext/src/shared/urlGate.ts`
- Create: `browserext/tests/shared/urlGate.test.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces (all pure, byte-aligned with userscript `utils.ts`/`main.ts`):
  - `urlMatches(a: string, b: string): boolean` — verbatim port of userscript `utils.urlMatches` (ignores protocol; hostname + normalized path + sorted-search equality; `hasExplicitPort` rejects ports).
  - `hasExplicitPort(url: string | URL): boolean` — verbatim port of userscript `utils.hasExplicitPort`.
  - `siteRoot(host: string): string` — verbatim port of userscript `utils.siteRoot` (`.edu.cn`/`.ac.cn`/`.com.cn` → last 3 labels; else last 2).
  - `sameCrawlSite(a: string, b: string): boolean` — `github.io` hosts must match exactly (each `*.github.io` is a different user site); `edu.cn` hosts must share `siteRoot`. Mirrors userscript `sameSite` + the spec §3.2 condition 3 github.io carve-out.
  - `isWechatHost(hostname: string): boolean` — `mp.weixin.qq.com` or `weixin.qq.com`.
  - `redirectKind(redirectUrl: string, requestedUrl: string): 'onsite' | 'wechat' | 'offsite'` — returns `wechat` if `isWechatHost(redirectUrl)`, else `offsite` if `!sameCrawlSite(redirectUrl, requestedUrl)`, else `onsite`.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/shared/urlGate.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('urlMatches: ignores protocol + trailing slash; sorts query; ports reject', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.urlMatches('http://x.edu.cn/p', 'https://x.edu.cn/p'), true);
    assert.equal(mod.urlMatches('http://x.edu.cn/p/', 'https://x.edu.cn/p'), true);
    assert.equal(mod.urlMatches('http://x.edu.cn/p?a=1&b=2', 'https://x.edu.cn/p?b=2&a=1'), true);
    assert.equal(mod.urlMatches('http://x.edu.cn/p?a=1', 'https://x.edu.cn/p?a=2'), false);
    assert.equal(mod.urlMatches('http://x.edu.cn/p', 'https://y.edu.cn/p'), false);
    assert.equal(mod.urlMatches('http://x.edu.cn/p', 'https://x.edu.cn/q'), false);
    assert.equal(mod.urlMatches('http://x.edu.cn:8080/p', 'http://x.edu.cn/p'), false);
    assert.equal(mod.urlMatches('not a url', 'http://x.edu.cn/p'), false);
  } finally {
    await cleanup();
  }
});

test('sameCrawlSite: edu.cn shares site root; github.io must match exactly', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.sameCrawlSite('https://a.xjtu.edu.cn/p', 'https://xjtu.edu.cn/'), true);
    assert.equal(mod.sameCrawlSite('https://xjtu.edu.cn/p', 'https://xjtu.edu.cn/q'), true);
    assert.equal(mod.sameCrawlSite('https://xjtu.edu.cn/', 'https://attacker.edu.cn/'), false);
    assert.equal(mod.sameCrawlSite('https://foo.github.io/', 'https://foo.github.io/x'), true);
    assert.equal(mod.sameCrawlSite('https://foo.github.io/', 'https://bar.github.io/'), false);
    assert.equal(mod.sameCrawlSite('http://xjtu.edu.cn/', 'https://xjtu.edu.cn/'), true);
  } finally {
    await cleanup();
  }
});

test('redirectKind: wechat vs offsite vs onsite', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.redirectKind('https://mp.weixin.qq.com/s?x=1', 'https://xjtu.edu.cn/'), 'wechat');
    assert.equal(mod.redirectKind('https://weixin.qq.com/', 'https://xjtu.edu.cn/'), 'wechat');
    assert.equal(mod.redirectKind('https://attacker.edu.cn/', 'https://xjtu.edu.cn/'), 'offsite');
    assert.equal(mod.redirectKind('https://a.xjtu.edu.cn/x', 'https://xjtu.edu.cn/'), 'onsite');
    assert.equal(mod.redirectKind('https://xjtu.edu.cn/renxueguang', 'https://xjtu.edu.cn/'), 'onsite');
  } finally {
    await cleanup();
  }
});

test('hasExplicitPort + siteRoot mirror userscript utils', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.hasExplicitPort('http://x.edu.cn:8080/'), true);
    assert.equal(mod.hasExplicitPort('http://x.edu.cn/'), false);
    assert.equal(mod.siteRoot('a.xjtu.edu.cn'), 'xjtu.edu.cn');
    assert.equal(mod.siteRoot('foo.github.io'), 'github.io');
    assert.equal(mod.siteRoot('x.scu.com.cn'), 'scu.com.cn');
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='urlMatches|sameCrawlSite|redirectKind|hasExplicitPort' 2>&1 | tail -15`
Expected: FAIL — `../src/shared/urlGate.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/shared/urlGate.ts`:
```ts
/** Same-crawl-site URL gate + wechat/offsite redirect detection (spec §3.2 cond 3,
 *  amend §3.1, §3.5). Verbatim port of userscripts/src/utils.ts urlMatches /
 *  hasExplicitPort / siteRoot, plus the sameCrawlSite rule (github.io hosts must
 *  match EXACTLY — each *.github.io is a different user site; edu.cn shares the
 *  site root) and the wechat/offsite redirect classification from
 *  userscripts/src/main.ts. Pure — no DOM, no chrome. Slice-4 content reuses
 *  urlMatches for the form-action targetUrl match (amend §5.1). */

const WECHAT_HOSTS = new Set(['mp.weixin.qq.com', 'weixin.qq.com']);

export function hasExplicitPort(url: string | URL): boolean {
  if (url instanceof URL) return url.port !== '';
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

function normalizedSearch(url: URL): string {
  if (!url.search) return '';
  const params = [...url.searchParams.entries()].sort(([aKey, aValue], [bKey, bValue]) => {
    const keyOrder = aKey.localeCompare(bKey);
    return keyOrder || aValue.localeCompare(bValue);
  });
  return params.map(([key, value]) => `${key}=${value}`).join('&');
}

/** Byte-aligned with userscripts/src/utils.ts urlMatches. */
export function urlMatches(a: string, b: string): boolean {
  try {
    if (hasExplicitPort(a) || hasExplicitPort(b)) return false;
    const u1 = new URL(a);
    const u2 = new URL(b);
    return (
      u1.hostname === u2.hostname &&
      u1.pathname.replace(/\/+$/, '') === u2.pathname.replace(/\/+$/, '') &&
      normalizedSearch(u1) === normalizedSearch(u2)
    );
  } catch {
    return false;
  }
}

function siteRoot(host: string): string {
  const parts = host.split('.');
  // Handle .edu.cn / .ac.cn / .com.cn style TLDs (mirrors userscript utils.siteRoot)
  if (parts.length >= 3 && parts.at(-1) === 'cn' && ['edu', 'ac', 'com'].includes(parts.at(-2)!)) {
    return parts.slice(-3).join('.');
  }
  return parts.slice(-2).join('.');
}

/** github.io hosts must match EXACTLY (each *.github.io is a different user site);
 *  edu.cn hosts share the site root. Mirrors userscript sameSite + spec §3.2 cond 3. */
export function sameCrawlSite(a: string, b: string): boolean {
  try {
    if (hasExplicitPort(a) || hasExplicitPort(b)) return false;
    const u1 = new URL(a);
    const u2 = new URL(b);
    const h1 = u1.hostname.toLowerCase();
    const h2 = u2.hostname.toLowerCase();
    if (h1.endsWith('.github.io') || h1 === 'github.io' || h2.endsWith('.github.io') || h2 === 'github.io') {
      return h1 === h2;
    }
    return siteRoot(h1) === siteRoot(h2);
  } catch {
    return false;
  }
}

export function isWechatHost(hostname: string): boolean {
  return WECHAT_HOSTS.has(hostname.toLowerCase());
}

/** Classify a redirect target against the requested URL (amend §3.1, §3.5).
 *  wechat wins outright (terminal skip wechat_redirect, no budget); a redirect
 *  to a different crawl-site root is offsite (terminal skip offsite_redirect, no
 *  budget); otherwise onsite (keep waiting for the final onCompleted). */
export function redirectKind(redirectUrl: string, requestedUrl: string): 'onsite' | 'wechat' | 'offsite' {
  try {
    const host = new URL(redirectUrl).hostname.toLowerCase();
    if (isWechatHost(host)) return 'wechat';
  } catch {
    return 'offsite';   // unparseable redirect target → treat as offsite (safe skip)
  }
  return sameCrawlSite(redirectUrl, requestedUrl) ? 'onsite' : 'offsite';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='urlMatches|sameCrawlSite|redirectKind|hasExplicitPort' 2>&1 | tail -15`
Expected: PASS — all four tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 75 fail 0` (71 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/shared/urlGate.ts browserext/tests/shared/urlGate.test.mjs
git commit -m "feat(browserext): same-crawl-site gate + wechat/offsite redirect detection (slice 3)"
```

---

## Task 3: Pure landing evaluation (amend §2.3 five conditions)

**Files:**
- Create: `browserext/src/controller/landing.ts`
- Create: `browserext/tests/controller/landing.test.mjs`

**Interfaces:**
- Consumes: `ControllerState`, `NavOutcome` (types) from `../shared/state.js`; `sameCrawlSite`, `isWechatHost` from `../shared/urlGate.js` (Task 2).
- Produces: `LandingVerdict` discriminated union + `evaluateLanding(state, now)`:
  ```ts
  export type LandingVerdict =
    | { kind: 'landed'; acceptedUrl: string; documentId: string }
    | { kind: 'terminal_skip'; reason: 'not_found' | 'content_removed' | 'empty_page' | 'wechat_redirect' | 'offsite_redirect' }
    | { kind: 'error_retry'; outcome: Exclude<NavOutcome, 'ok'>; detail?: string }
    | { kind: 'waiting' };
  export function evaluateLanding(state: ControllerState, now: number): LandingVerdict;
  ```
  Rules (amend §2.3 + §3.1 + §3.5):
  - No `navigation` or no `commit` → `waiting`.
  - `commit.committedUrl` fails `sameCrawlSite(commit.committedUrl, navigation.requestedUrl)` → `terminal_skip` `wechat_redirect` if `isWechatHost(commit.committedUrl host)`, else `offsite_redirect`.
  - If `navigation.pageReady` exists with `documentId !== commit.documentId` → `waiting` (stale PAGE_READY).
  - If `navigation.pageReady` exists with matching documentId:
    - `detection.terminalReason === 'not_found'|'content_removed'` → `terminal_skip` (that reason).
    - `detection.terminalReason === 'empty_page'` → `terminal_skip` `empty_page`.
    - `detection.errorPage === true` → `error_retry` `outcome: 'gateway'` (navigate-kind gateway funnel; amend §2.3).
    - (`terminalReason === null && errorPage === false` → continue to signal checks below.)
  - If `navigation.http` present but `requestId !== navigation.requestId` → `waiting` (stale attempt's HTTP; only retry clears).
  - If `navigation.http` present, requestId matches, **and** http carried a `documentId` that `!== commit.documentId` → `waiting` (join fails; amend §2.2 — await correlated event). (If http has no documentId, join satisfied by requestId alone.)
  - If `navigation.http.outcome` is `not_found|rate_limited|gateway|nav_error|unexpected_status` → `error_retry` with that outcome (and `detail` = status code for `unexpected_status`, error string for `nav_error`).
  - If `navigation.http.outcome === 'ok'` **and** `pageReady` matches (clean) → `landed` with `acceptedUrl = commit.committedUrl`, `documentId = commit.documentId`.
  - Otherwise (clean pageReady, http not yet `ok`, e.g. http still missing) → `waiting`.

  Pure: reads only `state.navigation` and `now`; does not mutate. (`now` is reserved for future deadline-coupling; landing is event-driven here, deadlines live in `deadline.ts`.)

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/landing.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function nav(overrides = {}) {
  return {
    jobId: 'job-1',
    requestedUrl: 'https://xjtu.edu.cn/faculty',
    issuedAt: 1000,
    attempt: 1,
    kind: 'navigate',
    commit: { documentId: 'DOC-1', committedUrl: 'https://xjtu.edu.cn/faculty', committedAt: 1100 },
    ...overrides,
  };
}
function state(navigation) {
  return {
    boundTabId: 7, boundAt: 1000, connected: true, autoMode: true, paused: false,
    currentJob: { id: 'job-1', url: 'https://xjtu.edu.cn/faculty' },
    phase: 'navigating', phaseStartedAt: 1000, navigation, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
  };
}
const CLEAN = { errorPage: false, terminalReason: null };

test('landed: commit + http ok (same requestId) + same-documentId clean PAGE_READY', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-1', documentId: 'DOC-1', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'https://xjtu.edu.cn/faculty', detection: CLEAN },
    }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'landed');
    assert.equal(v.acceptedUrl, 'https://xjtu.edu.cn/faculty');
    assert.equal(v.documentId, 'DOC-1');
  } finally { await cleanup(); }
});

test('landed: http with NO documentId still lands when requestId matches + clean PAGE_READY', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-1', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'https://xjtu.edu.cn/faculty', detection: CLEAN },
    }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'landed');
  } finally { await cleanup(); }
});

test('waiting: no navigation yet', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    assert.equal(mod.evaluateLanding(state(null), 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('waiting: commit present but no PAGE_READY / no http yet', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ requestId: 'REQ-1' }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('terminal_skip offsite_redirect: commit URL fails same-crawl-site gate', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ commit: { documentId: 'DOC-1', committedUrl: 'https://attacker.edu.cn/x', committedAt: 1100 } }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'terminal_skip');
    assert.equal(v.reason, 'offsite_redirect');
  } finally { await cleanup(); }
});

test('terminal_skip wechat_redirect: commit URL is a wechat host', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ commit: { documentId: 'DOC-1', committedUrl: 'https://mp.weixin.qq.com/s?x', committedAt: 1100 } }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'terminal_skip');
    assert.equal(v.reason, 'wechat_redirect');
  } finally { await cleanup(); }
});

test('terminal_skip not_found / content_removed / empty_page from PAGE_READY detection', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    for (const [reason, expected] of [['not_found','not_found'],['content_removed','content_removed'],['empty_page','empty_page']]) {
      const s = state(nav({ pageReady: { documentId: 'DOC-1', url: 'u', detection: { errorPage: false, terminalReason: reason } } }));
      const v = mod.evaluateLanding(s, 5000);
      assert.equal(v.kind, 'terminal_skip');
      assert.equal(v.reason, expected);
    }
  } finally { await cleanup(); }
});

test('error_retry gateway: PAGE_READY errorPage true on a navigate', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ pageReady: { documentId: 'DOC-1', url: 'u', detection: { errorPage: true, terminalReason: null } } }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'error_retry');
    assert.equal(v.outcome, 'gateway');
  } finally { await cleanup(); }
});

test('error_retry: http outcome not_found/gateway/rate_limited/unexpected_status/nav_error (requestId matched)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    for (const [outcome, code] of [['not_found',404],['gateway',502],['rate_limited',429],['unexpected_status',403],['nav_error',0]]) {
      const http = outcome === 'nav_error'
        ? { requestId: 'REQ-1', outcome: 'nav_error', error: 'ERR_X' }
        : { requestId: 'REQ-1', documentId: 'DOC-1', statusCode: code, outcome };
      const s = state(nav({ requestId: 'REQ-1', http, pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN } }));
      const v = mod.evaluateLanding(s, 5000);
      assert.equal(v.kind, 'error_retry', `${outcome} → error_retry`);
      assert.equal(v.outcome, outcome, `${outcome} outcome preserved`);
    }
  } finally { await cleanup(); }
});

test('error_retry unexpected_status carries statusCode detail; nav_error carries error detail', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s1 = state(nav({ requestId: 'REQ-1', http: { requestId: 'REQ-1', documentId: 'DOC-1', statusCode: 403, outcome: 'unexpected_status' }, pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN } }));
    const v1 = mod.evaluateLanding(s1, 5000);
    assert.equal(v1.kind, 'error_retry'); assert.equal(v1.detail, '403');
    const s2 = state(nav({ requestId: 'REQ-1', http: { requestId: 'REQ-1', outcome: 'nav_error', error: 'ERR_TIMED_OUT' }, pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN } }));
    const v2 = mod.evaluateLanding(s2, 5000);
    assert.equal(v2.kind, 'error_retry'); assert.equal(v2.detail, 'ERR_TIMED_OUT');
  } finally { await cleanup(); }
});

test('waiting: stale PAGE_READY whose documentId !== commit.documentId', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ pageReady: { documentId: 'DOC-OLD', url: 'u', detection: CLEAN } }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('waiting: http documentId !== commit documentId (join fails; await correlated event)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-1', documentId: 'DOC-OTHER', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN },
    }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('waiting: http from a different (stale) requestId — not yet cleared', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-OLD', documentId: 'DOC-1', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN },
    }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='landed:|waiting:|terminal_skip|error_retry' 2>&1 | tail -20`
Expected: FAIL — `../src/controller/landing.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/landing.ts`:
```ts
/** Pure landing evaluation — the amend §2.3 five-condition rule. Reads ONLY
 *  state.navigation (+ now, reserved); mutates nothing. The Controller calls
 *  this after each signal arrives and acts on the verdict:
 *    landed        → phase 'landed', set acceptedUrl (slice 4 then dispatches capture)
 *    terminal_skip → skip (no budget)
 *    error_retry   → retry funnel (amend §3.2 table)
 *    waiting       → keep observing
 *
 *  Five conditions (amend §2.3): (1) correlated main-frame commit, phase navigating|acting;
 *  (2) acceptable HTTP outcome from the SAME requestId final onCompleted, and if the HTTP
 *  event carried a documentId it must equal commit.documentId; (3) committedUrl passes the
 *  same-crawl-site gate; (4) PAGE_READY whose documentId equals commit.documentId; (5)
 *  PAGE_READY.detection errorPage===false && terminalReason===null.
 *
 *  requestId/documentId/job/time/phase correlation (binding, join) is established by the
 *  Controller's deliver* handlers BEFORE landing is evaluated; this function only checks
 *  whether the already-correlated signals satisfy the five conditions. */

import type { ControllerState, NavOutcome } from '../shared/state.js';
import { isWechatHost, sameCrawlSite } from '../shared/urlGate.js';

export type LandingVerdict =
  | { kind: 'landed'; acceptedUrl: string; documentId: string }
  | {
      kind: 'terminal_skip';
      reason: 'not_found' | 'content_removed' | 'empty_page' | 'wechat_redirect' | 'offsite_redirect';
    }
  | { kind: 'error_retry'; outcome: Exclude<NavOutcome, 'ok'>; detail?: string }
  | { kind: 'waiting' };

const TERMINAL_REASON_TO_SKIP = {
  not_found: 'not_found',
  content_removed: 'content_removed',
  empty_page: 'empty_page',
} as const;

function isWechatHostUrl(url: string): boolean {
  try {
    return isWechatHost(new URL(url).hostname);
  } catch {
    return false;
  }
}

export function evaluateLanding(state: ControllerState, _now: number): LandingVerdict {
  const nav = state.navigation;
  if (!nav || !nav.commit) return { kind: 'waiting' };

  // (3) same-crawl-site gate on the committed URL.
  if (!sameCrawlSite(nav.commit.committedUrl, nav.requestedUrl)) {
    return {
      kind: 'terminal_skip',
      reason: isWechatHostUrl(nav.commit.committedUrl) ? 'wechat_redirect' : 'offsite_redirect',
    };
  }

  // (4)+(5) PAGE_READY must match the commit documentId and be clean — but a stale
  // PAGE_READY (wrong documentId) means we are still waiting for the current document's.
  if (nav.pageReady) {
    if (nav.pageReady.documentId !== nav.commit.documentId) return { kind: 'waiting' };
    const d = nav.pageReady.detection;
    if (d.terminalReason === 'not_found' || d.terminalReason === 'content_removed') {
      return { kind: 'terminal_skip', reason: TERMINAL_REASON_TO_SKIP[d.terminalReason as 'not_found' | 'content_removed'] };
    }
    if (d.terminalReason === 'empty_page') {
      return { kind: 'terminal_skip', reason: 'empty_page' };
    }
    if (d.errorPage) {
      // amend §2.3: errorPage on a navigate enters the gateway retry budget (not a terminal skip).
      return { kind: 'error_retry', outcome: 'gateway' };
    }
  }

  // (2) HTTP outcome from the SAME requestId; if it carried a documentId it must join commit.
  if (nav.http) {
    if (nav.http.requestId !== nav.requestId) return { kind: 'waiting' };   // stale attempt's HTTP
    if (nav.http.documentId !== undefined && nav.http.documentId !== nav.commit.documentId) {
      return { kind: 'waiting' };   // join fails; await a correlated onCompleted
    }
    const outcome = nav.http.outcome;
    if (outcome !== 'ok') {
      const detail = outcome === 'unexpected_status'
        ? String(nav.http.statusCode)
        : outcome === 'nav_error'
          ? nav.http.error
          : undefined;
      return { kind: 'error_retry', outcome, detail };
    }
  }

  // (1)+(2)+(4)+(5) all satisfied and http ok.
  if (nav.pageReady && nav.http?.outcome === 'ok') {
    return {
      kind: 'landed',
      acceptedUrl: nav.commit.committedUrl,
      documentId: nav.commit.documentId,
    };
  }

  return { kind: 'waiting' };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='landed:|waiting:|terminal_skip|error_retry' 2>&1 | tail -20`
Expected: PASS — all tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 88 fail 0` (75 + 13 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/landing.ts browserext/tests/controller/landing.test.mjs
git commit -m "feat(browserext): pure landing evaluation (amend §2.3 five conditions)"
```

---

## Task 4: Pure retry funnel decision (amend §3.2 table)

**Files:**
- Create: `browserext/src/controller/funnel.ts`
- Create: `browserext/tests/controller/funnel.test.mjs`

**Interfaces:**
- Consumes: `NavOutcome` from `../shared/state.js`.
- Produces: `RetryOutcome = Exclude<NavOutcome, 'ok'>` and `FunnelDecision` discriminated union + `funnelDecision(outcome, attempt, detail?)`:
  ```ts
  export type RetryOutcome = Exclude<NavOutcome, 'ok'>;   // not_found|gateway|rate_limited|nav_error|unexpected_status
  export type FunnelDecision =
    | { kind: 'skip'; reason: string }
    | { kind: 'fail'; error: { kind: 'nav_error'; error: string }
                 | { kind: 'gateway_5xx' }
                 | { kind: 'unexpected_status'; statusCode: number }
                 | { kind: 'rate_limited' } }
    | { kind: 'retry_navigate' };
  export function funnelDecision(outcome: RetryOutcome, attempt: number, detail?: string): FunnelDecision;
  ```
  Table (amend §3.2, navigate kind):
  - `not_found` → `skip` `not_found` (terminal, no budget).
  - `rate_limited` → `fail` `rate_limited` (immediate, no refresh).
  - `gateway` → `attempt >= 3` ? `fail` `gateway_5xx` : `retry_navigate`.
  - `unexpected_status` → `attempt >= 3` ? `fail` `unexpected_status:<statusCode>` (detail is the status code string) : `retry_navigate`.
  - `nav_error` → `attempt >= 3` ? `fail` `nav_error:<error>` (detail is the error string) : `retry_navigate`.

  `attempt` is 1-based. Budget = 3 means attempts 1 and 2 retry, attempt 3 fails. `content_unavailable` is NOT a `RetryOutcome` (it never re-navigates; handled by `deadline.ts`).

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/funnel.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('not_found → terminal skip, no budget (any attempt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    for (const attempt of [1, 2, 3, 5]) {
      const d = mod.funnelDecision('not_found', attempt);
      assert.equal(d.kind, 'skip', `attempt ${attempt}`);
      assert.equal(d.reason, 'not_found');
    }
  } finally { await cleanup(); }
});

test('rate_limited → immediate fail rate_limited, no refresh (any attempt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    for (const attempt of [1, 2, 3]) {
      const d = mod.funnelDecision('rate_limited', attempt);
      assert.equal(d.kind, 'fail', `attempt ${attempt}`);
      assert.deepEqual(d.error, { kind: 'rate_limited' });
    }
  } finally { await cleanup(); }
});

test('gateway: attempts 1,2 retry; attempt 3 fails gateway_5xx', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    assert.equal(mod.funnelDecision('gateway', 1).kind, 'retry_navigate');
    assert.equal(mod.funnelDecision('gateway', 2).kind, 'retry_navigate');
    const d = mod.funnelDecision('gateway', 3);
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'gateway_5xx' });
  } finally { await cleanup(); }
});

test('unexpected_status: attempts 1,2 retry; attempt 3 fails unexpected_status:<code>', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    assert.equal(mod.funnelDecision('unexpected_status', 1, '403').kind, 'retry_navigate');
    assert.equal(mod.funnelDecision('unexpected_status', 2, '403').kind, 'retry_navigate');
    const d = mod.funnelDecision('unexpected_status', 3, '403');
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'unexpected_status', statusCode: 403 });
  } finally { await cleanup(); }
});

test('nav_error: attempts 1,2 retry; attempt 3 fails nav_error:<error>', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    assert.equal(mod.funnelDecision('nav_error', 1, 'ERR_CONNECTION_REFUSED').kind, 'retry_navigate');
    assert.equal(mod.funnelDecision('nav_error', 2, 'ERR_CONNECTION_REFUSED').kind, 'retry_navigate');
    const d = mod.funnelDecision('nav_error', 3, 'ERR_CONNECTION_REFUSED');
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'nav_error', error: 'ERR_CONNECTION_REFUSED' });
  } finally { await cleanup(); }
});

test('unexpected_status without detail → fail with statusCode 0 (defensive)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    const d = mod.funnelDecision('unexpected_status', 3);
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'unexpected_status', statusCode: 0 });
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='not_found → terminal|rate_limited → immediate|gateway: attempts|unexpected_status: attempts|nav_error: attempts|unexpected_status without' 2>&1 | tail -20`
Expected: FAIL — `../src/controller/funnel.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/funnel.ts`:
```ts
/** Pure retry-funnel decision — the amend §3.2 navigate-kind table. NO chrome,
 *  no IO. The Controller calls this with the error_retry outcome from
 *  evaluateLanding (+ the current 1-based attempt) and acts on the verdict:
 *    skip           → POST /jobs/{id}/skip (terminal, no budget)
 *    fail           → POST /jobs/{id}/fail (rate_limited immediate; gateway/unexpected/nav_error at attempt 3)
 *    retry_navigate → bump attempt, clear requestId/commit/http/pageReady/acceptedUrl, call tabs.update again
 *
 *  Budget = 3: attempts 1 and 2 retry-navigate, attempt 3 fails. not_found and
 *  rate_limited are terminal and never consume the budget. content_unavailable
 *  is NOT a RetryOutcome — it never re-navigates (see deadline.ts). */

import type { ControllerError, NavOutcome } from '../shared/state.js';

export type RetryOutcome = Exclude<NavOutcome, 'ok'>;

export type FunnelDecision =
  | { kind: 'skip'; reason: string }
  | { kind: 'fail'; error: Exclude<ControllerError, { kind: 'content_unavailable' }> }
  | { kind: 'retry_navigate' };

const BUDGET = 3;   // attempt 3 → fail

export function funnelDecision(outcome: RetryOutcome, attempt: number, detail?: string): FunnelDecision {
  if (outcome === 'not_found') return { kind: 'skip', reason: 'not_found' };
  if (outcome === 'rate_limited') return { kind: 'fail', error: { kind: 'rate_limited' } };
  if (attempt >= BUDGET) {
    if (outcome === 'gateway') return { kind: 'fail', error: { kind: 'gateway_5xx' } };
    if (outcome === 'unexpected_status') {
      const code = Number(detail);
      return { kind: 'fail', error: { kind: 'unexpected_status', statusCode: Number.isFinite(code) ? code : 0 } };
    }
    // nav_error
    return { kind: 'fail', error: { kind: 'nav_error', error: detail ?? '' } };
  }
  return { kind: 'retry_navigate' };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='not_found → terminal|rate_limited → immediate|gateway: attempts|unexpected_status: attempts|nav_error: attempts|unexpected_status without' 2>&1 | tail -20`
Expected: PASS — all tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 94 fail 0` (88 + 6 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/funnel.ts browserext/tests/controller/funnel.test.mjs
git commit -m "feat(browserext): pure retry funnel decision (amend §3.2 table)"
```

---

## Task 5: Pure two-level deadline (amend §2.4)

**Files:**
- Create: `browserext/src/controller/deadline.ts`
- Create: `browserext/tests/controller/deadline.test.mjs`

**Interfaces:**
- Consumes: `ControllerState` from `../shared/state.js`.
- Produces: `DeadlineVerdict` + `navTimeoutKind(state, now)`:
  ```ts
  export type DeadlineVerdict =
    | { kind: 'none' }
    | { kind: 'never_landed' }
    | { kind: 'content_unavailable'; missing: 'page_ready' | 'http_outcome'; sourceDocumentId: string };
  export function navTimeoutKind(state: ControllerState, now: number): DeadlineVerdict;
  ```
  Rules (amend §2.4):
  - No `navigation` → `none`.
  - `navigation` with no `commit`: `now >= issuedAt + 30_000` → `never_landed` (only a navigate may enter the funnel). Else `none`.
  - `navigation` with `commit`: `now >= committedAt + 30_000` → `content_unavailable` with `missing: 'page_ready'` if `pageReady` is absent/mismatched, else `missing: 'http_outcome'` if `http` is absent/mismatched. `sourceDocumentId = commit.documentId`. Else `none`.
  - Constants `NAV_DEADLINE_MS = 30_000`, `LANDING_SIGNALS_DEADLINE_MS = 30_000` exported.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/deadline.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function state(navigation) {
  return {
    boundTabId: 7, boundAt: 1000, connected: true, autoMode: true, paused: false,
    currentJob: { id: 'job-1', url: 'https://x.edu.cn/p' },
    phase: 'navigating', phaseStartedAt: 1000, navigation, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
  };
}

test('none: no navigation', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    assert.equal(mod.navTimeoutKind(state(null), 99999).kind, 'none');
  } finally { await cleanup(); }
});

test('none: navigation with no commit, within 30s (now < issuedAt+30s)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate' });
    assert.equal(mod.navTimeoutKind(s, 30999).kind, 'none');   // 1000+30000 = 31000; 30999 < 31000
  } finally { await cleanup(); }
});

test('never_landed: no commit past navigationDeadlineAt (now >= issuedAt+30s)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate' });
    assert.equal(mod.navTimeoutKind(s, 31000).kind, 'never_landed');   // 31000 >= 31000
  } finally { await cleanup(); }
});

test('none: commit present, within landingSignalsDeadlineAt (now < committedAt+30s)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 } });
    assert.equal(mod.navTimeoutKind(s, 31499).kind, 'none');   // 1500+30000 = 31500; 31499 < 31500
  } finally { await cleanup(); }
});

test('content_unavailable/page_ready: commit present, PAGE_READY missing, now >= committedAt+30s', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 } });
    const v = mod.navTimeoutKind(s, 31500);
    assert.equal(v.kind, 'content_unavailable');
    assert.equal(v.missing, 'page_ready');
    assert.equal(v.sourceDocumentId, 'DOC-1');
  } finally { await cleanup(); }
});

test('content_unavailable/http_outcome: commit + PAGE_READY present, HTTP missing, now >= committedAt+30s', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 },
      pageReady: { documentId: 'DOC-1', url: 'u', detection: { errorPage: false, terminalReason: null } } });
    const v = mod.navTimeoutKind(s, 31500);
    assert.equal(v.kind, 'content_unavailable');
    assert.equal(v.missing, 'http_outcome');
  } finally { await cleanup(); }
});

test('content_unavailable prioritizes page_ready when both missing', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 } });
    assert.equal(mod.navTimeoutKind(s, 31500).missing, 'page_ready');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='none: no navigation|none: navigation with no commit|never_landed:|none: commit present|content_unavailable' 2>&1 | tail -20`
Expected: FAIL — `../src/controller/deadline.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/deadline.ts`:
```ts
/** Pure two-level navigation deadline (amend §2.4). NO chrome, no IO. The
 *  Controller calls navTimeoutKind(state, now) in the tick and acts:
 *    never_landed        → navigation retry funnel (only a navigate kind may re-navigate)
 *    content_unavailable → NEVER refresh; set lastError content_unavailable{missing}, phase 'error',
 *                        recoveryExhausted=true (PAGE_READY/HTTP are not RPC-recoverable; slice 4 adds
 *                        RPC recovery for capture/prepare/perform only)
 *    none                → keep observing
 *
 *  Two deadlines:
 *    navigationDeadlineAt    = issuedAt + 30s   (no commit yet) → never_landed funnel
 *    landingSignalsDeadlineAt = committedAt + 30s (commit present, PAGE_READY/HTTP missing) → content_unavailable
 *
 *  page_ready is checked before http_outcome when both are missing (PAGE_READY is the
 *  stronger signal — if the page never reported ready, that is the root cause). */

import type { ControllerState } from '../shared/state.js';

export const NAV_DEADLINE_MS = 30_000;
export const LANDING_SIGNALS_DEADLINE_MS = 30_000;

export type DeadlineVerdict =
  | { kind: 'none' }
  | { kind: 'never_landed' }
  | {
      kind: 'content_unavailable';
      missing: 'page_ready' | 'http_outcome';
      sourceDocumentId: string;
    };

export function navTimeoutKind(state: ControllerState, now: number): DeadlineVerdict {
  const nav = state.navigation;
  if (!nav) return { kind: 'none' };

  if (!nav.commit) {
    // No commit yet → navigation deadline (only a navigate may enter the funnel).
    if (now >= nav.issuedAt + NAV_DEADLINE_MS) return { kind: 'never_landed' };
    return { kind: 'none' };
  }

  // Commit present → landing-signals deadline. Never refresh.
  if (now < nav.commit.committedAt + LANDING_SIGNALS_DEADLINE_MS) return { kind: 'none' };

  const pageReadyMissing = !nav.pageReady || nav.pageReady.documentId !== nav.commit.documentId;
  if (pageReadyMissing) {
    return { kind: 'content_unavailable', missing: 'page_ready', sourceDocumentId: nav.commit.documentId };
  }
  const httpMissing = !nav.http
    || nav.http.requestId !== nav.requestId
    || (nav.http.documentId !== undefined && nav.http.documentId !== nav.commit.documentId);
  if (httpMissing) {
    return { kind: 'content_unavailable', missing: 'http_outcome', sourceDocumentId: nav.commit.documentId };
  }
  return { kind: 'none' };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='none: no navigation|none: navigation with no commit|never_landed:|none: commit present|content_unavailable' 2>&1 | tail -20`
Expected: PASS — all tests green.

- [ ] **Step 5: Typecheck + full suite green**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite `pass 101 fail 0` (94 + 7 new).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/deadline.ts browserext/tests/controller/deadline.test.mjs
git commit -m "feat(browserext): pure two-level navigation deadline (amend §2.4)"
```

---

## Task 6: Controller — claim + persist-before-navigate + deliver* handlers + getNavScope + deadlines

**Files:**
- Modify: `browserext/src/api.ts` (add `claimNextJob`)
- Modify: `browserext/src/controller/controller.ts`
- Modify: `browserext/tests/controller/controller.test.mjs`
- Modify: `browserext/tests/api.test.mjs`

**Interfaces:**
- Consumes: `evaluateLanding` (Task 3), `funnelDecision` (Task 4), `navTimeoutKind` (Task 6 → Task 5), `redirectKind`/`urlMatches` (Task 2), event types from `shared/navEvents.js` (Task 1), `ApiClient`. **Does NOT depend on `status.ts` or `chrome.ts`** (the Controller receives the outcome from navMonitor and uses shared/navEvents event types, not chrome.ts types) — so Phase-1 `status.ts`/`navMonitor.ts` stay green through this task.
- Produces:
  - `api.ts` gains `claimNextJob(): Promise<FetchJob | null>` (GET `/jobs/next`; 204 → null; errors → null).
  - `CrawlController` interface gains `getNavScope(): NavScope` and the six `deliver*` methods (implementing `NavController`).
  - `tick(now)` adds, after the slice-2 reconcile+heartbeat: the claim step (`phase==='idle' && currentJob===null && autoMode && !paused && boundTabId!==null` → `claimNextJob`; 200 → cache job, `phase='assigned'`; 204/null → no-op) and the navigate step (`phase==='assigned' && currentJob && autoMode && !paused && boundTabId!==null` → persist `navigation` then `chrome.updateTabUrl(boundTabId, currentJob.url)`, `phase='navigating'`), and the deadline check (`phase==='navigating'` → `navTimeoutKind`; `never_landed` → funnel with `nav_error`/`timeout`-detail; `content_unavailable` → set `lastError`, `phase='error'`, `recoveryExhausted=true`, keep `navigation`), and the landing evaluation (`phase==='navigating'` → `evaluateLanding`; `landed` → `phase='landed'`, set `acceptedUrl`; `terminal_skip` → `skip`, `phase='error'`, `navigation=null`; `error_retry` → `funnelDecision`; `fail` → `fail`, `phase='error'`, `navigation=null`; `retry_navigate` → clear signals, `attempt+1`, persist, `updateTabUrl`).
  - `getNavScope()` returns `{ boundTabId: state?.boundTabId ?? null }` lock-free.
  - The six `deliver*` handlers each acquire the mutex, load state, apply the amend §2.1–§2.2 correlation rules, then re-evaluate landing (for committed/http/pageReady) and act. They are safe when gate is OFF (no-op) and when the event does not correlate (discard).

- [ ] **Step 1: Add claimNextJob tests to api.test.mjs**

Append to `browserext/tests/api.test.mjs`:
```js
test('claimNextJob GETs /jobs/next; 204 → null; 200 → FetchJob', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fullJob = {
      id: 'abc', url: 'https://x.edu.cn/p', status: 'assigned',
      context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
      created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
    };
    let count = 0;
    const fetchFn = async () => {
      count += 1;
      if (count === 1) return { status: 204, ok: true, json: async () => null };
      return { status: 200, ok: true, json: async () => fullJob };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    assert.equal(await api.claimNextJob(), null);
    const j = await api.claimNextJob();
    assert.equal(j.id, 'abc');
  } finally {
    await cleanup();
  }
});

test('claimNextJob returns null on fetch error', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNREFUSED'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    assert.equal(await api.claimNextJob(), null);
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run api test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='claimNextJob' 2>&1 | tail -10`
Expected: FAIL — `api.claimNextJob is not a function`.

- [ ] **Step 3: Add claimNextJob to api.ts**

Edit `browserext/src/api.ts`:

1. Add `claimNextJob` to the `ApiClient` interface:
```ts
export interface ApiClient {
  getStatus(): Promise<StatusPayload | null>;
  claimNextJob(): Promise<FetchJob | null>;
  failJob(jobId: string, message: string): Promise<void>;
  skipJob(jobId: string, reason: string): Promise<void>;
  sendHeartbeat(payload: HeartbeatPayload): Promise<void>;
}
```

2. Add the impl inside `createFetchApi` (after `getStatus`, before `failJob`):
```ts
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
```

3. Update the final `return`:
```ts
  return { getStatus, claimNextJob, failJob, skipJob, sendHeartbeat };
```

Run `cd browserext && npm test -- --test-name-pattern='claimNextJob' 2>&1 | tail -8` → PASS.

- [ ] **Step 4: Write the failing controller tests (extend controller.test.mjs)**

Append to `browserext/tests/controller/controller.test.mjs` (after the slice-2 block):
```js
// ---- slice 3: claim + navigate + landing + funnel + deadline + deliver/join ----

function fakeApi3({ statusResponse, nextJob }) {
  const calls = { getStatus: 0, claimNextJob: 0, fail: [], skip: [], sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async claimNextJob() {
      calls.claimNextJob += 1;
      if (typeof nextJob === 'function') return nextJob();
      return nextJob ?? null;
    },
    async failJob(id, msg) { calls.fail.push({ id, msg }); },
    async skipJob(id, reason) { calls.skip.push({ id, reason }); },
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}

function fakeChrome3({ tab }) {
  const updates = [];
  return {
    calls: { updates },
    async getTab() { return tab; },
    async updateTabUrl(t, url) { updates.push({ tabId: t, url }); },
  };
}

function fullJob(id, url = `https://xjtu.edu.cn/${id}`) {
  return {
    id, url, status: 'assigned',
    context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
    created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
  };
}

test('claim: idle+bound+auto → claimNextJob → navigate; persists navigation BEFORE updateTabUrl', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: fullJob('job-1') });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);   // idle → claim → assigned → navigate
    const s = await c.getState();
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'navigating');
    assert.ok(s.navigation, 'navigation persisted BEFORE updateTabUrl');
    assert.equal(s.navigation.attempt, 1);
    assert.equal(s.navigation.kind, 'navigate');
    assert.equal(s.navigation.requestedUrl, 'https://xjtu.edu.cn/job-1');
    assert.deepEqual(chr.calls.updates, [{ tabId: 42, url: 'https://xjtu.edu.cn/job-1' }]);
  } finally { await cleanup(); }
});

test('claim: 204 (no job) → stays idle, no navigate', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    const s = await c.getState();
    assert.equal(s.phase, 'idle');
    assert.equal(s.currentJob, null);
    assert.equal(chr.calls.updates.length, 0);
  } finally { await cleanup(); }
});

test('claim does NOT fire when !autoMode (defaults false)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: fullJob('job-1') });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);   // autoMode defaults false
    await c.tick(5000);
    assert.equal(api.calls.claimNextJob, 0);
    assert.equal(chr.calls.updates.length, 0);
  } finally { await cleanup(); }
});

test('deliverCommitted+Http+PageReady (same requestId/documentId, ok) → landed', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: fullJob('job-1') });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    assert.equal(c.getNavScope().boundTabId, 42);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const s = await c.getState();
    assert.equal(s.phase, 'landed');
    assert.equal(s.navigation.acceptedUrl, 'https://xjtu.edu.cn/job-1');
  } finally { await cleanup(); }
});

test('deliverHttpEvent 429 → immediate fail rate_limited (amend §8.1 #12)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 429, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'rate_limited');
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'rate_limited' });
    assert.equal(s.navigation, null);
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'rate_limited' }]);
    assert.equal(chr.calls.updates.length, 1, 'no re-navigate on 429 (only the initial navigate)');
  } finally { await cleanup(); }
});

test('deliverHttpEvent 502 attempt 1 → retry_navigate (clears signals, attempt 2, updateTabUrl again)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'gateway');
    const s = await c.getState();
    assert.equal(s.phase, 'navigating');
    assert.equal(s.navigation.attempt, 2);
    assert.equal(s.navigation.requestId, undefined, 'requestId cleared on retry');
    assert.equal(s.navigation.commit, undefined, 'commit cleared on retry');
    assert.equal(chr.calls.updates.length, 2, 're-navigated');
  } finally { await cleanup(); }
});

test('deliverHttpEvent 502 attempt 3 → fail gateway_5xx (signal-clear + budget)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'gateway');  // attempt 1 → retry 2
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-2', timeStamp: 6100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-2', timeStamp: 6200 }, 'gateway');  // attempt 2 → retry 3
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-3', timeStamp: 7100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-3', timeStamp: 7200 }, 'gateway');  // attempt 3 → fail
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'gateway_5xx' });
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'gateway_5xx' }]);
  } finally { await cleanup(); }
});

test('deliverHttpEvent 404 → immediate skip not_found (no budget)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 404, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'not_found');
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError, null, 'skip does not set a fail error');
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'not_found' }]);
  } finally { await cleanup(); }
});

test('deliverBeforeRedirect offsite → terminal skip offsite_redirect (amend §8.1 #14)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverBeforeRedirect({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', redirectUrl: 'https://attacker.edu.cn/x', requestId: 'REQ-1', timeStamp: 5200 });
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'offsite_redirect' }]);
  } finally { await cleanup(); }
});

test('deliverBeforeRedirect onsite → no skip; keep waiting for onCompleted (amend §8.1 #14)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverBeforeRedirect({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', redirectUrl: 'https://xjtu.edu.cn/renxueguang', requestId: 'REQ-1', timeStamp: 5200 });
    const s = await c.getState();
    assert.equal(s.phase, 'navigating', 'onsite redirect keeps waiting');
    assert.deepEqual(api.calls.skip, []);
  } finally { await cleanup(); }
});

test('deliverHttpEvent with documentId !== commit.documentId → discarded (amend §8.1 #7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-OTHER', timeStamp: 5300 }, 'ok');
    const s = await c.getState();
    assert.equal(s.navigation.http, undefined, 'http with mismatched documentId discarded');
    assert.equal(s.phase, 'navigating', 'still waiting');
  } finally { await cleanup(); }
});

test('stale requestId from previous attempt does NOT pollute current attempt (amend §8.1 #6)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'gateway');  // attempt 1 fail → retry
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-2', timeStamp: 6100 });
    // a LATE 502 from the OLD requestId REQ-1 arrives → must be discarded
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 6200 }, 'gateway');
    const s = await c.getState();
    assert.equal(s.navigation.requestId, 'REQ-2', 'current attempt keeps REQ-2');
    assert.equal(s.navigation.attempt, 2, 'not bumped to 3 by the stale event');
    assert.equal(s.navigation.http, undefined, 'stale http not written');
  } finally { await cleanup(); }
});

test('deliverError ERR_CONNECTION_REFUSED attempt 3 → fail nav_error:ERR_…', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    for (const [req, t] of [['REQ-1',5100],['REQ-2',6100],['REQ-3',7100]]) {
      await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: req, timeStamp: t });
      await c.deliverError({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', error: 'ERR_CONNECTION_REFUSED', frameId: 0, requestId: req, timeStamp: t + 100 }, 'nav_error');
    }
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'nav_error', error: 'ERR_CONNECTION_REFUSED' });
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'nav_error:ERR_CONNECTION_REFUSED' }]);
  } finally { await cleanup(); }
});

test('navigation deadline (never landed, 30s no commit) → funnel retry then fail nav_error:timeout at attempt 3', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);   // issuedAt=5000, attempt 1
    await c.tick(36000);   // 5000+30000=35000; 36000>=35000 expired, no commit → never_landed → retry attempt 2
    let s = await c.getState();
    assert.equal(s.navigation.attempt, 2);
    assert.equal(chr.calls.updates.length, 2);
    await c.tick(66000);   // attempt 2 expired → attempt 3
    s = await c.getState();
    assert.equal(s.navigation.attempt, 3);
    await c.tick(96000);   // attempt 3 expired → fail nav_error:timeout
    s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'nav_error', error: 'timeout' });
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'nav_error:timeout' }]);
  } finally { await cleanup(); }
});

test('landing deadline (commit present, no PAGE_READY, 30s) → content_unavailable/page_ready, NO refresh', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });   // committedAt=5200
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.tick(36000);   // 5200+30000=35200; 36000>=35200 expired, no PAGE_READY → content_unavailable/page_ready
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError.kind, 'content_unavailable');
    assert.equal(s.lastError.missing, 'page_ready');
    assert.equal(s.lastError.sourceDocumentId, 'DOC-1');
    assert.equal(s.lastError.recoveryExhausted, true);
    assert.equal(chr.calls.updates.length, 1, 'NEVER refreshed on content_unavailable');
  } finally { await cleanup(); }
});

test('deliver* ignores events from a non-bound tab (scope guard)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 999, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-X', timeStamp: 5100 });
    const s = await c.getState();
    assert.equal(s.navigation.requestId, undefined, 'non-bound-tab event discarded');
  } finally { await cleanup(); }
});

test('deliver* does not throw on an unbound controller (no navigation to correlate)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: null });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await assert.doesNotReject(async () => {
      await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'u', timeStamp: 5000 });
      await c.deliverHttpEvent({ tabId: 42, url: 'u', statusCode: 200, frameId: 0, requestId: 'R', timeStamp: 5000 }, 'ok');
      await c.deliverPageReady({ documentId: 'DOC-1', url: 'u', detection: { errorPage: false, terminalReason: null }, timeStamp: 5000 });
    });
    assert.deepEqual(api.calls.fail, []);
    assert.deepEqual(api.calls.skip, []);
  } finally { await cleanup(); }
});
```

- [ ] **Step 5: Run controller tests to verify they fail**

Run: `cd browserext && npm test -- --test-name-pattern='claim: idle|claim: 204|claim does NOT|deliverCommitted\+Http|deliverHttpEvent 429|deliverHttpEvent 502 attempt 1|deliverHttpEvent 502 attempt 3|deliverHttpEvent 404|deliverBeforeRedirect offsite|deliverBeforeRedirect onsite|documentId !== commit|stale requestId|deliverError ERR_CONNECTION_REFUSED|navigation deadline|landing deadline|non-bound tab|does not throw' 2>&1 | tail -30`
Expected: FAIL — `c.deliverBeforeRequest`/`c.getNavScope`/`c.claimNextJob` do not exist.

- [ ] **Step 6: Modify controller.ts — implement claim + navigate + deliver* + deadlines**

Replace `browserext/src/controller/controller.ts` with:
```ts
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
    if (e.kind === 'nav_error') return `nav_error:${e.error}`;
    if (e.kind === 'gateway_5xx') return 'gateway_5xx';
    if (e.kind === 'unexpected_status') return `unexpected_status:${e.statusCode}`;
    return 'rate_limited';
  }

  /** Act on a landing verdict (called under the lock after each signal). */
  async function applyLandingVerdict(s: ControllerState, now: number): Promise<void> {
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
    const nav = s.navigation!;
    const decision = funnelDecision(verdict.outcome, nav.attempt, verdict.detail);
    if (decision.kind === 'retry_navigate') {
      clearAttemptSignals(nav);
      nav.attempt += 1;
      nav.issuedAt = now;
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
```

- [ ] **Step 7: Run controller + api tests to verify they pass**

Run: `cd browserext && npm test -- --test-name-pattern='claimNextJob|claim: idle|claim: 204|claim does NOT|deliverCommitted|deliverHttpEvent|deliverBeforeRedirect|deliverError|navigation deadline|landing deadline|non-bound tab|does not throw' 2>&1 | tail -20`
Expected: PASS — all new controller + api tests green.

- [ ] **Step 8: Typecheck + full suite green (Phase-1 status.ts/navMonitor.ts UNTOUCHED — still green)**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -8`
Expected: typecheck exit 0; full suite green (`fail 0`). The Phase-1 `status.ts` (3) and `navMonitor.ts` (10) tests are UNCHANGED and still pass — the Controller does not depend on them. Count math: 101 + 2 api + ~16 controller slice-3 = ~119; exact number not load-bearing, `fail 0` is.

- [ ] **Step 9: Commit**

```bash
git add browserext/src/api.ts browserext/src/controller/controller.ts browserext/tests/controller/controller.test.mjs browserext/tests/api.test.mjs
git commit -m "feat(browserext): slice 3 — claim + persist-before-navigate + landing + funnel + deadline + deliver/join"
```

---

## Task 7: Rewrite status.ts + chrome.ts + navMonitor.ts + background.ts; delete storage.ts

This is the single coupled rewrite: the `status.ts` classifier rewrite and `chrome.ts` event-widening each break the Phase-1 `navMonitor.ts` tests (which are rewritten in the same commit), so they land together. After this commit the full suite is green.

**Files:**
- Modify: `browserext/src/status.ts`
- Rewrite: `browserext/tests/status.test.mjs`
- Modify: `browserext/src/chrome.ts`
- Modify: `browserext/tests/chrome.test.mjs`
- Rewrite: `browserext/src/navMonitor.ts`
- Rewrite: `browserext/tests/navMonitor.test.mjs`
- Modify: `browserext/src/background.ts`
- Modify: `browserext/tests/background.test.mjs`
- Delete: `browserext/src/storage.ts`
- Delete: `browserext/tests/storage.test.mjs`

**Interfaces:**
- `status.ts`: amend §3.1 classifier (drop dup `NavOutcome`; import from `shared/state.js`; `NavInput` from `shared/navEvents.js`). `500→gateway`, `204/205/401/403/301/302→unexpected_status`. Keeps `DEAD_STATUSES`/`RATE_LIMITED_STATUS`/`GATEWAY_STATUSES` (now includes 500).
- `chrome.ts`: widen `NavCompletedEvent`/`NavErrorEvent` (`requestId`/`documentId?`/`timeStamp`); add `onBeforeRequest`/`onBeforeRedirect`/`onCommitted`/`onHistoryStateUpdated` (main-frame only). Real impl binds the real `chrome.webNavigation.*`/`chrome.webRequest.*` listeners.
- `navMonitor.ts`: thin adapter — `NavMonitorDeps = { chrome, controller }`. `start()` registers the six listeners; each filters main-frame + bound tab (`controller.getNavScope()`) then forwards the raw event (+ outcome for completed/error) to the Controller. NO `api`/`storage`/`updateTabUrl`; never fails/skips/counts/navigates. `onBeforeRedirect` forwarded raw (no classifier).
- `background.ts`: `WireDeps` drops `storage`; navMonitor constructed with `{ chrome, controller }`.
- `storage.ts` (Phase-1): DELETED (sole consumer rewritten); `controller/storage.ts` survives.

- [ ] **Step 1: Rewrite the status test (amend §8.1 #16)**

Replace `browserext/tests/status.test.mjs` with:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// amend §8.1 #16: 200/304 → ok; 500 → gateway; 401/403/204/205 → unexpected_status;
// 301/302 (defensive — never seen in production because onBeforeRedirect is raw)
// → unexpected_status.

test('classifyNavigation maps status codes per amend §3.1', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 200 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 201 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 304 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 404 }), 'not_found');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 410 }), 'not_found');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 429 }), 'rate_limited');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 500 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 502 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 503 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 504 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 204 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 205 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 401 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 403 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 301 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 302 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 400 }), 'unexpected_status');
  } finally {
    await cleanup();
  }
});

test('classifyNavigation maps network errors to nav_error', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.equal(mod.classifyNavigation({ kind: 'error', error: 'ERR_CONNECTION_REFUSED' }), 'nav_error');
    assert.equal(mod.classifyNavigation({ kind: 'error', error: 'ERR_NAME_NOT_RESOLVED' }), 'nav_error');
    assert.equal(mod.classifyNavigation({ kind: 'error', error: 'ERR_TIMED_OUT' }), 'nav_error');
  } finally {
    await cleanup();
  }
});

test('DEAD/RATE_LIMITED/GATEWAY sets exported; GATEWAY now includes 500', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.ok(mod.DEAD_STATUSES.has(404));
    assert.ok(mod.DEAD_STATUSES.has(410));
    assert.equal(mod.RATE_LIMITED_STATUS, 429);
    assert.ok(mod.GATEWAY_STATUSES.has(500));
    assert.ok(mod.GATEWAY_STATUSES.has(502));
    assert.ok(mod.GATEWAY_STATUSES.has(503));
    assert.ok(mod.GATEWAY_STATUSES.has(504));
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Modify status.ts**

Replace `browserext/src/status.ts` with:
```ts
/** Pure navigation classifier — the ONLY place status-code → outcome mapping
 *  lives (spec §1.4, amend §3.1). No chrome, no IO. The canonical NavOutcome
 *  (6 values incl. unexpected_status) lives in shared/state.ts; this module
 *  imports it rather than redefining it.
 *
 *  Algorithm (amend §3.1):
 *    error kind                     → 'nav_error'
 *    2xx (except 204/205) or 304     → 'ok'
 *    404/410                         → 'not_found'
 *    429                             → 'rate_limited'
 *    500/502/503/504                 → 'gateway'
 *    anything else                   → 'unexpected_status'   (incl. 204/205/401/403; 301/302 defensively)
 *
 *  onBeforeRedirect is NEVER classified (amend §3.1): the Controller runs the
 *  same-crawl-site gate on redirectUrl directly, so 3xx never reaches here in
 *  production. The 301/302 → unexpected_status mapping is the defensive
 *  behavior if a 3xx somehow arrives via onCompleted. */

import type { NavOutcome } from './shared/state.js';
import type { NavInput } from './shared/navEvents.js';

export type { NavOutcome, NavInput };

export const DEAD_STATUSES = new Set([404, 410]);
export const RATE_LIMITED_STATUS = 429;
export const GATEWAY_STATUSES = new Set([500, 502, 503, 504]);

export function classifyNavigation(input: NavInput): NavOutcome {
  if (input.kind === 'error') return 'nav_error';
  const code = input.statusCode;
  if ((code >= 200 && code < 300 && code !== 204 && code !== 205) || code === 304) {
    return 'ok';
  }
  if (DEAD_STATUSES.has(code)) return 'not_found';
  if (code === RATE_LIMITED_STATUS) return 'rate_limited';
  if (GATEWAY_STATUSES.has(code)) return 'gateway';
  return 'unexpected_status';
}
```

- [ ] **Step 3: Modify chrome.ts — widen events + add listeners**

Edit `browserext/src/chrome.ts`:

1. Add the type-only import of event types at the top (after the file doc comment):
```ts
import type {
  BeforeRequestEvent,
  BeforeRedirectEvent,
  CommittedEvent,
} from './shared/navEvents.js';
```

2. Widen `NavCompletedEvent`/`NavErrorEvent` and add the new methods to the `ChromeRuntime` interface (replace the existing two interfaces and the `ChromeRuntime` interface):
```ts
export interface NavCompletedEvent {
  tabId: number;
  url: string;
  statusCode: number;
  frameId: number;
  requestId: string;
  documentId?: string;
  timeStamp: number;
}

export interface NavErrorEvent {
  tabId: number;
  url: string;
  error: string;
  frameId: number;
  requestId: string;
  timeStamp: number;
}

export interface ChromeRuntime {
  onNavCompleted(cb: (e: NavCompletedEvent) => void): void;
  onNavError(cb: (e: NavErrorEvent) => void): void;
  onBeforeRequest(cb: (e: BeforeRequestEvent) => void): void;
  onBeforeRedirect(cb: (e: BeforeRedirectEvent) => void): void;
  onCommitted(cb: (e: CommittedEvent) => void): void;
  onHistoryStateUpdated(cb: (e: CommittedEvent) => void): void;
  updateTabUrl(tabId: number, url: string): Promise<void>;
  findOwnerTab(): Promise<number | null>;
  getTab(tabId: number): Promise<{ id: number; url?: string } | null>;
  registerAlarm(name: string, periodMinutes: number, cb: () => void): void;
}
```

3. In `createRealChromeRuntime`, widen the existing `onNavCompleted`/`onNavError` to pass `requestId`/`documentId`/`timeStamp`, and add the four new listener methods (after `onNavError`, before `updateTabUrl`). Replace the existing `onNavCompleted`/`onNavError` blocks and insert the new ones:
```ts
    onNavCompleted(cb) {
      chrome.webRequest.onCompleted.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({
            tabId: details.tabId, url: details.url, statusCode: details.statusCode,
            frameId: details.frameId, requestId: details.requestId,
            documentId: (details as chrome.webRequest.WebResponseDetails & { documentId?: string }).documentId,
            timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onNavError(cb) {
      chrome.webRequest.onErrorOccurred.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({
            tabId: details.tabId, url: details.url, error: details.error,
            frameId: details.frameId, requestId: details.requestId, timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onBeforeRequest(cb) {
      chrome.webRequest.onBeforeRequest.addListener(
        (details) => {
          if (details.type !== 'main_frame') return;
          cb({
            tabId: details.tabId, frameId: details.frameId, type: details.type,
            url: details.url, requestId: details.requestId, timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onBeforeRedirect(cb) {
      chrome.webRequest.onBeforeRedirect.addListener(
        (details) => {
          if (details.type !== 'main_frame') return;
          cb({
            tabId: details.tabId, frameId: details.frameId, type: details.type,
            url: details.url, redirectUrl: details.redirectUrl, requestId: details.requestId,
            timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onCommitted(cb) {
      chrome.webNavigation.onCommitted.addListener((details) => {
        if (details.frameId !== 0) return;
        cb({
          tabId: details.tabId, frameId: details.frameId, documentId: details.documentId ?? '',
          url: details.url, timeStamp: details.timeStamp,
        });
      });
    },
    onHistoryStateUpdated(cb) {
      chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
        if (details.frameId !== 0) return;
        cb({
          tabId: details.tabId, frameId: details.frameId, documentId: details.documentId ?? '',
          url: details.url, timeStamp: details.timeStamp,
        });
      });
    },
```

> `chrome.webRequest.WebResponseDetails` does not carry `documentId` (it is on `webNavigation` details); the cast is a safe optional read — if absent, `documentId` is `undefined`, which the join logic treats as "no documentId on the event → join satisfied by requestId alone" (amend §2.2). Some Chrome versions DO populate `documentId` on `onCompleted`; reading it defensively is correct.

- [ ] **Step 4: Update chrome.test.mjs — widen the fake + add a real-export assertion**

Edit `browserext/tests/chrome.test.mjs` — replace `fakeRuntime()` and add the new test:
```js
function fakeRuntime() {
  const completedCbs = [], errorCbs = [], beforeReqCbs = [], beforeRedirectCbs = [], committedCbs = [], historyCbs = [];
  let alarmCb = null;
  let lastUpdated = null;
  let tabs = [{ id: 1, url: 'https://www.x.edu.cn/faculty' }];
  return {
    onNavCompleted(cb) { completedCbs.push(cb); },
    onNavError(cb) { errorCbs.push(cb); },
    onBeforeRequest(cb) { beforeReqCbs.push(cb); },
    onBeforeRedirect(cb) { beforeRedirectCbs.push(cb); },
    onCommitted(cb) { committedCbs.push(cb); },
    onHistoryStateUpdated(cb) { historyCbs.push(cb); },
    async updateTabUrl(tabId, url) { lastUpdated = { tabId, url }; },
    async findOwnerTab() {
      const t = tabs.find((t) => /edu\.cn$|github\.io$/.test(new URL(t.url).hostname)) ?? tabs[0] ?? null;
      return t ? t.id : null;
    },
    async getTab(tabId) { const t = tabs.find((t) => t.id === tabId) ?? null; return t ? { id: t.id, url: t.url } : null; },
    registerAlarm(_name, _period, cb) { alarmCb = cb; },
    // test helpers
    fireCompleted(e) { for (const cb of completedCbs) cb(e); },
    fireError(e) { for (const cb of errorCbs) cb(e); },
    fireBeforeRequest(e) { for (const cb of beforeReqCbs) cb(e); },
    fireBeforeRedirect(e) { for (const cb of beforeRedirectCbs) cb(e); },
    fireCommitted(e) { for (const cb of committedCbs) cb(e); },
    fireHistory(e) { for (const cb of historyCbs) cb(e); },
    fireAlarm() { if (alarmCb) alarmCb(); },
    lastUpdated,
    _tabs: tabs,
  };
}

test('createRealChromeRuntime exposes nav listeners + widened events (slice 3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    const rt = mod.createRealChromeRuntime();
    for (const m of ['onBeforeRequest','onBeforeRedirect','onCommitted','onHistoryStateUpdated','onNavCompleted','onNavError','updateTabUrl','findOwnerTab','getTab','registerAlarm']) {
      assert.equal(typeof rt[m], 'function', `${m} is a function`);
    }
  } finally {
    await cleanup();
  }
});
```
(Keep the existing `'ChromeRuntime interface is satisfiable by a fake'` and `'createRealChromeRuntime exposes registerAlarm + getTab'` tests.)

- [ ] **Step 5: Rewrite navMonitor.ts (thin adapter)**

Replace `browserext/src/navMonitor.ts` with:
```ts
/** Thin main-frame navigation-event adapter (amend §3.2). It ONLY:
 *    - registers the six webRequest/webNavigation listeners;
 *    - scope-filters main-frame + bound tab (lock-free via controller.getNavScope());
 *    - forwards the RAW event (+ outcome for the final onCompleted/onErrorOccurred) to the Controller.
 *  It does NOT hold api/storage, does NOT call fail/skip, does NOT accumulate gateway
 *  counts, does NOT call tabs.update. onBeforeRedirect is forwarded RAW — it never calls
 *  the classifier (amend §3.1); the Controller runs the same-crawl-site gate on redirectUrl.
 *  The Controller (NavController) owns all requestId/documentId/job/time/phase correlation
 *  under its mutex (amend §2.1–§2.2) and the retry funnel (amend §3.2). */

import type { ChromeRuntime } from './chrome.js';
import { classifyNavigation } from './status.js';
import { isMainFrame } from './shared/navEvents.js';
import type { NavController } from './shared/navEvents.js';

export interface NavMonitorDeps {
  chrome: Pick<
    ChromeRuntime,
    'onBeforeRequest' | 'onBeforeRedirect' | 'onCommitted' | 'onHistoryStateUpdated' | 'onNavCompleted' | 'onNavError'
  >;
  controller: NavController;
}

export interface NavMonitor {
  start(): void;
}

export function createNavMonitor(deps: NavMonitorDeps): NavMonitor {
  const { chrome, controller } = deps;

  function boundTabMatches(tabId: number): boolean {
    return controller.getNavScope().boundTabId === tabId;
  }

  return {
    start() {
      chrome.onBeforeRequest((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverBeforeRequest(e);
      });
      chrome.onBeforeRedirect((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverBeforeRedirect(e);    // RAW — no classifier (amend §3.1)
      });
      chrome.onCommitted((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverCommitted(e);
      });
      chrome.onHistoryStateUpdated((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverCommitted(e);          // SPA same-document → treat as commit (slice 4 refines)
      });
      chrome.onNavCompleted((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        const outcome = classifyNavigation({ kind: 'completed', statusCode: e.statusCode });
        void controller.deliverHttpEvent(e, outcome);
      });
      chrome.onNavError((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        const outcome = classifyNavigation({ kind: 'error', error: e.error });
        void controller.deliverError(e, outcome);
      });
    },
  };
}
```

- [ ] **Step 6: Rewrite navMonitor.test.mjs (amend §8.1 #17 + #14)**

Replace `browserext/tests/navMonitor.test.mjs` with:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// A NavController fake that records every delivery (amend §8.1 #17: navMonitor
// forwards raw event + outcome for all 6 outcomes; it never decides/acts itself).
function fakeController(boundTabId = 1) {
  const calls = { beforeRequest: [], beforeRedirect: [], committed: [], http: [], error: [], pageReady: [], scopeReads: 0 };
  return {
    calls,
    getNavScope() { calls.scopeReads += 1; return { boundTabId }; },
    async deliverBeforeRequest(e) { calls.beforeRequest.push(e); },
    async deliverBeforeRedirect(e) { calls.beforeRedirect.push(e); },
    async deliverCommitted(e) { calls.committed.push(e); },
    async deliverHttpEvent(e, o) { calls.http.push({ e, o }); },
    async deliverError(e, o) { calls.error.push({ e, o }); },
    async deliverPageReady(e) { calls.pageReady.push(e); },
  };
}

function fakeChrome() {
  const cbs = { beforeRequest: [], beforeRedirect: [], committed: [], history: [], completed: [], error: [] };
  return {
    onBeforeRequest(cb) { cbs.beforeRequest.push(cb); },
    onBeforeRedirect(cb) { cbs.beforeRedirect.push(cb); },
    onCommitted(cb) { cbs.committed.push(cb); },
    onHistoryStateUpdated(cb) { cbs.history.push(cb); },
    onNavCompleted(cb) { cbs.completed.push(cb); },
    onNavError(cb) { cbs.error.push(cb); },
    fire(kind, e) { for (const cb of cbs[kind]) cb(e); },
  };
}

test('navMonitor accepts only {chrome, controller} deps (thin adapter; no api/storage)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const nm = mod.createNavMonitor({ chrome: fakeChrome(), controller: fakeController() });
    nm.start();
    assert.equal(typeof nm.start, 'function');
  } finally {
    await cleanup();
  }
});

test('onNavCompleted forwards raw event + classifyNavigation outcome for every NavOutcome (amend §8.1 #17)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    const cases = [
      [200, 'ok'], [304, 'ok'], [404, 'not_found'], [410, 'not_found'], [429, 'rate_limited'],
      [500, 'gateway'], [502, 'gateway'], [503, 'gateway'], [504, 'gateway'],
      [204, 'unexpected_status'], [401, 'unexpected_status'], [403, 'unexpected_status'], [301, 'unexpected_status'],
    ];
    for (const [code, expectedOutcome] of cases) {
      chr.fire('completed', { tabId: 1, url: 'https://x.edu.cn/p', statusCode: code, frameId: 0, requestId: 'R', documentId: 'D', timeStamp: 100 });
      assert.equal(ctrl.calls.http.at(-1).o, expectedOutcome, `${code} → ${expectedOutcome}`);
      assert.equal(ctrl.calls.http.at(-1).e.statusCode, code);
      assert.equal(ctrl.calls.http.at(-1).e.requestId, 'R');
    }
    assert.equal(ctrl.calls.http.length, cases.length);
  } finally {
    await cleanup();
  }
});

test('onNavError forwards raw event + nav_error outcome', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('error', { tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0, requestId: 'R', timeStamp: 100 });
    assert.equal(ctrl.calls.error.length, 1);
    assert.equal(ctrl.calls.error[0].o, 'nav_error');
    assert.equal(ctrl.calls.error[0].e.error, 'ERR_CONNECTION_REFUSED');
  } finally {
    await cleanup();
  }
});

test('onBeforeRedirect forwards raw, NO classifier call (amend §8.1 #14)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('beforeRedirect', { tabId: 1, frameId: 0, type: 'main_frame', url: 'https://x.edu.cn/p', redirectUrl: 'https://attacker.edu.cn/x', requestId: 'R', timeStamp: 100 });
    assert.equal(ctrl.calls.beforeRedirect.length, 1);
    assert.equal(ctrl.calls.beforeRedirect[0].redirectUrl, 'https://attacker.edu.cn/x');
    assert.equal(ctrl.calls.http.length, 0, 'onBeforeRedirect must not call the classifier / not deliver an http outcome');
  } finally {
    await cleanup();
  }
});

test('scope filter: sub-frame (frameId !== 0) events are dropped', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('completed', { tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 2, requestId: 'R', timeStamp: 100 });
    chr.fire('committed', { tabId: 1, frameId: 2, documentId: 'D', url: 'u', timeStamp: 100 });
    assert.equal(ctrl.calls.http.length, 0);
    assert.equal(ctrl.calls.committed.length, 0);
  } finally {
    await cleanup();
  }
});

test('scope filter: non-bound-tab events are dropped', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController(1);
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('completed', { tabId: 999, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0, requestId: 'R', timeStamp: 100 });
    assert.equal(ctrl.calls.http.length, 0, 'non-bound-tab event dropped');
  } finally {
    await cleanup();
  }
});

test('beforeRequest + committed forwarded raw', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('beforeRequest', { tabId: 1, frameId: 0, type: 'main_frame', url: 'https://x.edu.cn/p', requestId: 'R', timeStamp: 100 });
    chr.fire('committed', { tabId: 1, frameId: 0, documentId: 'D', url: 'https://x.edu.cn/p', timeStamp: 110 });
    assert.equal(ctrl.calls.beforeRequest[0].requestId, 'R');
    assert.equal(ctrl.calls.committed[0].documentId, 'D');
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 7: Update background.ts (drop Storage; navMonitor gets {chrome, controller})**

Replace `browserext/src/background.ts` with:
```ts
/** MV3 background service worker entry. Composition root only — no logic.
 *  Constructs the real chrome.* runtime, the fetch HTTP client, the
 *  chrome.storage ControllerState area, then wires the gated CrawlController
 *  and the thin navMonitor adapter (slice 3: navMonitor forwards raw main-frame
 *  events + outcome to the controller; it no longer holds api/storage and never
 *  fails/skips/navigates on its own). The 1-minute reconciliation alarm drives
 *  controller.tick(). Gate OFF in official slice 1–5 builds → all of this is a
 *  runtime no-op. */

import { createRealChromeRuntime } from './chrome.js';
import type { ChromeRuntime } from './chrome.js';
import { createFetchApi } from './api.js';
import type { ApiClient } from './api.js';
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
}

export function wireBackground(deps: WireDeps): {
  navMonitor: NavMonitor;
  controller: CrawlController;
} {
  const controller = createCrawlController({
    storage: createControllerStorage(chrome.storage.local as unknown as StorageArea),
    api: deps.api,
    chrome: deps.chrome,
  });
  const navMonitor = createNavMonitor({ chrome: deps.chrome, controller });
  navMonitor.start();
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
  });
}
```

- [ ] **Step 8: Update background.test.mjs**

Replace `browserext/tests/background.test.mjs` with:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('wireBackground constructs navMonitor {chrome, controller} + controller, starts navMonitor, wires reconciliation alarm → controller.tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
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
      onBeforeRequest() {}, onBeforeRedirect() {}, onCommitted() {}, onHistoryStateUpdated() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; },
      async getTab() { return null; },
      registerAlarm(name, period, cb) { alarmReg = { name, period, cb }; },
    };
    const fakeApi = { async getStatus() { return null; }, async claimNextJob() { return null; }, async failJob() {}, async skipJob() {}, async sendHeartbeat() {} };
    const { navMonitor, controller } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi });
    assert.ok(navMonitor, 'navMonitor constructed');
    assert.ok(controller, 'controller constructed');
    assert.equal(typeof controller.tick, 'function');
    assert.equal(typeof controller.deliverCommitted, 'function');
    assert.equal(typeof controller.getNavScope, 'function');
    assert.ok(alarmReg, 'reconciliation alarm registered');
    assert.equal(alarmReg.name, 'dext-reconcile');
    assert.equal(alarmReg.period, 1);
    await assert.doesNotReject(async () => { await alarmReg.cb(); });
  } finally {
    if (prevLocal === undefined) delete globalThis.chrome.storage.local;
    else globalThis.chrome.storage.local = prevLocal;
    await cleanup();
  }
});
```

- [ ] **Step 9: Delete src/storage.ts + its test**

First confirm no other importer remains:
```bash
cd browserext && grep -rn "from './storage.js'\|from '../storage.js'\|createChromeStorage\b" src tests | grep -v "controller/storage.js"
```
Expected: no hits (the old `src/storage.ts` importers were `navMonitor.ts` and `background.ts`, both rewritten above). The `controller/storage.ts` module (`createControllerStorage`) is a DIFFERENT file and must NOT be deleted.

Then:
```bash
cd browserext && git rm src/storage.ts tests/storage.test.mjs
```
Expected: both files removed from the index and working tree.

- [ ] **Step 10: Run the full suite + typecheck (everything green)**

Run: `cd browserext && npm run typecheck && npm test 2>&1 | tail -12`
Expected: typecheck exit 0; ALL tests green, `fail 0`. The Phase-1 `storage` tests are gone (deleted); the rewritten `navMonitor`/`status` tests pass; the new `landing`/`funnel`/`deadline`/`urlGate`/`navEvents` + controller + api tests pass; the slice-1/2 tests pass. Exact count not load-bearing — `fail 0` is. If any test fails, STOP and fix before committing.

- [ ] **Step 11: Commit**

```bash
git add browserext/src/status.ts browserext/tests/status.test.mjs \
        browserext/src/chrome.ts browserext/tests/chrome.test.mjs \
        browserext/src/navMonitor.ts browserext/tests/navMonitor.test.mjs \
        browserext/src/background.ts browserext/tests/background.test.mjs
git commit -m "feat(browserext): thin navMonitor adapter + amend §3.1 classifier + widened chrome events; delete Phase-1 storage.ts"
```
(The `git rm` of `storage.ts`/`storage.test.mjs` from Step 9 is already staged; this commit captures it. Run `git status` to confirm the deletions are included.)

---

## Task 8: Slice-3 acceptance — full suite green, gate off, Phase-1 intact (except the two deliberate rewrites), Python unaffected

**Files:**
- No new files; verification gate.

- [ ] **Step 1: Clean build + full test suite + typecheck**

Run: `cd browserext && rm -rf dist && npm install && npm run typecheck && npm run build && npm test 2>&1 | tail -12`
Expected:
- `npm install` succeeds.
- typecheck exit 0 (both configs).
- build emits `dist/background.js` + `dist/content.js`.
- all tests pass, `fail 0`.

- [ ] **Step 2: Confirm the default build is still gated off (a true no-op)**

Run: `cd browserext && node -e "const fs=require('fs');const bg=fs.readFileSync('dist/background.js','utf8');console.log('if(true)return:', /if \(true\) return/.test(bg));console.log('if(false){:', /if \(false\) \{/.test(bg));const c=fs.readFileSync('dist/content.js','utf8');console.log('content if(false){:', /if \(false\) \{/.test(c));"`
Expected: `if(true)return: true` (controller early-returns when gate off), `if(false){: true` (controller tick/deliver bodies guarded), `content if(false){: true`. The reconciliation alarm fires `controller.tick()` → early-return; navMonitor's `start()` registers listeners but each `deliver*` early-returns on `!EXCLUSIVE_CONTROL_ENABLED` before any state mutation — a harmless no-op.

- [ ] **Step 3: Confirm Phase-1 regression status (the two deliberate rewrites are expected, not regression)**

Run: `cd browserext && npm test 2>&1 | grep -E "tests|pass|fail"`
Expected: `fail 0`. Specifically: `status` (rewritten, 3 tests), `navMonitor` (rewritten, 7 tests), `chrome` (widened, +1 test), `api` (+claimNextJob, +2 tests), `storage` DELETED (absent), `controller` (slice-1/2 + slice-3 all green), `landing`/`funnel`/`deadline`/`urlGate`/`navEvents` new suites green, `content`/`build`/`smoke` green.

- [ ] **Step 4: Confirm amend §8.1 must-pass items 6, 7, 12, 14, 16, 17 are covered**

Run: `cd browserext && npm test -- --test-name-pattern='stale requestId from previous attempt|documentId !== commit|immediate fail rate_limited|onBeforeRedirect forwards raw|maps status codes per amend|forwards raw event \+ classifyNavigation outcome' 2>&1 | tail -10`
Expected: all green — these pin items #6/#7/#12/#14/#16/#17.

- [ ] **Step 5: Confirm storage.ts is fully gone; no stale importers**

Run: `cd browserext && git ls-files | grep -E "src/storage\.ts|tests/storage\.test\.mjs" || echo "no Phase-1 storage files tracked"`
Expected: `no Phase-1 storage files tracked`.

Run: `cd browserext && grep -rn "createChromeStorage\|from './storage.js'\|from '../storage.js'" src tests | grep -v "controller/storage.js" || echo "clean"`
Expected: `clean` (no stale references to the deleted Phase-1 store; `controller/storage.js` is the surviving ControllerState persistence module).

- [ ] **Step 6: Confirm Python suite is unaffected**

Run: `uv run pytest -q` (from repo root)
Expected: PASS (slice 3 touches only `browserext/`; no Python changed). If any Python test fails, it is pre-existing and unrelated — note it but do not fix in this slice.

- [ ] **Step 7: Update memory with the slice-3 landing fact**

Update the `browserext-slice3-design` memory (or add a `browserext-slice3-landed` memory) recording: slice 3 landed (gated off) — `/jobs/next` claim + persist-before-navigate + landing 5-condition + 3-signal aggregation + retry funnel + two-level deadline + requestId/documentId join + rewritten status.ts classifier (unexpected_status, 500→gateway) + thin navMonitor adapter; Phase-1 `storage.ts` deleted; navMonitor no longer holds api/storage; no capture/formActions yet (slice 4); amend §8.1 items 6/7/12/14/16/17 pinned. This orients future sessions without re-deriving from git.

- [ ] **Step 8: Final commit (if any docs/memory artifacts were staged)**

```bash
# Only if README/manifest wording changed (it should not this slice):
git add browserext/README.md browserext/manifest.json 2>/dev/null
git commit -m "docs(browserext): note slice-3 claim/navigate/landing/funnel landed (gate off)" 2>/dev/null || echo "nothing to commit"
```

---

## Self-Review (completed by plan author)

**1. Spec coverage (slice-3 row of spec §6.2 + amend §2.1–§2.4, §3.1–§3.2, §8.2):**
- `/jobs/next` claim (assigned→navigating; `/status.current_job` still truth) → Task 6 Step 6 claim step (idle+bound+auto+!paused → claimNextJob; 204→idle) + `api.claimNextJob` (Task 6 Step 3). ✓ (spec §2.3 step 3)
- Navigation dispatch (sole tabs.update caller) + persist-before-navigate → Task 6 `navigateNow` (persists `navigation` THEN `updateTabUrl`). ✓ (spec §3.1)
- Landing 5-condition rule → Task 3 `evaluateLanding` + Task 6 `applyLandingVerdict`. ✓ (amend §2.3)
- 3-signal aggregation by documentId → Task 3 (commit/http/pageReady keyed by documentId; landing fires when all present + ok). ✓ (spec §3.3)
- Retry funnel (3-budget, signal-clear-on-retry, not_found→terminal skip, gateway/nav_error/unexpected_status→attempt-3 fail else re-navigate, rate_limited→immediate fail no refresh, offsite/wechat terminal skip no budget) → Task 4 `funnelDecision` + Task 6 retry path (`clearAttemptSignals`, `attempt+1`, re-navigate). ✓ (amend §3.2, spec §3.5)
- Two-level deadline (navigationDeadlineAt no-commit→funnel vs landingSignalsDeadlineAt post-commit→content_unavailable, never refresh) → Task 5 `navTimeoutKind` + Task 6 tick step 6. ✓ (amend §2.4)
- requestId binding rules (amend §2.1) → Task 6 `deliverBeforeRequest` (empty requestId, bound tab, main_frame, phase navigating|acting, job match, timeStamp>=issuedAt, urlMatches). ✓
- commit/HTTP/documentId join (amend §2.2) → Task 6 `deliverCommitted` (bound-tab+main-frame+job+time-window; NO requestId — onCommitted doesn't carry it) / `deliverHttpEvent` (same requestId; documentId join) / `deliverBeforeRedirect` (raw). ✓
- Rewrite status.ts classifier (amend §3.1) → Task 7 Step 2. ✓
- Rewrite navMonitor.ts as thin main-frame adapter (amend §3.2) → Task 7 Step 5. ✓
- capture/formActions NOT ported (slice 4) → Task 6 lands `phase='landed'` and stops (comment: slice 4 dispatches capture); no CS RPC, no fingerprint. ✓
- Gate stays OFF → Task 8 Step 2 verifies gate-off no-op; every new tick/deliver step is after `if (!EXCLUSIVE_CONTROL_ENABLED) return;`. ✓
- amend §8.1 items landing here: #6 (stale requestId) → Task 6 test `stale requestId from previous attempt` + deliver guards; #7 (onCompleted documentId≠commit drop) → Task 6 test `documentId !== commit` + `deliverHttpEvent` guard; #12 (429→Controller fail) → Task 6 test `immediate fail rate_limited`; #14 (onBeforeRedirect no classifier/offsite skip/onsite wait) → Task 7 test `onBeforeRedirect forwards raw` + Task 6 `deliverBeforeRedirect`; #16 (status.ts mapping) → Task 7 status test; #17 (navMonitor delivers outcome only) → Task 7 navMonitor test `forwards raw event + classifyNavigation outcome for every NavOutcome`. ✓ (all six pinned)

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Every code block contains complete code. Test-count math is hedged ("exact number not load-bearing — `fail 0` is"). The `deliverCommitted` handler correctly does NOT reference `e.requestId` (the `CommittedEvent` type has no such field — onCommitted doesn't carry requestId; a prose comment in the code explains why). The `details as ... & { documentId?: string }` cast in chrome.ts is a real defensive read with an explanation, not a placeholder.

**3. Type consistency:**
- `NavOutcome` canonical = `shared/state.ts` (6-value). `status.ts` re-exports it (Task 7); `navEvents.ts` `deliverHttpEvent(e, outcome: NavOutcome)` (Task 1); `landing.ts` `error_retry.outcome: Exclude<NavOutcome,'ok'>` (Task 3); `funnel.ts` `RetryOutcome = Exclude<NavOutcome,'ok'>` (Task 4); controller `deliverHttpEvent(e, outcome: NavOutcome)` (Task 6). All consistent. ✓
- `NavController` interface (Task 1) — implemented by `CrawlController extends NavController` (Task 6 `export interface CrawlController extends NavController`); consumed by `NavMonitorDeps.controller: NavController` (Task 7). The six method names match exactly. ✓
- Event types defined in Task 1; widened `NavCompletedEvent`/`NavErrorEvent` in `chrome.ts` (Task 7) carry the same `requestId`/`documentId?`/`timeStamp` — controller handlers (Task 6) read `e.requestId`/`e.documentId`/`e.timeStamp`/`e.statusCode`/`e.error`. `CommittedEvent` has NO `requestId`; `deliverCommitted` does not reference it. ✓
- `funnelDecision(outcome, attempt, detail?)` — Task 4 signature; Task 6 `applyLandingVerdict` calls `funnelDecision(verdict.outcome, nav.attempt, verdict.detail)` — `verdict.outcome` is `Exclude<NavOutcome,'ok'>` = `RetryOutcome`, matches. ✓
- `evaluateLanding(state, now)` — Task 3 signature; Task 6 calls `evaluateLanding(s, now)`. ✓
- `navTimeoutKind(state, now)` — Task 5 signature; Task 6 calls `navTimeoutKind(s, now)`; reads `dl.kind`/`dl.missing`/`dl.sourceDocumentId`. ✓
- `claimNextJob(): Promise<FetchJob | null>` — Task 6 Step 3 defines on `ApiClient` + impl; Task 6 controller calls `deps.api.claimNextJob()`; Task 7 background fake provides it. ✓
- `ControllerError` content_unavailable shape (Task 6) matches `shared/state.ts` definition (`missing`/`sourceDocumentId`/`since`/`recoveryAttempts`/`nextRecoveryAt`/`recoveryExhausted`). ✓
- `redirectKind`/`urlMatches`/`isWechatHost` — Task 2 defines; Task 6 `deliverBeforeRedirect` calls `redirectKind`; `deliverBeforeRequest` calls `urlMatches`; landing (Task 3) calls `sameCrawlSite`/`isWechatHost`. ✓
- `NavMonitorDeps = { chrome, controller }` (Task 7) — `background.ts` constructs `createNavMonitor({ chrome: deps.chrome, controller })` (Task 7 Step 7); `WireDeps` drops `storage`. ✓
- `clearAttemptSignals(nav)` typed as `NonNullable<ControllerState['navigation']>` so the optional-field clears type-check. ✓

**4. Phase-1 regression check:**
- `status.ts` — rewritten (amend §3.1); its test rewritten (expected, amend §8.2). ✓
- `navMonitor.ts` — rewritten to thin adapter; its test rewritten (expected, amend §8.2); old `handleCompleted`/`handleError`/`resolveJobIfMatched`/`reportAndMark`/`sameUrl` removed. ✓
- `chrome.ts` — widens 2 event types + adds 4 listeners; chrome.test fake updated (Task 7 Step 4). ✓
- `api.ts` — additive `claimNextJob` (existing assertions unaffected). ✓
- `storage.ts` (Phase-1) — DELETED (sole consumer rewritten); `controller/storage.ts` survives. ✓
- `background.ts` — `WireDeps` drops `storage`; navMonitor constructed with `{chrome, controller}`; test rewritten. ✓
- `content/index.ts` — untouched (slice 4 owns PAGE_READY emit). The Controller's `deliverPageReady` is wired but no CS sends PAGE_READY yet (slice 4); tests drive it directly. ✓
- `controller/reconcile.ts`/`backoff.ts`/`heartbeat.ts`/`mutex.ts`/`storage.ts`(controller) — untouched. ✓

**5. Gate-off invariant:** Official build (`EXCLUSIVE_CONTROL_ENABLED=false`) — `controller.tick` early-returns after `ensureLoaded` before claim/navigate/deadline; `bind` early-returns; every `deliver*` early-returns after `ensureLoaded` before any state mutation. navMonitor `start()` registers listeners but each `deliver*` is a no-op when gated. The reconciliation alarm fires `controller.tick()` → early-return. No marker, no heartbeat, no `/status`, no claim, no navigate. True no-op. Task 8 Step 2 verifies the folded `if (true) return` / `if (false) {` in `dist/`. ✓

**6. Single-in-flight invariant:** `/jobs/next` returns 204 when a job is assigned; the claim step runs only when `phase==='idle' && currentJob===null` and does NOT loop (one attempt per tick). Once `currentJob` is cached, reconcile (slice 2) preserves it across ticks; the claim guard `currentJob===null` prevents re-claim. Re-navigation only via the funnel (`retry_navigate`), never concurrently. ✓

**7. Task ordering (each task leaves the full suite green):** Tasks 1–5 (pure modules) depend only on `shared/state.ts` + each other → green. Task 6 (Controller + api.claimNextJob) depends on Tasks 1–5 but NOT on `status.ts`/`chrome.ts` (Controller receives outcome from navMonitor; uses shared/navEvents types) → Phase-1 status/navMonitor untouched and green through Task 6. Task 7 is the single coupled rewrite (status.ts + chrome.ts + navMonitor.ts + background.ts + storage.ts deletion) → green after. Task 8 acceptance. No commit folding; "one green commit per step" holds at every boundary. ✓

No blocking issues found. The plan is internally consistent, scoped to slice 3, and preserves the gate-off + Phase-1-intact (modulo the two deliberate rewrites) + Python-unaffected invariants.
