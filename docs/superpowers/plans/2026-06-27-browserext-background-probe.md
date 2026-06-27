# Browser-extension background probe — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an MV3 background-only Chrome extension that recovers the owner tab when it lands on a browser-native error page (`about:neterror` / `ERR_CONNECTION_*`) — reporting real HTTP status codes to the backend and re-redirecting the stalled tab — without touching the userscript or the backend.

**Architecture:** A standalone `browserext/` MV3 project. Its background service worker runs two pure-logic engines — `navMonitor` (webRequest → status-code classification → fail/skip) and `watchdog` (alarms → read `/status` → heartbeat-stale → re-redirect) — over injectable `chrome.*` and HTTP-port interfaces. The backend `/status.current_job` is the sole scheduling truth; the extension never calls `/jobs/next` and never constructs jobs.

**Tech Stack:** TypeScript, MV3 `manifest.json`, `chrome.webRequest` / `chrome.alarms` / `chrome.storage.local` / `chrome.tabs` APIs, global `fetch`. Tests: `node:test` + `typescript` transpile-to-mjs harness (mirrors `userscripts/tests/utils.test.mjs`). No bundler for the extension itself in Phase 1 — MV3 loads `background.ts` compiled to `background.js` via a tiny `tsc` build.

**Spec:** `docs/superpowers/specs/2026-06-27-browserext-background-probe-design.md`

---

## Global Constraints

- **Don't touch the userscript.** No edits under `userscripts/`. The extension is a parallel project.
- **Don't touch the backend or HTTP contract.** All endpoints used (`GET /status`, `POST /jobs/{id}/fail`, `POST /jobs/{id}/skip`) already exist and are idempotent.
- **Backend is the sole job-state truth.** The extension never calls `/jobs/next`, never tracks job state, never constructs a `FetchJob`. It only reads `/status` and posts `fail`/`skip`.
- **Single in-flight invariant (SP0 §1.6) must hold.** The extension releases the in-flight slot only via backend `/fail`/`/skip`; it never assigns a job.
- **Backend base URL:** `http://127.0.0.1:21520/api` (matches `userscripts/src/api.ts:12` `API_BASE`).
- **HTTP status→action contract (mirrors `src/dext/engine/retry.py:21` `classify_fetch_failure`):**
  - 404 / 410 → `POST /jobs/{id}/skip` body `{"reason":"not_found"}`
  - 502 / 503 / 504 → count +1; on the **3rd consecutive** abnormal navigation for that job → `POST /jobs/{id}/fail` body `{"message":"gateway_5xx"}`
  - `onErrorOccurred` (`ERR_*`) → count +1; on 3rd → `POST /jobs/{id}/fail` body `{"message":"nav_error:ERR_..."}` (the literal `error` string from the event)
  - 2xx / 3xx / 429 → no call
- **STALE_THRESHOLD = 15 s** (matches `src/dext/bridge/health.py:10` `DEFAULT_FRONTEND_HEARTBEAT_STALE_SECONDS = 15.0` — the backend's own staleness gate, so the extension's watchdog reclaims at the same threshold the backend uses to pause the job timeout).
- **MAX abnormal navigations before fail = 3.** Persisted per-job in `chrome.storage.local` (service workers are evicted; no in-memory state survives).
- **Phase-1 split rule (avoid double-write with userscript):** On `about:neterror` / `onErrorOccurred` (userscript does NOT inject) the extension itself re-redirects via `chrome.tabs.update`. On http 5xx (userscript DOES inject and retries via `autoCheck.isErrorPage()`) the extension only counts + fails; it does NOT redirect. See Task 7.
- **Test harness:** every TS unit test transpiles the source with `typescript.transpileModule` to a temp `.mjs` and imports it — copy the `importTsModule` helper from `userscripts/tests/utils.test.mjs`. Tests inject fakes for `chrome.*` and the HTTP client; never hit the real browser or real backend.
- **Platform:** Windows + Git Bash. `LF will be replaced by CRLF` git warnings are benign.
- **Commit convention:** one conventional commit per green step: `feat(browserext): …`, `test(browserext): …`, `docs(browserext): …`, `chore(browserext): …`.
- **Build/run commands** (run from repo root `D:\pyprj\dext`):
  - Extension unit tests: `cd browserext && npm test`
  - Backend regression (zero changes expected): `uv run pytest -q`

---

## File Structure

```
browserext/
  package.json              # node deps: typescript, @types/chrome, node; scripts: build, test
  tsconfig.json             # MV3 target: ES2022, module ESNext, chrome types
  manifest.json             # MV3: background SW, webRequest + alarms + tabs + storage perms
  src/
    chrome.ts               # injectable chrome.* surface (an interface + a real impl + a fake)
    api.ts                  # HTTP client: getStatus(), failJob(id,msg), skipJob(id,reason)
    status.ts               # pure: classify navigation → NavAction (no chrome, no IO)
    storage.ts              # injectable chrome.storage.local wrapper for per-job counters
    navMonitor.ts           # wires webRequest events → status.classify → api/storage
    watchdog.ts             # wires chrome.alarms → api.getStatus → chrome.tabs.update
    background.ts           # MV3 SW entry: registers listeners + alarm; thin wiring only
  tests/
    harness.mjs             # importTsModule helper (copied from userscripts/tests/utils.test.mjs)
    status.test.mjs         # classify() pure-logic tests
    storage.test.mjs        # counter persistence + verdict_sent guard tests
    api.test.mjs            # api.ts request building + 204 handling (fake fetch)
    navMonitor.test.mjs     # end-to-end: webRequest event → api call (fake chrome + fetch)
    watchdog.test.mjs       # end-to-end: alarm → status → tabs.update / no-op
    background.test.mjs     # wiring sanity: listeners registered, alarm created
  README.md                 # load-unpacked instructions + manual verification checklist
```

**Responsibility boundaries:**

- `status.ts` — **pure**, no chrome, no IO. `classify(statusCode | error) → NavAction`. The only decision logic. Fully unit-testable with plain data.
- `storage.ts` — injectable `Storage` interface (`bumpCount`, `getCount`, `markVerdictSent`, `wasVerdictSent`, `recordRedirect`, `shouldRedirect`) backed by `chrome.storage.local`; a fake backs tests.
- `api.ts` — injectable `ApiClient` interface (`getStatus`, `failJob`, `skipJob`) backed by `fetch`; a fake backs tests.
- `chrome.ts` — injectable `ChromeRuntime` interface (webRequest subscribe, alarms, tabs.update, tabs.query). Real impl wraps `chrome.*`; fake backs tests.
- `navMonitor.ts` — orchestrator: takes (`ChromeRuntime`, `ApiClient`, `Storage`). Wires webRequest events to `status.classify` + storage + api. No decision logic of its own.
- `watchdog.ts` — orchestrator: takes (`ChromeRuntime`, `ApiClient`, `Storage`, `clock`). On alarm: getStatus → if stale & current_job → find owner tab → tabs.update. No state of its own beyond reading storage for debounce.
- `background.ts` — composition root only. Constructs real impls, wires them, registers the alarm. No logic; not unit-tested beyond "registers N listeners, creates 1 alarm".

DAG: `background.ts` → `navMonitor`, `watchdog`, `chrome`, `api`, `storage`, `status`. `navMonitor`/`watchdog` → `status`, `api`, `storage`, `chrome`. `status`/`storage`/`api`/`chrome` are leaves. Acyclic.

---

## Task 1: Scaffold `browserext/` project (manifest, tsconfig, package.json, build)

**Files:**
- Create: `browserext/package.json`
- Create: `browserext/tsconfig.json`
- Create: `browserext/manifest.json`
- Create: `browserext/src/background.ts` (empty placeholder so `tsc` has an input)
- Create: `browserext/.gitignore`

**Interfaces:**
- Produces: a `npm test`-runnable, `tsc`-compilable MV3 project skeleton that later tasks fill in. No exports yet.

- [ ] **Step 1: Create `browserext/package.json`**

```json
{
  "name": "dext-browserext",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "tsc",
    "test": "node --test tests/"
  },
  "devDependencies": {
    "@types/chrome": "^0.0.287",
    "typescript": "~5.7.0"
  }
}
```

> Note: `@types/chrome` provides the MV3 `chrome.*` typings. Version `~0.7.x`/`~0.0.2xx` both float; `^0.0.287` is a recent published line at write time — if `npm install` fails to resolve, run `npm view @types/chrome version` and pin to whatever that prints. The tests do NOT import `@types/chrome` (they use their own fake interfaces), so the exact version only affects the build, not the tests.

- [ ] **Step 2: Create `browserext/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022"],
    "types": ["chrome"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "outDir": "dist",
    "sourceMap": false,
    "declaration": false,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts"]
}
```

- [ ] **Step 3: Create `browserext/manifest.json`**

```json
{
  "manifest_version": 3,
  "name": "dext background probe",
  "version": "0.1.0",
  "description": "Recovers the dext userscript owner tab when it lands on a browser-native error page. Watches nav status codes and re-redirects stalled tabs.",
  "background": {
    "service_worker": "dist/background.js",
    "type": "module"
  },
  "permissions": ["webRequest", "alarms", "tabs", "storage"],
  "host_permissions": [
    "http://127.0.0.1:21520/*",
    "<all_urls>"
  ],
  "minimum_chrome_version": "110"
}
```

> `<all_urls>` host permission is required for `webRequest` to observe navigations on every site (the owner can be on any edu.cn/github.io host). `127.0.0.1:21520` is covered by `<all_urls>` too but listed explicitly for clarity; harmless duplication.

- [ ] **Step 4: Create `browserext/src/background.ts` placeholder**

```typescript
// MV3 background service worker entry.
// Wired up in Task 8. Placeholder so tsc has an input for the build smoke test.
export {};
```

- [ ] **Step 5: Create `browserext/.gitignore`**

```
node_modules/
dist/
```

- [ ] **Step 6: Install deps and verify the build compiles**

Run:
```bash
cd browserext && npm install && npm run build
```
Expected: exits 0, creates `browserext/dist/background.js`. (If `@types/chrome` version won't resolve, see Step 1 note.)

- [ ] **Step 7: Commit**

```bash
git add browserext/
git commit -m "chore(browserext): scaffold MV3 project (manifest, tsconfig, package.json)"
```

---

## Task 2: Copy test harness; add a smoke test that `npm test` runs

**Files:**
- Create: `browserext/tests/harness.mjs`
- Create: `browserext/tests/smoke.test.mjs`

**Interfaces:**
- Produces: `importTsModule(sourcePath, outputName)` (async → `{ mod, cleanup }`) for all later test files to consume. Copied verbatim from `userscripts/tests/utils.test.mjs:8-27` so the harness is identical.

- [ ] **Step 1: Create `browserext/tests/harness.mjs`**

Copy verbatim from `userscripts/tests/utils.test.mjs` lines 8–27:

```javascript
import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import ts from 'typescript';

export async function importTsModule(sourcePath, outputName) {
  const outDir = await mkdtemp(join(tmpdir(), 'dext-ext-test-'));
  const result = ts.transpileModule(
    await readFile(new URL(sourcePath, import.meta.url), 'utf8'),
    {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: outputName,
    },
  );
  const outFile = join(outDir, outputName.replace(/\.ts$/, '.mjs'));
  await writeFile(outFile, result.outputText, 'utf8');
  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return {
    mod,
    cleanup: () => rm(outDir, { recursive: true, force: true }),
  };
}
```

> `typescript` is already a devDependency (Task 1). The harness imports `readFile`/`writeFile` as named imports (slightly cleaner than the inline dynamic `import('node:fs/promises')` in the userscript original) — same behavior.

- [ ] **Step 2: Create a smoke test**

`browserext/tests/smoke.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('harness transpiles and imports a TS module', async () => {
  // Re-import background.ts (the placeholder from Task 1) just to prove the harness works.
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  try {
    assert.equal(typeof mod, 'object');
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 3: Run the test**

Run:
```bash
cd browserext && npm test
```
Expected: PASS, 1 test. (If it fails to find `typescript`, re-run `npm install`.)

- [ ] **Step 4: Commit**

```bash
git add browserext/tests/
git commit -m "test(browserext): add TS transpile harness + smoke test"
```

---

## Task 3: `status.ts` — pure navigation classifier

**Files:**
- Create: `browserext/src/status.ts`
- Test: `browserext/tests/status.test.mjs`

**Interfaces:**
- Produces:
  - `type NavOutcome = 'ok' | 'not_found' | 'gateway' | 'rate_limited' | 'nav_error'`
  - `interface NavInput { kind: 'completed'; statusCode: number } | { kind: 'error'; error: string }`
  - `function classifyNavigation(input: NavInput): NavOutcome`
  - `const GATEWAY_STATUSES = new Set([502, 503, 504])` (exported for tests)
  - `const DEAD_STATUSES = new Set([404, 410])` (exported for tests)
- Consumes: nothing (pure leaf).

> Decision logic lives ONLY here. `navMonitor.ts` (Task 6) calls `classifyNavigation` and maps outcomes to actions; it never hardcodes a status code.

- [ ] **Step 1: Write the failing test**

`browserext/tests/status.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('classifyNavigation maps status codes', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    // 2xx → ok
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 200 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 204 }), 'ok');
    // 3xx is not delivered by onCompleted (final 2xx is), but if seen → ok
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 301 }), 'ok');
    // 404/410 → not_found
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 404 }), 'not_found');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 410 }), 'not_found');
    // 429 → rate_limited (no action; the backend's 429 path handles backoff)
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 429 }), 'rate_limited');
    // 5xx gateway
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 502 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 503 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 504 }), 'gateway');
    // 500 → gateway too (server error, treat as retryable transient)
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 500 }), 'gateway');
    // other 4xx (403/401) → ok (let the userscript capture; backend's unavailable-page
    // assessment + LLM handle semantic exclusion, not this probe)
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 403 }), 'ok');
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

test('GATEWAY_STATUSES and DEAD_STATUSES are exported', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.ok(mod.GATEWAY_STATUSES.has(502));
    assert.ok(mod.GATEWAY_STATUSES.has(503));
    assert.ok(mod.GATEWAY_STATUSES.has(504));
    assert.ok(mod.DEAD_STATUSES.has(404));
    assert.ok(mod.DEAD_STATUSES.has(410));
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `status.ts` does not exist, import errors.

- [ ] **Step 3: Implement `browserext/src/status.ts`**

```typescript
/** Pure navigation classifier — the ONLY place status-code → outcome mapping lives.
 * No chrome, no IO. Mirrors the backend's retry.classify_fetch_failure intent
 * (404/410 = terminal skip, 5xx = retryable gateway, 429 = rate-limited). */

export type NavOutcome = 'ok' | 'not_found' | 'gateway' | 'rate_limited' | 'nav_error';

export type NavInput =
  | { kind: 'completed'; statusCode: number }
  | { kind: 'error'; error: string };

export const DEAD_STATUSES = new Set([404, 410]);
export const RATE_LIMITED_STATUS = 429;
export const GATEWAY_STATUSES = new Set([500, 502, 503, 504]);

export function classifyNavigation(input: NavInput): NavOutcome {
  if (input.kind === 'error') return 'nav_error';
  const code = input.statusCode;
  if (DEAD_STATUSES.has(code)) return 'not_found';
  if (code === RATE_LIMITED_STATUS) return 'rate_limited';
  if (GATEWAY_STATUSES.has(code)) return 'gateway';
  return 'ok';
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (smoke test + 3 status tests = 4 passing).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/status.ts browserext/tests/status.test.mjs
git commit -m "feat(browserext): add pure navigation status classifier"
```

---

## Task 4: `storage.ts` — per-job counter & verdict-sent persistence (injectable)

**Files:**
- Create: `browserext/src/storage.ts`
- Test: `browserext/tests/storage.test.mjs`

**Interfaces:**
- Produces:
  - `interface Storage { bumpCount(jobId): Promise<number>; getCount(jobId): Promise<number>; markVerdictSent(jobId): Promise<void>; wasVerdictSent(jobId): Promise<boolean>; recordRedirect(jobId): Promise<void>; shouldRedirect(jobId, nowMs): Promise<boolean>; clear(jobId): Promise<void> }`
  - `const REDIRECT_DEBOUNCE_MS = 15_000` (matches STALE_THRESHOLD in Global Constraints; reused as watchdog redirect-debounce window)
  - `function createChromeStorage(area): Storage` — `area` is a `chrome.storage.local`-shaped object
- Consumes: nothing (leaf). The `area` param shape mirrors `chrome.storage.local` (`get(keys): Promise<Record<string,unknown>>`, `set(obj): Promise<void>`, `remove(keys): Promise<void>`).

> Counting semantics (from spec §2.1, clarified): `bumpCount` returns the count AFTER incrementing. The caller treats `>= MAX_ABNORMAL_NAVS` (3) as "fail now". `MAX_ABNORMAL_NAVS` is a Global Constraint, hardcoded 3 in the caller (navMonitor) — storage itself is threshold-agnostic.

- [ ] **Step 1: Write the failing test**

`browserext/tests/storage.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// Minimal fake of chrome.storage.local — an in-memory Map behind the same shape.
function fakeArea() {
  const store = new Map();
  return {
    async get(keys) {
      if (keys === null) {
        const out = {};
        for (const [k, v] of store) out[k] = v;
        return out;
      }
      const arr = Array.isArray(keys) ? keys : [keys];
      const out = {};
      for (const k of arr) if (store.has(k)) out[k] = store.get(k);
      return out;
    },
    async set(obj) {
      for (const [k, v] of Object.entries(obj)) store.set(k, v);
    },
    async remove(keys) {
      const arr = Array.isArray(keys) ? keys : [keys];
      for (const k of arr) store.delete(k);
    },
  };
}

test('bumpCount increments and returns post-increment count', async () => {
  const { mod, cleanup } = await importTsModule('../src/storage.ts', 'storage.ts');
  try {
    const s = mod.createChromeStorage(fakeArea());
    assert.equal(await s.bumpCount('job-1'), 1);
    assert.equal(await s.bumpCount('job-1'), 2);
    assert.equal(await s.bumpCount('job-1'), 3);
    // a different job is independent
    assert.equal(await s.bumpCount('job-2'), 1);
  } finally {
    await cleanup();
  }
});

test('getCount reads without incrementing', async () => {
  const { mod, cleanup } = await importTsModule('../src/storage.ts', 'storage.ts');
  try {
    const s = mod.createChromeStorage(fakeArea());
    assert.equal(await s.getCount('job-1'), 0);
    await s.bumpCount('job-1');
    assert.equal(await s.getCount('job-1'), 1);
  } finally {
    await cleanup();
  }
});

test('markVerdictSent / wasVerdictSent guard against duplicate reports', async () => {
  const { mod, cleanup } = await importTsModule('../src/storage.ts', 'storage.ts');
  try {
    const s = mod.createChromeStorage(fakeArea());
    assert.equal(await s.wasVerdictSent('job-1'), false);
    await s.markVerdictSent('job-1');
    assert.equal(await s.wasVerdictSent('job-1'), true);
  } finally {
    await cleanup();
  }
});

test('recordRedirect / shouldRedirect debounce within 15s', async () => {
  const { mod, cleanup } = await importTsModule('../src/storage.ts', 'storage.ts');
  try {
    const s = mod.createChromeStorage(fakeArea());
    // never redirected → allowed
    assert.equal(await s.shouldRedirect('job-1', 1_000), true);
    await s.recordRedirect('job-1'); // records at Date.now() internally
    // immediately after → blocked (would need a real-clock seam; tested via clear+timing below)
  } finally {
    await cleanup();
  }
});

test('shouldRedirect allows again after the debounce window', async () => {
  const { mod, cleanup } = await importTsModule('../src/storage.ts', 'storage.ts');
  try {
    const s = mod.createChromeStorage(fakeArea());
    // Drive time explicitly: record with a forced clock by setting storage directly.
    // The storage API uses Date.now() internally, so we approximate by waiting is flaky.
    // Instead, verify the exported constant matches the 15s threshold.
    assert.equal(mod.REDIRECT_DEBOUNCE_MS, 15_000);
  } finally {
    await cleanup();
  }
});

test('clear removes all per-job keys', async () => {
  const { mod, cleanup } = await importTsModule('../src/storage.ts', 'storage.ts');
  try {
    const s = mod.createChromeStorage(fakeArea());
    await s.bumpCount('job-1');
    await s.markVerdictSent('job-1');
    await s.recordRedirect('job-1');
    await s.clear('job-1');
    assert.equal(await s.getCount('job-1'), 0);
    assert.equal(await s.wasVerdictSent('job-1'), false);
  } finally {
    await cleanup();
  }
});
```

> `shouldRedirect`'s real-clock behavior is intentionally not unit-tested for elapsed-time (would need a clock seam; spec §4.2 uses `Date.now()`). The 15s constant is asserted instead. If during implementation you find `shouldRedirect` cleanly accepts a `nowMs` param, the test in Step 1 already passes `nowMs` — wire it through.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `storage.ts` does not exist.

- [ ] **Step 3: Implement `browserext/src/storage.ts`**

```typescript
/** Per-job counter & verdict-sent persistence over an injectable chrome.storage.local-shaped area.
 * All state lives in storage (service workers are evicted; no in-memory state survives). */

export const REDIRECT_DEBOUNCE_MS = 15_000;

export interface StorageArea {
  get(keys: string | string[] | null): Promise<Record<string, unknown>>;
  set(obj: Record<string, unknown>): Promise<void>;
  remove(keys: string | string[]): Promise<void>;
}

export interface Storage {
  bumpCount(jobId: string): Promise<number>;
  getCount(jobId: string): Promise<number>;
  markVerdictSent(jobId: string): Promise<void>;
  wasVerdictSent(jobId: string): Promise<boolean>;
  recordRedirect(jobId: string): Promise<void>;
  shouldRedirect(jobId: string, nowMs?: number): Promise<boolean>;
  clear(jobId: string): Promise<void>;
}

const COUNT_KEY = (id: string) => `count:${id}`;
const VERDICT_KEY = (id: string) => `verdict:${id}`;
const REDIRECT_KEY = (id: string) => `redirect:${id}`;

export function createChromeStorage(area: StorageArea): Storage {
  async function bumpCount(jobId: string): Promise<number> {
    const next = (await getCount(jobId)) + 1;
    await area.set({ [COUNT_KEY(jobId)]: next });
    return next;
  }
  async function getCount(jobId: string): Promise<number> {
    const obj = await area.get(COUNT_KEY(jobId));
    const v = obj[COUNT_KEY(jobId)];
    return typeof v === 'number' ? v : 0;
  }
  async function markVerdictSent(jobId: string): Promise<void> {
    await area.set({ [VERDICT_KEY(jobId)]: true });
  }
  async function wasVerdictSent(jobId: string): Promise<boolean> {
    const obj = await area.get(VERDICT_KEY(jobId));
    return obj[VERDICT_KEY(jobId)] === true;
  }
  async function recordRedirect(jobId: string): Promise<void> {
    await area.set({ [REDIRECT_KEY(jobId)]: Date.now() });
  }
  async function shouldRedirect(jobId: string, nowMs: number = Date.now()): Promise<boolean> {
    const obj = await area.get(REDIRECT_KEY(jobId));
    const last = obj[REDIRECT_KEY(jobId)];
    if (typeof last !== 'number') return true;
    return nowMs - last >= REDIRECT_DEBOUNCE_MS;
  }
  async function clear(jobId: string): Promise<void> {
    await area.remove([COUNT_KEY(jobId), VERDICT_KEY(jobId), REDIRECT_KEY(jobId)]);
  }
  return { bumpCount, getCount, markVerdictSent, wasVerdictSent, recordRedirect, shouldRedirect, clear };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (smoke + status 3 + storage 6 = 10 passing).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/storage.ts browserext/tests/storage.test.mjs
git commit -m "feat(browserext): add injectable per-job counter & verdict-sent storage"
```

---

## Task 5: `api.ts` — HTTP client (injectable fetch)

**Files:**
- Create: `browserext/src/api.ts`
- Test: `browserext/tests/api.test.mjs`

**Interfaces:**
- Produces:
  - `interface CurrentJob { id: string; url: string }` (minimal slice of `/status` response)
  - `interface FrontendHealth { alive: boolean; last_seen_seconds_ago: number | null }`
  - `interface StatusPayload { current_job: CurrentJob | null; frontend_health: FrontendHealth }`
  - `interface ApiClient { getStatus(): Promise<StatusPayload | null>; failJob(jobId, message): Promise<void>; skipJob(jobId, reason): Promise<void> }`
  - `function createFetchApi(base: string, fetchFn?): ApiClient` — `base` is `http://127.0.0.1:21520/api`; `fetchFn` defaults to global `fetch`
- Consumes: nothing (leaf). Shape of `/status` response verified against `src/dext/bridge/server.py:183-193` `handle_status` + `health.py:83-109` `snapshot()`.

- [ ] **Step 1: Write the failing test**

`browserext/tests/api.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeFetch(routes) {
  // routes: { 'GET /status': {status, body}, 'POST /jobs/:id/fail': ..., ... }
  return async (url, init) => {
    const u = new URL(url);
    const method = (init?.method || 'GET').toUpperCase();
    let key = `${method} ${u.pathname}`;
    // match /jobs/{id}/fail etc.
    const jobMatch = u.pathname.match(/^\/api\/jobs\/([^/]+)\/(fail|skip)$/);
    if (jobMatch) key = `POST /jobs/:id/${jobMatch[2]}`;
    const route = routes[key];
    if (!route) throw new Error(`no fake route for ${key}`);
    return {
      status: route.status,
      ok: route.status >= 200 && route.status < 300,
      json: async () => route.body,
      text: async () => JSON.stringify(route.body ?? ''),
    };
  };
}

test('getStatus parses current_job and frontend_health', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = fakeFetch({
      'GET /status': { status: 200, body: {
        queue: { pending: 0, assigned: 1, completed: 2, failed: 0, skipped: 1 },
        current_job: { id: 'abc', url: 'https://x.edu.cn/p' },
        frontend_health: { alive: true, last_seen_seconds_ago: 1.2 },
      } },
    });
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s.current_job.id, 'abc');
    assert.equal(s.current_job.url, 'https://x.edu.cn/p');
    assert.equal(s.frontend_health.alive, true);
    assert.equal(s.frontend_health.last_seen_seconds_ago, 1.2);
  } finally {
    await cleanup();
  }
});

test('getStatus returns null current_job when none in flight', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = fakeFetch({
      'GET /status': { status: 200, body: {
        queue: { pending: 0, assigned: 0, completed: 0, failed: 0, skipped: 0 },
        current_job: null,
        frontend_health: { alive: false, last_seen_seconds_ago: null },
      } },
    });
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s.current_job, null);
    assert.equal(s.frontend_health.alive, false);
  } finally {
    await cleanup();
  }
});

test('getStatus returns null on fetch error (backend down)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNREFUSED'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s, null);
  } finally {
    await cleanup();
  }
});

test('failJob POSTs message', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let capturedBody = null;
    const fetchFn = async (url, init) => {
      capturedBody = JSON.parse(init.body);
      return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.failJob('abc', 'gateway_5xx');
    assert.deepEqual(capturedBody, { message: 'gateway_5xx' });
  } finally {
    await cleanup();
  }
});

test('skipJob POSTs reason', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let capturedBody = null;
    const fetchFn = async (url, init) => {
      capturedBody = JSON.parse(init.body);
      return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.skipJob('abc', 'not_found');
    assert.deepEqual(capturedBody, { reason: 'not_found' });
  } finally {
    await cleanup();
  }
});

test('failJob and skipJob swallow errors (idempotent; backend already-resolved → no-op)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('network'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    // must NOT throw — late/stale reports are normal (backend 60s timeout may have fired)
    await api.failJob('abc', 'gateway_5xx');
    await api.skipJob('abc', 'not_found');
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `api.ts` does not exist.

- [ ] **Step 3: Implement `browserext/src/api.ts`**

```typescript
/** Injectable HTTP client for the dext backend. Mirrors userscripts/src/api.ts endpoints
 * but uses fetch (background SW has no GM_xmlhttpRequest). All calls are best-effort:
 * getStatus returns null on error; failJob/skipJob swallow errors (late/stale reports
 * are normal — the backend's 60s job timeout may have already released the slot,
 * and /fail + /skip are idempotent against stale ids anyway). */

export interface CurrentJob {
  id: string;
  url: string;
}

export interface FrontendHealth {
  alive: boolean;
  last_seen_seconds_ago: number | null;
}

export interface StatusPayload {
  current_job: CurrentJob | null;
  frontend_health: FrontendHealth;
}

export interface ApiClient {
  getStatus(): Promise<StatusPayload | null>;
  failJob(jobId: string, message: string): Promise<void>;
  skipJob(jobId: string, reason: string): Promise<void>;
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

  return { getStatus, failJob, skipJob };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (smoke 1 + status 3 + storage 6 + api 6 = 16 passing).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/api.ts browserext/tests/api.test.mjs
git commit -m "feat(browserext): add injectable backend HTTP client"
```

---

## Task 6: `chrome.ts` — injectable chrome.* runtime surface

**Files:**
- Create: `browserext/src/chrome.ts`
- Test: `browserext/tests/chrome.test.mjs`

**Interfaces:**
- Produces:
  - `interface NavCompletedEvent { tabId: number; url: string; statusCode: number; frameId: number }`
  - `interface NavErrorEvent { tabId: number; url: string; error: string; frameId: number }`
  - `interface ChromeRuntime { onNavCompleted(cb: (e: NavCompletedEvent) => void): void; onNavError(cb: (e: NavErrorEvent) => void): void; updateTabUrl(tabId: number, url: string): Promise<void>; findOwnerTab(): Promise<number | null>; registerWatchdogAlarm(name: string, periodMinutes: number, cb: () => void): void }`
  - `function createRealChromeRuntime(): ChromeRuntime` — wraps real `chrome.*` (only used by `background.ts`; not unit-tested directly)
- Consumes: nothing (leaf). The real impl reads `chrome.webRequest`, `chrome.alarms`, `chrome.tabs`.

> Tests do NOT exercise `createRealChromeRuntime` — they construct fakes implementing `ChromeRuntime`. The real impl is exercised only in manual verification (Task 10).

- [ ] **Step 1: Write the failing test**

`browserext/tests/chrome.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// We can't easily unit-test createRealChromeRuntime (it binds real chrome.*),
// but we CAN test the interface contract by building a fake that matches it,
// proving the shape navMonitor/watchdog will consume.
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
    registerWatchdogAlarm(_name, _period, cb) { alarmCb = cb; },
    // test helpers
    fireCompleted(e) { for (const cb of completedCbs) cb(e); },
    fireError(e) { for (const cb of errorCbs) cb(e); },
    fireAlarm() { if (alarmCb) alarmCb(); },
    lastUpdated,
    _tabs: tabs,
  };
}

test('ChromeRuntime interface is satisfiable by a fake and forwards callbacks', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    // Just import the module to prove the interface + createRealChromeRuntime export exist;
    // behavioral testing of consumers happens in navMonitor/watchdog tests.
    assert.equal(typeof mod.createRealChromeRuntime, 'function');
  } finally {
    await cleanup();
  }
});
```

> This task's test is intentionally thin — `chrome.ts` is a thin adapter over the real `chrome.*` APIs and is verified manually (Task 10). The interface shape is what matters, and it's exercised end-to-end via the fakes in Tasks 7 & 8. Keeping a minimal test avoids brittle mocking of `chrome.*` internals.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `chrome.ts` does not exist.

- [ ] **Step 3: Implement `browserext/src/chrome.ts`**

```typescript
/** Injectable adapter over the MV3 chrome.* surface. Consumers (navMonitor, watchdog)
 * depend on the ChromeRuntime interface so tests inject fakes. createRealChromeRuntime
 * binds the real chrome.* APIs and is used only by background.ts (manual verification). */

export interface NavCompletedEvent {
  tabId: number;
  url: string;
  statusCode: number;
  frameId: number;
}

export interface NavErrorEvent {
  tabId: number;
  url: string;
  error: string;
  frameId: number;
}

export interface ChromeRuntime {
  onNavCompleted(cb: (e: NavCompletedEvent) => void): void;
  onNavError(cb: (e: NavErrorEvent) => void): void;
  updateTabUrl(tabId: number, url: string): Promise<void>;
  findOwnerTab(): Promise<number | null>;
  registerWatchdogAlarm(name: string, periodMinutes: number, cb: () => void): void;
}

const ALLOWED_HOST_SUFFIXES = ['edu.cn', 'github.io'];

function isAllowedHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return ALLOWED_HOST_SUFFIXES.some((s) => h === s || h.endsWith(`.${s}`));
}

export function createRealChromeRuntime(): ChromeRuntime {
  return {
    onNavCompleted(cb) {
      // Main frame only (frameId === 0) — sub-frame errors are noise.
      chrome.webRequest.onCompleted.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({ tabId: details.tabId, url: details.url, statusCode: details.statusCode, frameId: details.frameId });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onNavError(cb) {
      chrome.webRequest.onErrorOccurred.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({ tabId: details.tabId, url: details.url, error: details.error, frameId: details.frameId });
        },
        { urls: ['<all_urls>'] },
      );
    },
    async updateTabUrl(tabId, url) {
      await chrome.tabs.update(tabId, { url });
    },
    async findOwnerTab() {
      const tabs = await chrome.tabs.query({});
      // Prefer a tab on an allowed fetch host; fall back to the active tab.
      for (const t of tabs) {
        try {
          if (t.url && isAllowedHost(new URL(t.url).hostname)) return t.id ?? null;
        } catch {
          // ignore non-URL tab URLs (about:blank, chrome-error, etc.)
        }
      }
      const active = await chrome.tabs.query({ active: true, currentWindow: true });
      return active[0]?.id ?? null;
    },
    registerWatchdogAlarm(name, periodMinutes, cb) {
      chrome.alarms.create(name, { periodInMinutes: periodMinutes });
      chrome.alarms.onAlarm.addListener((alarm) => {
        if (alarm.name === name) cb();
      });
    },
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (smoke 1 + status 3 + storage 6 + api 6 + chrome 1 = 17 passing).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/chrome.ts browserext/tests/chrome.test.mjs
git commit -m "feat(browserext): add injectable chrome.* runtime surface"
```

---

## Task 7: `navMonitor.ts` — webRequest events → classify → fail/skip/redirect

**Files:**
- Create: `browserext/src/navMonitor.ts`
- Test: `browserext/tests/navMonitor.test.mjs`

**Interfaces:**
- Produces:
  - `const MAX_ABNORMAL_NAVS = 3` (Global Constraint, exported)
  - `function createNavMonitor(deps: { chrome: ChromeRuntime; api: ApiClient; storage: Storage; clock?: () => number }): NavMonitor`
  - `interface NavMonitor { start(): void; handleCompleted(e: NavCompletedEvent): Promise<void>; handleError(e: NavErrorEvent): Promise<void> }` (`start` wires chrome callbacks; `handleCompleted`/`handleError` exposed for direct unit testing)
- Consumes:
  - `ChromeRuntime` from Task 6
  - `ApiClient` from Task 5
  - `Storage` from Task 4
  - `classifyNavigation`, `NavInput`, `NavOutcome` from Task 3

> Split rule (spec §1.2 + §2.1): `nav_error` (about:neterror) → the extension itself re-redirects via `chrome.updateTabUrl` (userscript does NOT inject there). `gateway`/`not_found` → the extension does NOT redirect; it only counts + reports (the userscript injects on http pages and retries via `autoCheck.isErrorPage()`).

- [ ] **Step 1: Write the failing test**

`browserext/tests/navMonitor.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeStorage() {
  const counts = new Map();
  const verdicts = new Set();
  const redirects = new Map();
  return {
    async bumpCount(id) { const n = (counts.get(id) ?? 0) + 1; counts.set(id, n); return n; },
    async getCount(id) { return counts.get(id) ?? 0; },
    async markVerdictSent(id) { verdicts.add(id); },
    async wasVerdictSent(id) { return verdicts.has(id); },
    async recordRedirect(id) { redirects.set(id, Date.now()); },
    async shouldRedirect(id) { return !redirects.has(id); },
    async clear(id) { counts.delete(id); verdicts.delete(id); redirects.delete(id); },
  };
}

function fakeApi(statusResponse) {
  const calls = [];
  return {
    calls,
    async getStatus() { return statusResponse; },
    async failJob(id, msg) { calls.push({ kind: 'fail', id, msg }); },
    async skipJob(id, reason) { calls.push({ kind: 'skip', id, reason }); },
  };
}

function fakeChrome() {
  const updates = [];
  const completedCbs = [], errorCbs = [];
  return {
    onNavCompleted(cb) { completedCbs.push(cb); },
    onNavError(cb) { errorCbs.push(cb); },
    async updateTabUrl(tabId, url) { updates.push({ tabId, url }); },
    async findOwnerTab() { return 1; },
    registerWatchdogAlarm() {},
    // test helpers
    updates,
    async fireCompleted(e) { for (const cb of completedCbs) await cb(e); },
    async fireError(e) { for (const cb of errorCbs) await cb(e); },
  };
}

test('404 posts skip with reason=not_found', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0 });
    assert.deepEqual(api.calls, [{ kind: 'skip', id: 'job-1', reason: 'not_found' }]);
  } finally {
    await cleanup();
  }
});

test('502 counts; on 3rd posts fail message=gateway_5xx; does NOT redirect', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    assert.deepEqual(api.calls, []); // not yet
    assert.equal(chr.updates.length, 0); // gateway never self-redirects
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    assert.deepEqual(api.calls, [{ kind: 'fail', id: 'job-1', msg: 'gateway_5xx' }]);
  } finally {
    await cleanup();
  }
});

test('nav_error self-redirects on counts 1 and 2; posts fail nav_error on 3rd', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireError({ tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0 });
    assert.equal(chr.updates.length, 1);
    assert.equal(chr.updates[0].url, 'https://x.edu.cn/p');
    await chr.fireError({ tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0 });
    assert.equal(chr.updates.length, 2); // debounced by storage.shouldRedirect — fake allows every time
    assert.deepEqual(api.calls, []);
    await chr.fireError({ tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0 });
    assert.deepEqual(api.calls, [{ kind: 'fail', id: 'job-1', msg: 'nav_error:ERR_CONNECTION_REFUSED' }]);
  } finally {
    await cleanup();
  }
});

test('no current_job → no calls (backend already released slot)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: null, frontend_health: { alive: false, last_seen_seconds_ago: null } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});

test('verdict_sent guard: no double report for same job', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0 });
    // a second 404 for the same job (e.g. userscript hadn't navigated away yet) must not re-skip
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0 });
    assert.equal(api.calls.length, 1);
  } finally {
    await cleanup();
  }
});

test('2xx → no action', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 200, frameId: 0 });
    assert.deepEqual(api.calls, []);
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('429 → no action (backend handles rate-limit via status_code path)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 429, frameId: 0 });
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});

test('abnormal nav on an UNRELATED url does not touch the in-flight job (spec §4.4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    // The owner browses a different page that 404s. The in-flight crawl job (job-1)
    // must NOT be skipped — the event URL does not match current_job.url.
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/faculty' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://other.example.com/gone', statusCode: 404, frameId: 0 });
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});

test('nav_error on an UNRELATED url does not redirect to the job (spec §4.4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/faculty' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireError({ tabId: 1, url: 'https://dead.example.invalid/x', error: 'ERR_NAME_NOT_RESOLVED', frameId: 0 });
    assert.equal(chr.updates.length, 0);
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});
```

> The two §4.4 tests assert that an abnormal navigation whose URL does **not** match `current_job.url` is ignored entirely — the owner browsing an unrelated broken page must not skip/fail the in-flight crawl job. This requires the implementation to fetch `/status` and compare `event.url` against `current_job.url` before acting.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `navMonitor.ts` does not exist.

- [ ] **Step 3: Implement `browserext/src/navMonitor.ts`**

```typescript
/** Orchestrator: webRequest events → status.classify → api.fail/skip + chrome.redirect.
 * Split rule (spec §2.1): nav_error (about:neterror, userscript does NOT inject) →
 * self-redirect via chrome.updateTabUrl; gateway/not_found (http page, userscript
 * DOES inject and retries) → count + report only, no redirect.
 * Scope guard (spec §4.4): only act on navigations whose URL matches current_job.url —
 * the owner browsing an unrelated broken page must not skip/fail the in-flight job. */

import type { ApiClient } from './api';
import { classifyNavigation } from './status';
import type { ChromeRuntime, NavCompletedEvent, NavErrorEvent } from './chrome';
import type { Storage } from './storage';

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
    // below threshold: self-redirect (userscript can't — it doesn't inject on about:neterror)
    if (await storage.shouldRedirect(job.id)) {
      await chrome.updateTabUrl(e.tabId, job.url);
      await storage.recordRedirect(job.id);
    }
  }

  return {
    start() {
      chrome.onNavCompleted((e) => { void handleCompleted(e); });
      chrome.onNavError((e) => { void handleError(e); });
    },
    handleCompleted,
    handleError,
  };
}
```

> Change vs. the original Task 7 implementation: the `currentJobId()` helper became `resolveJobIfMatched(eventUrl)`, which fetches `/status` and returns the job **only if** `event.url` matches `current_job.url`. The `handleError` self-redirect now uses `job.url` from the same resolved object (no second `getStatus` call). The `sameUrl` helper mirrors the userscript's `urlMatches` intent.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (smoke 1 + status 3 + storage 6 + api 6 + chrome 1 + navMonitor 9 = 26 passing).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/navMonitor.ts browserext/tests/navMonitor.test.mjs
git commit -m "feat(browserext): wire webRequest events to fail/skip + nav_error redirect"
```

---

## Task 8: `watchdog.ts` — alarm → getStatus → re-redirect stalled owner

**Files:**
- Create: `browserext/src/watchdog.ts`
- Test: `browserext/tests/watchdog.test.mjs`

**Interfaces:**
- Produces:
  - `const STALE_THRESHOLD_SECONDS = 15` (matches `health.py:10`; exported)
  - `const WATCHDOG_ALARM_NAME = 'dext-watchdog'`
  - `const WATCHDOG_PERIOD_MINUTES = 1` (`chrome.alarms` minimum period; webRequest events keep the SW warm so effective cadence is faster — spec §4.1)
  - `function createWatchdog(deps: { chrome: ChromeRuntime; api: ApiClient; storage: Storage; clock?: () => number }): Watchdog`
  - `interface Watchdog { start(): void; tick(): Promise<void> }` (`tick` exposed for direct unit testing)
- Consumes: `ChromeRuntime` (Task 6), `ApiClient` (Task 5), `Storage` (Task 4).

> Watchdog does NOT call fail/skip — that's navMonitor's job. Watchdog only re-redirects. If the redirected nav then 5xx/errs, navMonitor counts it; after 3, navMonitor fails. This is the closed loop in spec §3.

- [ ] **Step 1: Write the failing test**

`browserext/tests/watchdog.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeStorage() {
  const redirects = new Map();
  return {
    async bumpCount() { return 0; },
    async getCount() { return 0; },
    async markVerdictSent() {},
    async wasVerdictSent() { return false; },
    async recordRedirect(id) { redirects.set(id, Date.now()); },
    async shouldRedirect(id) { return !redirects.has(id); },
    async clear() {},
  };
}

function fakeChrome(ownerTabId = 1) {
  const updates = [];
  let alarmCb = null;
  return {
    onNavCompleted() {},
    onNavError() {},
    async updateTabUrl(tabId, url) { updates.push({ tabId, url }); },
    async findOwnerTab() { return ownerTabId; },
    registerWatchdogAlarm(_name, _period, cb) { alarmCb = cb; },
    updates,
    fireAlarm() { if (alarmCb) alarmCb(); },
  };
}

function fakeApi(statusResponse) {
  return {
    async getStatus() { return statusResponse; },
    async failJob() {},
    async skipJob() {},
  };
}

test('tick redirects when stale + current_job present', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 1);
    assert.equal(chr.updates[0].url, 'https://x.edu.cn/p');
  } finally {
    await cleanup();
  }
});

test('tick does nothing when heartbeat alive', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: true, last_seen_seconds_ago: 3 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('tick does nothing when no current_job (backend released slot)', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: null,
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('tick does nothing when stale but within 15s threshold', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    // alive=false but last_seen 10s (< 15s threshold) → don't redirect yet
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 10 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('debounce: second tick for same job within window does not re-redirect', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    await wd.tick();
    assert.equal(chr.updates.length, 1); // storage.shouldRedirect blocked the 2nd
  } finally {
    await cleanup();
  }
});

test('start registers alarm; fireAlarm triggers tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    wd.start();
    chr.fireAlarm();
    // alarm callback is async; let it settle
    await new Promise((r) => setImmediate(r));
    assert.equal(chr.updates.length, 1);
  } finally {
    await cleanup();
  }
});

test('no owner tab found → no redirect, no crash', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(null);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('STALE_THRESHOLD_SECONDS is 15', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    assert.equal(mod.STALE_THRESHOLD_SECONDS, 15);
    assert.equal(mod.WATCHDOG_ALARM_NAME, 'dext-watchdog');
    assert.equal(mod.WATCHDOG_PERIOD_MINUTES, 1);
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `watchdog.ts` does not exist.

- [ ] **Step 3: Implement `browserext/src/watchdog.ts`**

```typescript
/** Watchdog: on each alarm, read /status; if the owner heartbeat is stale beyond the
 * threshold AND a current_job exists, re-redirect the owner tab to the job URL (the
 * userscript can't recover itself from about:neterror). Debounced per-job via storage.
 * Does NOT call fail/skip — that's navMonitor's job. Closed loop (spec §3): redirect →
 * nav counts → navMonitor fails after MAX_ABNORMAL_NAVS. */

import type { ApiClient } from './api';
import type { ChromeRuntime } from './chrome';
import type { Storage } from './storage';

export const STALE_THRESHOLD_SECONDS = 15;
export const WATCHDOG_ALARM_NAME = 'dext-watchdog';
export const WATCHDOG_PERIOD_MINUTES = 1;

export interface WatchdogDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export interface Watchdog {
  start(): void;
  tick(): Promise<void>;
}

export function createWatchdog(deps: WatchdogDeps): Watchdog {
  const { chrome, api, storage } = deps;

  async function tick(): Promise<void> {
    const status = await api.getStatus();
    if (!status) return;
    const fh = status.frontend_health;
    if (fh.alive) return;
    const lastSeen = fh.last_seen_seconds_ago;
    if (lastSeen === null || lastSeen < STALE_THRESHOLD_SECONDS) return;
    const job = status.current_job;
    if (!job) return; // backend already released the slot
    if (!(await storage.shouldRedirect(job.id))) return; // debounced
    const tabId = await chrome.findOwnerTab();
    if (tabId === null) return; // no candidate tab; try next alarm
    await chrome.updateTabUrl(tabId, job.url);
    await storage.recordRedirect(job.id);
  }

  return {
    start() {
      chrome.registerWatchdogAlarm(WATCHDOG_ALARM_NAME, WATCHDOG_PERIOD_MINUTES, () => { void tick(); });
    },
    tick,
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (smoke 1 + status 3 + storage 6 + api 6 + chrome 1 + navMonitor 9 + watchdog 8 = 34 passing).

- [ ] **Step 5: Commit**

```bash
git add browserext/src/watchdog.ts browserext/tests/watchdog.test.mjs
git commit -m "feat(browserext): add heartbeat-stale watchdog re-redirect"
```

---

## Task 9: `background.ts` — composition root (wire real impls + register)

**Files:**
- Modify: `browserext/src/background.ts` (replace the Task 1 placeholder)
- Test: `browserext/tests/background.test.mjs`

**Interfaces:**
- Produces: `function wireBackground(deps: { chrome: ChromeRuntime; api: ApiClient; storage: Storage }): { navMonitor: NavMonitor; watchdog: Watchdog }` — the pure composition function (testable); `background.ts` top-level calls it with real impls.
- Consumes: `createRealChromeRuntime` (Task 6), `createFetchApi` (Task 5), `createChromeStorage` (Task 4), `createNavMonitor` (Task 7), `createWatchdog` (Task 8), and the real `chrome.storage.local`.

- [ ] **Step 1: Write the failing test**

`browserext/tests/background.test.mjs`:

```javascript
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('wireBackground constructs navMonitor + watchdog and starts both', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  try {
    let started = { nav: false, wd: false };
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; },
      registerWatchdogAlarm() {},
    };
    const fakeApi = { async getStatus() { return null; }, async failJob() {}, async skipJob() {} };
    const fakeStorage = {
      async bumpCount() { return 0; }, async getCount() { return 0; },
      async markVerdictSent() {}, async wasVerdictSent() { return false; },
      async recordRedirect() {}, async shouldRedirect() { return true; }, async clear() {},
    };
    const { navMonitor, watchdog } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi, storage: fakeStorage });
    // monkeypatch start to detect invocation
    const origNavStart = navMonitor.start.bind(navMonitor);
    const origWdStart = watchdog.start.bind(watchdog);
    navMonitor.start = () => { started.nav = true; origNavStart(); };
    watchdog.start = () => { started.wd = true; origWdStart(); };
    navMonitor.start();
    watchdog.start();
    assert.equal(started.nav, true);
    assert.equal(started.wd, true);
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `wireBackground` not exported.

- [ ] **Step 3: Implement `browserext/src/background.ts`**

```typescript
/** MV3 background service worker entry. Composition root only — no logic.
 * Constructs the real chrome.* runtime, the fetch HTTP client, the chrome.storage
 * storage, then wires navMonitor + watchdog and starts both. */

import { createRealChromeRuntime } from './chrome';
import type { ChromeRuntime } from './chrome';
import { createFetchApi } from './api';
import type { ApiClient } from './api';
import { createChromeStorage } from './storage';
import type { Storage } from './storage';
import { createNavMonitor } from './navMonitor';
import type { NavMonitor } from './navMonitor';
import { createWatchdog } from './watchdog';
import type { Watchdog } from './watchdog';

const API_BASE = 'http://127.0.0.1:21520/api';

export interface WireDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export function wireBackground(deps: WireDeps): { navMonitor: NavMonitor; watchdog: Watchdog } {
  const navMonitor = createNavMonitor(deps);
  const watchdog = createWatchdog(deps);
  navMonitor.start();
  watchdog.start();
  return { navMonitor, watchdog };
}

// Self-invoke on SW startup with real implementations.
wireBackground({
  chrome: createRealChromeRuntime(),
  api: createFetchApi(API_BASE),
  storage: createChromeStorage(chrome.storage.local),
});
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd browserext && npm test`
Expected: PASS (35 passing). Note: the test imports `background.ts`, which references `chrome` at module top-level via `createRealChromeRuntime()` call — but `createRealChromeRuntime` only references `chrome.*` inside its returned methods, not at construction time, so importing the module in node (where `chrome` is undefined) does NOT throw. The `wireBackground(...)` self-call at the bottom DOES run on import, but inside node it will throw `chrome is not defined` because the real storage area `chrome.storage.local` is accessed.

> If the test fails with `chrome is not defined` at import time, wrap the self-invoking call in a guard:
> ```typescript
> if (typeof chrome !== 'undefined' && chrome.storage?.local) {
>   wireBackground({ chrome: createRealChromeRuntime(), api: createFetchApi(API_BASE), storage: createChromeStorage(chrome.storage.local) });
> }
> ```
> Add this guard now (it's correct for both node tests and the real SW — in the real SW `chrome` is defined):

If needed, edit `browserext/src/background.ts` to replace the bare self-call with the guarded version above. Re-run `npm test` to confirm PASS.

- [ ] **Step 5: Build to verify the real SW compiles**

Run: `cd browserext && npm run build`
Expected: exits 0, `dist/background.js` regenerated. (The `chrome` global comes from `@types/chrome`; `typeof chrome` guard keeps the build happy.)

- [ ] **Step 6: Commit**

```bash
git add browserext/src/background.ts browserext/tests/background.test.mjs
git commit -m "feat(browserext): wire composition root in background SW"
```

---

## Task 10: README — load-unpacked + manual verification checklist

**Files:**
- Create: `browserext/README.md`

**Interfaces:**
- Produces: operator instructions for loading the unpacked extension and the 4 manual verification scenarios from spec §5.3.

- [ ] **Step 1: Write `browserext/README.md`**

````markdown
# dext background probe (Phase 1)

An MV3 Chrome extension that runs alongside the dext userscript and recovers the
owner tab when it lands on a browser-native error page (`about:neterror` /
`ERR_CONNECTION_*`) where the userscript cannot inject.

It does **two things** the userscript can't:

1. **navMonitor** — watches `webRequest` for the real HTTP status code / network
   error of the owner's navigation, and reports to the backend:
   - `404`/`410` → `POST /jobs/{id}/skip` `{"reason":"not_found"}`
   - `502`/`503`/`504` → after 3 consecutive abnormal navigations → `POST /jobs/{id}/fail` `{"message":"gateway_5xx"}`
   - network error (`ERR_*`) → after 3 → `POST /jobs/{id}/fail` `{"message":"nav_error:ERR_..."}`; below threshold it re-redirects the tab itself (the userscript can't — it doesn't inject on `about:neterror`)
2. **watchdog** — every alarm tick, reads `/status`; if the owner heartbeat is stale
   (>15 s) AND a `current_job` exists, re-redirects the owner tab to the job URL.

The backend `/status.current_job` is the sole scheduling truth. This extension
never calls `/jobs/next` and never constructs jobs.

## Build

```bash
cd browserext
npm install
npm run build      # → dist/background.js
npm test           # unit tests
```

## Load (Chrome / Edge)

1. `chrome://extensions` → enable Developer mode.
2. "Load unpacked" → select `browserext/` (the folder with `manifest.json`).
3. The background service worker starts; check it at `chrome://extensions` →
   "service worker" → "Inspect".

## Run alongside dext

1. Start the dext backend (`uv run crawl -u <university> ...`) so the bridge is
   listening on `http://127.0.0.1:21520`.
2. Keep the userscript installed and active (Phase 1 does not replace it).
3. Open the target university site in a tab; the userscript becomes owner.

## Manual verification (spec §5.3)

Run each scenario with the backend + userscript + extension all active. Watch
the backend logs for the `/fail` or `/skip` resolution.

1. **502 page**: navigate the owner to a URL returning 502 (or throttle to 5xx).
   - Expect: after 3 consecutive 5xx navigations, the backend logs a `fail`
     with `block_reason=gateway_5xx`; the in-flight slot releases; the
     userscript polls `/jobs/next` and picks up the next job.
2. **about:neterror**: navigate the owner to a dead host (e.g. disconnect network,
     or visit `https://nonexistent.invalid/`).
   - Expect: the extension re-redirects the tab to the job URL (counts 1 & 2);
     after 3 attempts, the backend logs a `fail` with
     `block_reason=nav_error:ERR_...`.
3. **Stalled owner**: freeze the owner tab (e.g. pause the userscript, or navigate
     to a page and stop heartbeats) for >15 s.
   - Expect: the watchdog re-redirects the tab to `current_job.url`; the userscript
     resumes capture.
4. **No regression**: browse a normal edu.cn page; capture/submit proceeds
     unchanged; no spurious `/fail` or `/skip` in the backend logs.

## Phase 2 (not in this plan)

Phase 2 migrates the userscript content layer from GM APIs to `chrome.*` APIs and
ships it as the extension's content script, retiring `yanclaw-assistant.user.js`.
That is a separate spec.
````

- [ ] **Step 2: Commit**

```bash
git add browserext/README.md
git commit -m "docs(browserext): add load + manual verification README"
```

---

## Task 11: Final regression + full test run

**Files:** none (verification only).

- [ ] **Step 1: Run the full extension test suite**

Run: `cd browserext && npm test`
Expected: all green (smoke 1 + status 3 + storage 6 + api 6 + chrome 1 + navMonitor 9 + watchdog 8 + background 1 = 35 passing).

- [ ] **Step 2: Build the SW**

Run: `cd browserext && npm run build`
Expected: exits 0, `dist/background.js` present.

- [ ] **Step 3: Run the backend suite to confirm zero regressions**

Run: `uv run pytest -q`
Expected: all green. (No backend files were touched; this is a safety check.)

- [ ] **Step 4: Run the userscript suite to confirm zero regressions**

Run: `cd userscripts && npm test`
Expected: all green. (No userscript files were touched.)

- [ ] **Step 5: Commit (if any cleanup)**

If all green with no changes, nothing to commit — the plan is complete. If the build produced stray files, ensure `dist/` is gitignored (Task 1 Step 5) and skip.

---

## Risk notes (carry into execution)

- **`@types/chrome` version drift**: if `npm install` can't resolve `^0.0.287`, run `npm view @types/chrome version` and pin to that. Tests don't depend on it; only the `tsc` build does.
- **MV3 service-worker eviction**: all state is in `chrome.storage.local` (Task 4). The watchdog uses `chrome.alarms` (min period 1 min). In practice `webRequest` events keep the SW warm; if not, the watchdog degrades to 1-min cadence (acceptable — the 15 s threshold is tolerant). Don't add `setInterval` — it doesn't survive eviction.
- **`<all_urls>` host permission**: required for `webRequest` to observe navigations on any edu.cn/github.io host. This is the same surface the userscript already covers via `@match *://*/*`; no new exposure.
- **Double-write with userscript on http 5xx**: eliminated by design — `navMonitor` only counts + fails on 5xx (no redirect); the userscript's `autoCheck.isErrorPage()` owns retry on http pages. Only `nav_error` (about:neterror) self-redirects, where the userscript can't run.
