# Browserext exclusive control — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the Phase-2 toolchain foundation and gated, no-op runtime scaffolding: a `CrawlController` state shape persisted to `chrome.storage.local`, a coarse async mutex, a minimal content-script bundle injected at `<all_urls>` that sets the DOM marker and early-returns on disallowed hosts, the userscript early-exit guard, and the `EXCLUSIVE_CONTROL_ENABLED` build constant — all gated OFF so the build is a safe runtime no-op and the existing Phase-1 tests stay green.

**Architecture:** This slice changes the *shape* of the extension without changing its *behavior*: the existing Phase-1 background probe (`navMonitor`/`watchdog`) keeps running unchanged; the new `CrawlController`, content bundle, and shared RPC types compile and are wired only behind the gate. A new esbuild-based build emits two bundles (background ESM + content IIFE) plus a `.css` text import, replacing the current plain-`tsc` emit. The test harness switches from per-file TS-API transpilation to recursive esbuild bundling so `src/content/**` and `src/controller/**` subdirectories resolve. The `ControllerState` type carries the full amended shape (`PendingRpc`/`NavigationState`/`ControllerError`) even though slice 1 only persists the binding skeleton — later slices fill the behavior.

**Tech Stack:** TypeScript 5.7, MV3 Chrome/Edge (≥110), esbuild (new devDep), node:test (existing), chrome.storage.local (persisted), WebWorker+DOM lib split via `tsconfig.*.json`.

## Global Constraints

Copied verbatim from the spec/amend so every task implicitly inherits them:

- **Gate default false.** Slice 1–5 official builds default `EXCLUSIVE_CONTROL_ENABLED = false`; content script sets no marker, binds nothing, does no heartbeat/claim/navigate. Only unit tests or an explicit test build enable it. (spec §6.1)
- **`<all_urls>` content injection.** Content script `matches` is `["<all_urls>"]`, `run_at: "document_start"`. The marker is set on every page regardless of host; non-allowed-host pages early-return before any RPC/backend call. (spec §5.2, §4.6)
- **Marker is page-priority, not failover.** `<html data-dext-extension-controller="v1">` set at `document_start` before any other action; removed at capture time. Its presence makes the userscript stand down; it does NOT auto-trigger failover. (spec §5.1, §5.3)
- **Allowed fetch hosts: `edu.cn` and `github.io` only.** Dot-boundary matching (reject `notedu.cn` / `evilgithub.io`), mirroring `userscripts/src/hostPolicy.ts` and backend `dext.url_policy.is_allowed_fetch_host`. (spec §5.2, hostPolicy.ts:24-38)
- **Backend HTTP contract is FIXED.** No new/changed endpoints, no DB schema change, no second job state machine. `/status.current_job` remains sole scheduling truth. (CLAUDE.md, spec §1.3, amend §9)
- **Single in-flight + one bound tab.** At most one bound crawl tab; `boundTabId: number | null`. YAGNI: no multi-tab/owner-election logic is reserved. (spec §0.2, §1.1)
- **ControllerState uses the amended type shapes.** `PendingRpc` carries `delivery: 'prepared'|'received'` + `sourceDocumentId`; `NavigationState` carries `requestId/commit/http/pageReady/action` slots; `lastError` is `ControllerError | null` (not `string`). Even though slice 1 only persists binding, the type must match the amend so later slices don't rename. (amend §1.1–§1.3)
- **2s TICK does not unconditionally write storage.** Persist only on actual state change. (spec §2.2)
- **Userscript stays installable but disabled; this slice only adds the early-exit guard.** Full userscript-disable docs come in slice 6. (spec §5.1)
- **One conventional commit per green step.** `feat(spN)`/`test(spN)`/`docs(spN)`/`chore(spN)`. (CLAUDE.md)
- **Browserext tests run with `cd browserext && npm test` (node:test, no pytest).** Relative imports in `src/*.ts` carry `.js` so tsc output resolves in the browser ESM loader. (CLAUDE.md)
- **`dist/` is gitignored** — build-toolchain changes never dirty the tracked tree.
- **Phase-1 `navMonitor.ts`/`status.ts`/`watchdog.ts` are NOT deleted or behaviorally changed in slice 1.** `watchdog` is deleted in slice 2; `status.ts`/`navMonitor.ts` are refactored in slice 3 per amend §3. Slice 1 leaves them byte-identical so the 36 existing tests stay green.

---

## File Structure

New/modified files in this slice. Decomposition rationale: the controller state, the storage persistence, and the async mutex are three separable responsibilities that later slices consume independently — keep them in separate small files.

```
browserext/
  package.json                         # MODIFY: add esbuild devDep, build/test scripts
  tsconfig.json                        # MODIFY → becomes tsconfig.background.json (rename)
  tsconfig.base.json                   # CREATE: shared strict settings
  tsconfig.background.json             # CREATE: extends base, lib ESNext+WebWorker
  tsconfig.content.json                # CREATE: extends base, lib ESNext+DOM+DOM.Iterable
  esbuild.config.mjs                   # CREATE: build script (2 bundles + gate constant + css text loader)
  src/shared/rpc.ts                    # CREATE: shared CsToSw/SwToCs/PanelState/PanelCommand type unions (amend-aligned)
  src/shared/state.ts                  # CREATE: ControllerState/PendingRpc/NavigationState/ControllerError types (amend §1)
  src/shared/hostPolicy.ts             # CREATE: port of isAllowedFetchHost (mirror userscripts/src/hostPolicy.ts:24-38)
  src/controller/mutex.ts             # CREATE: async mutex (acquire/release)
  src/controller/storage.ts            # CREATE: ControllerState load/save to chrome.storage.local (single key)
  src/controller/controller.ts        # CREATE: CrawlController skeleton (state + tick shim, gated, no behavior)
  src/content/index.ts                 # CREATE: content entry — set marker, early-return on disallowed host (gated)
  src/background.ts                    # MODIFY: keep Phase-1 wiring; conditionally wire controller (gate)
  manifest.json                        # MODIFY: add webNavigation perm, content_scripts <all_urls> document_start; remove spike entry
  tests/harness.mjs                    # MODIFY: switch to esbuild recursive bundling
  tests/shared/hostPolicy.test.mjs     # CREATE
  tests/controller/mutex.test.mjs      # CREATE
  tests/controller/storage.test.mjs    # CREATE
  tests/controller/controller.test.mjs # CREATE
  tests/content/index.test.mjs         # CREATE
  tests/build.test.mjs                 # CREATE: asserts dist/background.js + dist/content.js exist + gate off
userscripts/
  src/main.ts                          # MODIFY: add early-exit guard at bootstrap top (before blocked-host check)
docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design.md  # no change
docs/superpowers/specs/2026-06-28-browserext-exclusive-control-design-amend.md  # no change
```

Boundary notes:
- `src/shared/*` must import neither DOM nor WebWorker-only globals — it stays clean under both tsconfigs.
- `src/controller/*` runs in the SW (background) — may use `chrome.*` but not DOM.
- `src/content/*` runs in the page — may use DOM but not `chrome.*` extension APIs directly (it speaks via `chrome.runtime.sendMessage`/`onMessage`, which IS available to content scripts — that's allowed).
- `src/navMonitor.ts`, `src/status.ts`, `src/watchdog.ts`, `src/api.ts`, `src/chrome.ts`, `src/storage.ts` (Phase-1 storage) are **untouched** in slice 1. The Phase-1 `storage.ts` is per-job counter storage; the new `controller/storage.ts` is `ControllerState` persistence — different responsibility, both coexist until Phase-1 storage is retired later.

---

## Task 1: Add esbuild devDependency and split tsconfigs

**Files:**
- Modify: `browserext/package.json`
- Create: `browserext/tsconfig.base.json`
- Create: `browserext/tsconfig.background.json`
- Create: `browserext/tsconfig.content.json`
- Delete (rename away): `browserext/tsconfig.json`

**Interfaces:**
- Consumes: nothing
- Produces: three tsconfigs where `npx tsc -p tsconfig.background.json --noEmit` and `npx tsc -p tsconfig.content.json --noEmit` both succeed (once src files exist; this task only adds configs + a placeholder so typecheck has a target).

- [ ] **Step 1: Write the three tsconfig files**

`browserext/tsconfig.base.json`:
```json
{
  "compilerOptions": {
    "target": "esnext",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "types": ["chrome"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "sourceMap": false,
    "declaration": false,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  }
}
```

`browserext/tsconfig.background.json`:
```json
{
  "extends": "./tsconfig.base.json",
  "compilerOptions": {
    "lib": ["ESNext", "WebWorker"],
    "outDir": "dist"
  },
  "include": ["src/background/**/*.ts", "src/controller/**/*.ts", "src/shared/**/*.ts", "src/navMonitor.ts", "src/status.ts", "src/watchdog.ts", "src/api.ts", "src/chrome.ts", "src/storage.ts", "src/background.ts"]
}
```

`browserext/tsconfig.content.json`:
```json
{
  "extends": "./tsconfig.base.json",
  "compilerOptions": {
    "lib": ["ESNext", "DOM", "DOM.Iterable"],
    "outDir": "dist"
  },
  "include": ["src/content/**/*.ts", "src/shared/**/*.ts"]
}
```

- [ ] **Step 2: Delete the old single tsconfig.json**

Run: `git rm browserext/tsconfig.json`
(The old config had `lib: ["ESNext","WebWorker"]` only — insufficient for content DOM typing; its `include: ["src/**/*.ts"]` is now split across the two new configs.)

- [ ] **Step 3: Update package.json — add esbuild, scripts**

Replace the `scripts` and add `esbuild` to devDependencies in `browserext/package.json`:
```json
{
  "name": "dext-browserext",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "build": "node esbuild.config.mjs",
    "typecheck": "tsc -p tsconfig.background.json --noEmit && tsc -p tsconfig.content.json --noEmit",
    "test": "node --test \"tests/**/*.test.mjs\""
  },
  "devDependencies": {
    "@types/chrome": "^0.0.287",
    "esbuild": "^0.24.0",
    "typescript": "~5.7.0"
  }
}
```

- [ ] **Step 4: Install esbuild**

Run: `cd browserext && npm install`
Expected: `added N packages` (esbuild + platform binary); `package-lock.json` updated. No errors.

- [ ] **Step 5: Verify typecheck still passes against current src (no new src yet)**

Run: `cd browserext && npm run typecheck`
Expected: Both `tsc` invocations succeed with exit 0 (the existing `src/*.ts` files are included in the background config; content config has no `src/content/**` yet — empty include set is valid and typechecks clean).

- [ ] **Step 6: Verify existing tests still pass**

Run: `cd browserext && npm test`
Expected: `tests 36 / pass 36 / fail 0` (unchanged from baseline — harness not yet modified).

- [ ] **Step 7: Commit**

```bash
git add browserext/package.json browserext/package-lock.json \
        browserext/tsconfig.base.json browserext/tsconfig.background.json browserext/tsconfig.content.json
git rm browserext/tsconfig.json
git commit -m "chore(browserext): split tsconfig into base/background/content + add esbuild"
```

---

## Task 2: esbuild build script emitting two bundles + gate constant + CSS text loader

**Files:**
- Create: `browserext/esbuild.config.mjs`

**Interfaces:**
- Consumes: `src/background.ts` (entry), `src/content/index.ts` (entry — created in Task 7; until then this script must still run by skipping the content entry if absent, but Task 7 lands before the build is exercised, so assume both entries exist)
- Produces: `dist/background.js` (ESM), `dist/content.js` (IIFE), with `EXCLUSIVE_CONTROL_ENABLED` injected as `false` by default and overridable via `DEXTC_EXCLUSIVE_CONTROL=1` env for test builds.

- [ ] **Step 1: Write the failing test for the build**

Create `browserext/tests/build.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, rmSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const distBg = join(root, 'dist', 'background.js');
const distContent = join(root, 'dist', 'content.js');

function build() {
  execFileSync(process.execPath, ['esbuild.config.mjs'], { cwd: root, stdio: 'pipe' });
}

test('build emits dist/background.js and dist/content.js', () => {
  rmSync(join(root, 'dist'), { recursive: true, force: true });
  try {
    build();
    assert.ok(existsSync(distBg), 'dist/background.js missing');
    assert.ok(existsSync(distContent), 'dist/content.js missing');
  } finally {
    // leave dist for inspection; gitignored anyway
  }
});

test('default build has EXCLUSIVE_CONTROL_ENABLED = false (gate off)', () => {
  rmSync(join(root, 'dist'), { recursive: true, force: true });
  try {
    build();
    const bg = readFileSync(distBg, 'utf8');
    assert.match(bg, /false/, 'gate must be false in default build');
    assert.doesNotMatch(bg, /EXCLUSIVE_CONTROL_ENABLED\s*=\s*true/);
  } finally {
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test`
Expected: FAIL — `tests/build.test.mjs` errors because `esbuild.config.mjs` does not exist (the `execFileSync` throws ENOENT) and `dist/content.js` doesn't exist.

- [ ] **Step 3: Write the esbuild config**

Create `browserext/esbuild.config.mjs`:
```js
import { build } from 'esbuild';

const GATE = process.env.DEXTC_EXCLUSIVE_CONTROL === '1' ? 'true' : 'false';

const shared = {
  bundle: true,
  target: 'es2022',
  platform: 'browser',
  sourcemap: false,
  minify: false,
  logLevel: 'info',
  define: {
    'EXCLUSIVE_CONTROL_ENABLED': GATE,
  },
};

await Promise.all([
  build({
    ...shared,
    entryPoints: ['src/background.ts'],
    outfile: 'dist/background.js',
    format: 'esm',
  }),
  build({
    ...shared,
    entryPoints: ['src/content/index.ts'],
    outfile: 'dist/content.js',
    format: 'iife',
    loader: { '.css': 'text' },
  }),
]);
```

- [ ] **Step 4: Run test to verify it passes (after Task 7 content entry exists)**

This test cannot pass until `src/content/index.ts` exists (Task 7). Mark it as the gate for Task 7's completion. Run it now to confirm the failure mode is "content entry missing" rather than a config error:
Run: `cd browserext && npm test 2>&1 | grep -A3 build.test`
Expected: FAIL with "Could not resolve src/content/index.ts" (esbuild error) — confirms the config itself is wired; only the entry is missing.

- [ ] **Step 5: Commit (config + test; build test stays red until Task 7, then Task 7 turns it green and commits again)**

```bash
git add browserext/esbuild.config.mjs browserext/tests/build.test.mjs
git commit -m "chore(browserext): esbuild build script with gate constant + css text loader"
```

> Note: The build test is intentionally committed RED here — it is the failing test that drives Task 7's content entry. Task 7's final step re-runs and commits GREEN. This is the TDD RED step for the content bundle.

---

## Task 3: Switch test harness to recursive esbuild bundling

**Files:**
- Modify: `browserext/tests/harness.mjs`

**Interfaces:**
- Consumes: esbuild (Task 1 devDep)
- Produces: `importTsModule(sourcePath, outputName)` with the same signature/return shape as today (`{ mod, cleanup }`), but now bundling the target + all its relative imports recursively via esbuild into a temp `.mjs` file — so `src/content/**` and `src/controller/**` subdirectory imports resolve.

- [ ] **Step 1: Confirm current harness-based tests pass (baseline)**

Run: `cd browserext && npm test`
Expected: `pass 36` (unchanged) — establishes the harness refactor must not regress.

- [ ] **Step 2: Rewrite harness.mjs to use esbuild**

Replace the entire contents of `browserext/tests/harness.mjs` with:
```js
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { build } from 'esbuild';

/** Bundle a TS module (and all relative imports recursively) into a temp .mjs
 *  that Node ESM can load. Replaces the per-file TS-API transpile, which could
 *  not follow imports into src/content/ or src/controller/ subdirectories. */
export async function importTsModule(sourcePath, outputName) {
  const sourceFile = new URL(sourcePath, import.meta.url);
  const outDir = await mkdtemp(join(tmpdir(), 'dext-ext-test-'));
  const requestedOutName = outputName.replace(/\.ts$/, '.mjs');
  const outFile = join(outDir, requestedOutName);

  await build({
    entryPoints: [fileUrl(sourceFile)],
    bundle: true,
    format: 'esm',
    target: 'es2022',
    platform: 'neutral',
    outfile: outFile,
    sourcemap: false,
    write: true,
    logLevel: 'silent',
    // Keep ".js" relative specifiers resolving to their ".ts" sources during bundling.
    resolveExtensions: ['.ts', '.js', '.mjs'],
    define: { 'EXCLUSIVE_CONTROL_ENABLED': 'true' },
  });

  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return {
    mod,
    cleanup: () => rm(outDir, { recursive: true, force: true }),
  };
}

function fileUrl(u) {
  return u.toString().replace(/^file:\/\/\//, '').replace(/\//g, sep()) || u.toString();
}
function sep() {
  return process.platform === 'win32' ? '\\' : '/';
}
```

Wait — the `fileUrl` helper above is fragile on Windows. Use esbuild's native path acceptance instead (esbuild accepts a POSIX/Windows absolute path string directly as an entryPoint on all platforms). Simplify `importTsModule` to:

```js
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

export async function importTsModule(sourcePath, outputName) {
  const sourceAbs = fileURLToPath(new URL(sourcePath, import.meta.url));
  const outDir = await mkdtemp(join(tmpdir(), 'dext-ext-test-'));
  const requestedOutName = outputName.replace(/\.ts$/, '.mjs');
  const outFile = join(outDir, requestedOutName);

  await build({
    entryPoints: [sourceAbs],
    bundle: true,
    format: 'esm',
    target: 'es2022',
    platform: 'neutral',
    outfile: outFile,
    sourcemap: false,
    write: true,
    logLevel: 'silent',
    resolveExtensions: ['.ts', '.js', '.mjs'],
    define: { 'EXCLUSIVE_CONTROL_ENABLED': 'true' },
  });

  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return { mod, cleanup: () => rm(outDir, { recursive: true, force: true }) };
}
```

- [ ] **Step 3: Run all tests to verify the harness refactor is green**

Run: `cd browserext && npm test`
Expected: `tests 36 / pass 36 / fail 0` — the recursive bundler produces the same module surface for every existing test. (The `build.test.mjs` from Task 2 may still fail due to missing content entry — that's expected and tracked by Task 7. If running the full suite, that one failure is the known-red; all 36 harness-based tests must pass.)

- [ ] **Step 4: Commit**

```bash
git add browserext/tests/harness.mjs
git commit -m "test(browserext): switch harness to recursive esbuild bundling"
```

---

## Task 4: Shared host-policy (port of isAllowedFetchHost)

**Files:**
- Create: `browserext/src/shared/hostPolicy.ts`
- Create: `browserext/tests/shared/hostPolicy.test.mjs`

**Interfaces:**
- Consumes: nothing
- Produces: `isAllowedFetchHost(hostname: string): boolean` — mirror of `userscripts/src/hostPolicy.ts:32-38`. Pure, no DOM, no chrome.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/shared/hostPolicy.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('isAllowedFetchHost accepts edu.cn + github.io hosts (dot-boundary)', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/hostPolicy.ts', 'hostPolicy.ts');
  try {
    assert.equal(mod.isAllowedFetchHost('xjtu.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('a.b.xjtu.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('foo.github.io'), true);
    assert.equal(mod.isAllowedFetchHost('github.io'), true);
  } finally { await cleanup(); }
});

test('isAllowedFetchHost rejects non-suffix + lookalikes, accepts any edu.cn/github.io', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/hostPolicy.ts', 'hostPolicy.ts');
  try {
    // isAllowedFetchHost accepts ANY *.edu.cn / *.github.io host — it's the
    // "is this a host we'd ever run on" gate, NOT the same-crawl-site landing
    // gate (spec §3.2 condition 3: xjtu.edu.cn → attacker.edu.cn is rejected
    // by the same-site gate, which is slice 3). So attacker.edu.cn is allowed.
    assert.equal(mod.isAllowedFetchHost('attacker.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('notedu.cn'), false);         // dot-boundary
    assert.equal(mod.isAllowedFetchHost('evilgithub.io'), false);     // dot-boundary
    assert.equal(mod.isAllowedFetchHost('mp.weixin.qq.com'), false);
    assert.equal(mod.isAllowedFetchHost(''), false);
    assert.equal(mod.isAllowedFetchHost('example.com'), false);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern=isAllowedFetchHost 2>&1 | tail -15`
Expected: FAIL — module `../src/shared/hostPolicy.ts` does not exist (ENOENT on entry).

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/shared/hostPolicy.ts`:
```ts
/** Mirrors backend dext.url_policy.is_allowed_fetch_host and
 * userscripts/src/hostPolicy.ts. Deliberately excludes ac.cn and uses
 * dot-boundary matching so notedu.cn / evilgithub.io are rejected.
 * Pure — no DOM, no chrome. Lives in src/shared so both tsconfigs see it. */

const ALLOWED_FETCH_HOST_SUFFIXES = ['edu.cn', 'github.io'];

export function isAllowedFetchHost(hostname: string): boolean {
  const host = (hostname || '').toLowerCase();
  if (!host) return false;
  return ALLOWED_FETCH_HOST_SUFFIXES.some(
    (suffix) => host === suffix || host.endsWith(`.${suffix}`),
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern=isAllowedFetchHost 2>&1 | tail -15`
Expected: PASS — both tests green.

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0 (the new module is under `src/shared/**`, included by both configs, imports nothing DOM/worker-specific).

- [ ] **Step 6: Commit**

```bash
git add browserext/src/shared/hostPolicy.ts browserext/tests/shared/hostPolicy.test.mjs
git commit -m "feat(browserext): port isAllowedFetchHost to src/shared/hostPolicy"
```

---

## Task 5: Shared RPC + state types (amend-aligned)

**Files:**
- Create: `browserext/src/shared/state.ts`
- Create: `browserext/src/shared/rpc.ts`

**Interfaces:**
- Consumes: `FetchJob`, `FetchAction`, `PaginationState`, `PendingDecision` from a shared types module (define inline in rpc.ts, mirroring `userscripts/src/types.ts` — do NOT import from userscripts; the extension is a separate build).
- Produces: `ControllerState`, `PendingRpc`, `NavigationState`, `ControllerError`, `PageDetection`, `RpcOperation`, `ControllerPhase` (state.ts); `CsToSw`, `SwToCs`, `PanelCommand`, `PanelState`, `MessageReceipt` (rpc.ts). These names are the contract later slices depend on — type them exactly per the amend.

- [ ] **Step 1: Write the state types module**

Create `browserext/src/shared/state.ts`:
```ts
/** ControllerState and its sub-shapes — the single chrome.storage.local
 * payload a CrawlController rehydrates from across SW restarts. Shape follows
 * the amendment (§1.1–§1.3) exactly: PendingRpc carries delivery + sourceDocumentId;
 * NavigationState carries requestId/commit/http/pageReady/action slots;
 * lastError is a discriminated ControllerError, never a string. Slice 1 only
 * persists the binding skeleton; later slices fill navigation/pendingRpc. */

import type { FetchJob } from './types.js';

export type ControllerPhase =
  | 'idle' | 'assigned' | 'claiming' | 'navigating' | 'landed'
  | 'acting' | 'capturing' | 'submitting' | 'error';

export type RpcOperation = 'prepare_action' | 'perform_action' | 'capture';

export interface PendingRpc {
  id: string;
  jobId: string;
  op: RpcOperation;
  sourceDocumentId: string;
  delivery: 'prepared' | 'received';
  issuedAt: number;
  resultDeadlineAt: number;
}

export type TerminalUnavailableReason = 'not_found' | 'content_removed' | 'empty_page' | string;

export interface PageDetection {
  errorPage: boolean;
  terminalReason: TerminalUnavailableReason | null;
}

export type NavOutcome =
  | 'ok' | 'not_found' | 'gateway' | 'rate_limited' | 'nav_error' | 'unexpected_status';

export interface NavigationState {
  jobId: string;
  requestedUrl: string;
  issuedAt: number;
  attempt: number;
  kind: 'navigate' | 'form_action';
  sourceDocumentId?: string;
  requestId?: string;
  commit?: {
    documentId: string;
    committedUrl: string;
    committedAt: number;
  };
  http?: {
    requestId: string;
    documentId?: string;
    statusCode?: number;
    outcome: NavOutcome;
    error?: string;
  };
  pageReady?: {
    documentId: string;
    url: string;
    detection: PageDetection;
  };
  action?: {
    expectedEffect: 'new_document' | 'same_document' | 'unknown';
    method: string;
    preparationFingerprint: string;
    invocationReported: boolean;
    effectConfirmed: boolean;
  };
  acceptedUrl?: string;
}

export type ControllerError =
  | {
      kind: 'content_unavailable';
      missing:
        | 'page_ready' | 'capture_result' | 'action_prepare'
        | 'action_result' | 'http_outcome';
      sourceDocumentId: string;
      since: number;
      recoveryAttempts: number;
      nextRecoveryAt: number | null;
      recoveryExhausted: boolean;
    }
  | { kind: 'nav_error'; error: string }
  | { kind: 'gateway_5xx' }
  | { kind: 'unexpected_status'; statusCode: number }
  | { kind: 'rate_limited' };

export interface ControllerState {
  boundTabId: number | null;
  boundAt: number | null;
  connected: boolean;
  autoMode: boolean;
  paused: boolean;
  currentJob: FetchJob | null;
  phase: ControllerPhase;
  phaseStartedAt: number;
  navigation: NavigationState | null;
  pendingRpc: PendingRpc | null;
  backendFailureCount: number;
  nextBackendRetryAt: number | null;
  lastError: ControllerError | null;
}

export function initialControllerState(now: number): ControllerState {
  return {
    boundTabId: null,
    boundAt: null,
    connected: false,
    autoMode: false,
    paused: false,
    currentJob: null,
    phase: 'idle',
    phaseStartedAt: now,
    navigation: null,
    pendingRpc: null,
    backendFailureCount: 0,
    nextBackendRetryAt: null,
    lastError: null,
  };
}
```

- [ ] **Step 2: Write the shared types + RPC types module**

Create `browserext/src/shared/types.ts` (DTOs mirroring `userscripts/src/types.ts` — the extension's own copy; do not import across project boundaries):
```ts
/** DTOs mirroring userscripts/src/types.ts and the backend types.ts (dext.types).
 *  The extension vendors its own copy so its build is independent. */

export type JobStatus = 'pending' | 'assigned' | 'completed' | 'failed' | 'skipped';

export interface JobContext {
  university_name: string;
  agent_state: string;
  intent: string;
  parent_url: string;
  depth: number;
  org_unit_name: string;
  hints: string[];
}

export interface FetchAction {
  kind: string;
  form_name?: string;
  fields?: Record<string, string>;
  submit?: boolean;
  synthetic_url?: string;
  label?: string;
  page_index?: number;
  state_id?: string;
}

export interface PaginationState {
  kind: 'form_submit';
  state_id: string;
  label: string;
  page_index: number;
  total_pages?: number;
  form_name: string;
  fields: Record<string, string>;
  submit: boolean;
  synthetic_url: string;
  url: string;
}

export interface FetchJob {
  id: string;
  url: string;
  status: JobStatus;
  context: JobContext;
  created_at: string;
  timeout_seconds: number;
  action?: FetchAction | null;
  identity_url?: string | null;
}

export interface PendingDecision {
  id: string;
  kind: string;
  org_unit_name: string;
  failure_count: number;
  sample_urls: string[];
  suggested_action: string;
  status: string;
  action?: string | null;
  created_at: string;
  resolved_at?: string | null;
}
```

Create `browserext/src/shared/rpc.ts`:
```ts
/** CS↔SW RPC contract (spec §4.2, amend §7). Shared by both tsconfigs;
 *  imports only types, never DOM or chrome. PanelState.lastError is
 *  ControllerError | null (amend §7) — the content panel renders the
 *  human-readable text, not the SW. */

import type { ControllerError, ControllerPhase, TerminalUnavailableReason } from './state.js';
import type { FetchAction, FetchJob, PaginationState, PendingDecision } from './types.js';

export type PanelCommand =
  | { kind: 'bind' }
  | { kind: 'unbind' }
  | { kind: 'set_auto'; value: boolean }
  | { kind: 'set_paused'; value: boolean }
  | { kind: 'open' }
  | { kind: 'submit' }
  | { kind: 'skip'; reason?: string }
  | { kind: 'fail'; message?: string }
  | { kind: 'override'; url: string }
  | { kind: 'decision'; id: string; action: string };

export type CsToSw =
  | { op: 'REGISTER'; url: string }
  | { op: 'TICK' }
  | {
      op: 'PAGE_READY';
      url: string;
      title: string;
      detection: { errorPage: boolean; terminalReason: TerminalUnavailableReason | null };
    }
  | {
      op: 'CAPTURE_RESULT';
      rpcId: string; jobId: string; ok: boolean; url: string;
      html?: string; title?: string; paginationStates?: PaginationState[];
      detection?: { errorPage: boolean; terminalReason: TerminalUnavailableReason | null };
      error?: string;
    }
  | {
      op: 'ACTION_RESULT';
      rpcId: string; jobId: string; ok: boolean;
      invoked: boolean; navigationExpected: boolean; effectApplied: boolean;
      error?: string;
    }
  | { op: 'COMMAND'; command: PanelCommand };

export type SwToCs =
  | { op: 'STATE_CHANGED'; state: PanelState }
  | { op: 'CAPTURE'; rpcId: string; jobId: string }
  | {
      op: 'PREPARE_ACTION';
      rpcId: string; jobId: string; action: FetchAction;
    }
  | {
      op: 'PERFORM_ACTION';
      rpcId: string; jobId: string; action: FetchAction;
      preparationFingerprint: string;
    }
  | { op: 'TOAST'; message: string; kind?: 'info' | 'error' };

export interface PanelState {
  isBoundTab: boolean;
  bound: boolean;
  connected: boolean;
  autoMode: boolean;
  paused: boolean;
  phase: ControllerPhase;
  currentJob: FetchJob | null;
  navigationAttempt: number;
  lastError: ControllerError | null;
  pendingDecision: PendingDecision | null;
}

export interface MessageReceipt {
  received: true;
  state?: PanelState;
}
```

- [ ] **Step 3: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0 — all three new shared modules compile under both configs (they import only from each other within `src/shared/`, which is included by both tsconfigs).

- [ ] **Step 4: Write a type-assertion test that the initial state has the gated shape**

Create `browserext/tests/shared/state.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('initialControllerState is unbound idle with null navigation/pendingRpc/lastError', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/state.ts', 'state.ts');
  try {
    const s = mod.initialControllerState(1000);
    assert.equal(s.boundTabId, null);
    assert.equal(s.phase, 'idle');
    assert.equal(s.navigation, null);
    assert.equal(s.pendingRpc, null);
    assert.equal(s.lastError, null);
    assert.equal(s.connected, false);
  } finally { await cleanup(); }
});
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern=initialControllerState 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/shared/state.ts browserext/src/shared/types.ts browserext/src/shared/rpc.ts \
        browserext/tests/shared/state.test.mjs
git commit -m "feat(browserext): vendored shared DTOs + amend-aligned ControllerState/RPC types"
```

---

## Task 6: Async mutex (controller single-flight)

**Files:**
- Create: `browserext/src/controller/mutex.ts`
- Create: `browserext/tests/controller/mutex.test.mjs`

**Interfaces:**
- Consumes: nothing
- Produces: `createMutex()` → `{ acquire(): Promise<() => void> }`. The returned function releases the lock. Reentrancy not supported (the Controller never re-acquires under itself). Errors thrown inside the critical section must still release the lock.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/mutex.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('mutex serializes: second acquire waits for first release', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/mutex.ts', 'mutex.ts');
  try {
    const m = mod.createMutex();
    const order = [];
    const release1 = await m.acquire();
    let p2Resolved = false;
    const p2 = m.acquire().then((r) => { p2Resolved = true; order.push('second'); return r; });
    // give the microtask queue a chance — p2 must NOT have acquired yet
    await Promise.resolve();
    assert.equal(p2Resolved, false, 'second acquire must wait');
    order.push('first-release');
    release1();
    const release2 = await p2;
    release2();
    assert.deepEqual(order, ['first-release', 'second']);
  } finally { await cleanup(); }
});

test('mutex releases on thrown error so next acquire is not deadlocked', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/mutex.ts', 'mutex.ts');
  try {
    const m = mod.createMutex();
    const release = await m.acquire();
    // simulate a critical section that throws; release() in finally must still run
    let threw = false;
    try {
      try {
        throw new Error('boom');
      } finally {
        release();
      }
    } catch {
      threw = true;
    }
    assert.equal(threw, true, 'error propagated as expected');
    // the real contract: the lock is released, so the next acquire must not hang
    const release2 = await m.acquire();
    release2();
  } finally {
    await cleanup();
  }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='mutex' 2>&1 | tail -15`
Expected: FAIL — module `../src/controller/mutex.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/mutex.ts`:
```ts
/** Coarse async mutex serializing Controller ticks, UI commands, and nav events.
 *  The critical section is sub-second; long waits (capture) happen outside the
 *  lock (spec §2.2). The returned release fn must be called exactly once;
 *  callers use try/finally. Not reentrant — the Controller never re-acquires
 *  under itself. */

export interface Mutex {
  acquire(): Promise<() => void>;
}

export function createMutex(): Mutex {
  let locked = false;
  const waiters: Array<() => void> = [];

  function dispatchNext(): void {
    if (locked || waiters.length === 0) return;
    const next = waiters.shift();
    if (!next) return;
    locked = true;
    next();
  }

  return {
    acquire() {
      return new Promise<() => void>((resolve) => {
        const run = () => resolve(release);
        if (locked) {
          waiters.push(run);
        } else {
          locked = true;
          run();
        }
      });
    },
  };

  function release(): void {
    locked = false;
    dispatchNext();
  }
}
```

> Note: `release` is a function declaration hoisted above its use in the `acquire` closure — valid in JS/TS. Keep it that way to avoid TDZ; do not convert to a `const` arrow assigned after `acquire`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='mutex' 2>&1 | tail -15`
Expected: PASS — both tests green.

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/mutex.ts browserext/tests/controller/mutex.test.mjs
git commit -m "feat(browserext): coarse async mutex for Controller single-flight"
```

---

## Task 7: ControllerState storage persistence (single key, change-gated writes)

**Files:**
- Create: `browserext/src/controller/storage.ts`
- Create: `browserext/tests/controller/storage.test.mjs`

**Interfaces:**
- Consumes: `ControllerState`, `initialControllerState` from `../shared/state.js` (Task 5); an injectable `StorageArea` shaped like `chrome.storage.local`.
- Produces: `createControllerStorage(area): ControllerStorage` where:
  ```ts
  interface ControllerStorage {
    load(): Promise<ControllerState>;                       // returns initialControllerState(Date.now()) if absent
    save(state: ControllerState): Promise<void>;            // overwrites the single key
    saveIfChanged(prev: ControllerState, next: ControllerState): Promise<boolean>;  // no-op write if JSON-equal; returns whether it wrote
    clear(): Promise<void>;
  }
  ```
  Single storage key: `dext_controller_state_v1`.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/storage.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function fakeArea() {
  const store = new Map();
  return {
    async get(keys) {
      if (keys === null) {
        const obj = {}; for (const [k, v] of store) obj[k] = v; return obj;
      }
      const arr = Array.isArray(keys) ? keys : [keys];
      const obj = {}; for (const k of arr) if (store.has(k)) obj[k] = store.get(k);
      return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) {
      const arr = Array.isArray(keys) ? keys : [keys];
      for (const k of arr) store.delete(k);
    },
    _dump() { return Object.fromEntries(store); },
  };
}

test('load returns initial state when storage empty', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const cs = mod.createControllerStorage(fakeArea());
    const s = await cs.load();
    assert.equal(s.boundTabId, null);
    assert.equal(s.phase, 'idle');
    assert.equal(s.navigation, null);
  } finally { await cleanup(); }
});

test('save then load round-trips a bound state', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const area = fakeArea();
    const cs = mod.createControllerStorage(area);
    const s = await cs.load();
    s.boundTabId = 7;
    s.boundAt = 1234;
    s.phase = 'assigned';
    await cs.save(s);
    const reloaded = await cs.load();
    assert.equal(reloaded.boundTabId, 7);
    assert.equal(reloaded.boundAt, 1234);
    assert.equal(reloaded.phase, 'assigned');
  } finally { await cleanup(); }
});

test('saveIfChanged writes only when state differs', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const area = fakeArea();
    const cs = mod.createControllerStorage(area);
    const s = await cs.load();
    s.boundTabId = 5;
    let wrote = await cs.saveIfChanged(await cs.load(), s);
    assert.equal(wrote, true, 'first save writes');
    wrote = await cs.saveIfChanged(s, { ...s });   // JSON-equal
    assert.equal(wrote, false, 'identical state does not write');
    const s2 = { ...s, paused: true };
    wrote = await cs.saveIfChanged(s, s2);
    assert.equal(wrote, true, 'changed state writes');
  } finally { await cleanup(); }
});

test('clear removes the controller key', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const area = fakeArea();
    const cs = mod.createControllerStorage(area);
    const s = await cs.load();
    s.boundTabId = 9;
    await cs.save(s);
    await cs.clear();
    const reloaded = await cs.load();
    assert.equal(reloaded.boundTabId, null);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='load returns initial' 2>&1 | tail -10`
Expected: FAIL — `../src/controller/storage.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/storage.ts`:
```ts
/** Persists ControllerState to a single chrome.storage.local key so a SW can
 *  rehydrate across restarts. Change-gated writes (saveIfChanged) prevent the
 *  2s TICK from blindly writing storage every tick (spec §2.2 write-frequency
 *  invariant). Injectable area for tests. */

import { initialControllerState } from '../shared/state.js';
import type { ControllerState } from '../shared/state.js';

const STORAGE_KEY = 'dext_controller_state_v1';

export interface StorageArea {
  get(keys: string | string[] | null): Promise<Record<string, unknown>>;
  set(obj: Record<string, unknown>): Promise<void>;
  remove(keys: string | string[]): Promise<void>;
}

export interface ControllerStorage {
  load(): Promise<ControllerState>;
  save(state: ControllerState): Promise<void>;
  saveIfChanged(prev: ControllerState, next: ControllerState): Promise<boolean>;
  clear(): Promise<void>;
}

export function createControllerStorage(area: StorageArea): ControllerStorage {
  async function load(): Promise<ControllerState> {
    const obj = await area.get(STORAGE_KEY);
    const v = obj[STORAGE_KEY];
    if (v && typeof v === 'object') {
      // shallow-validate the persisted shape; deeper validation is the Controller's job
      return structuredClone(v) as ControllerState;
    }
    return initialControllerState(Date.now());
  }

  async function save(state: ControllerState): Promise<void> {
    await area.set({ [STORAGE_KEY]: state });
  }

  async function saveIfChanged(prev: ControllerState, next: ControllerState): Promise<boolean> {
    if (JSON.stringify(prev) === JSON.stringify(next)) return false;
    await area.set({ [STORAGE_KEY]: next });
    return true;
  }

  async function clear(): Promise<void> {
    await area.remove(STORAGE_KEY);
  }

  return { load, save, saveIfChanged, clear };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='load returns initial\|round-trips\|saveIfChanged\|clear removes' 2>&1 | tail -15`
Expected: PASS — all four tests green.

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/storage.ts browserext/tests/controller/storage.test.mjs
git commit -m "feat(browserext): ControllerState persistence (single key, change-gated writes)"
```

---

## Task 8: CrawlController skeleton (state + tick shim, gated, no behavior)

**Files:**
- Create: `browserext/src/controller/controller.ts`
- Create: `browserext/tests/controller/controller.test.mjs`

**Interfaces:**
- Consumes: `ControllerStorage` (Task 7), `Mutex` (Task 6), `ControllerState`/`initialControllerState` (Task 5). The global `EXCLUSIVE_CONTROL_ENABLED` build constant (default `false`).
- Produces: `createCrawlController({ storage }): CrawlController` where:
  ```ts
  interface CrawlController {
    tick(now?: number): Promise<void>;   // gated no-op when EXCLUSIVE_CONTROL_ENABLED is false
    getState(): Promise<ControllerState>;
    bind(tabId: number, now?: number): Promise<void>;     // gated no-op
  }
  ```
  When the gate is **off**, `tick`/`bind` load persisted state, return immediately, and persist nothing new. When the gate is **on** (test build), `bind` sets `boundTabId`/`boundAt`/`phase='assigned'` and persists — this is the only behavior slice 1 needs to assert the gate flips correctly.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/controller/controller.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

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

// The test harness injects EXCLUSIVE_CONTROL_ENABLED = true (see harness.mjs define),
// so these tests assert the GATED-ON path. The build.test.mjs asserts the default
// official build is gated OFF.

test('bind sets boundTabId/phase=assigned and persists (gate ON)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area) });
    await c.bind(42, 1000);
    const s = await c.getState();
    assert.equal(s.boundTabId, 42);
    assert.equal(s.boundAt, 1000);
    assert.equal(s.phase, 'assigned');
    // persisted
    const persisted = (await area.get('dext_controller_state_v1')).dext_controller_state_v1;
    assert.equal(persisted.boundTabId, 42);
  } finally { await cleanup(); }
});

test('tick with no currentJob and idle does not navigate or claim (gate ON, slice-1 no-op)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area) });
    await c.tick(2000);
    const s = await c.getState();
    assert.equal(s.phase, 'idle');
    assert.equal(s.currentJob, null);
  } finally { await cleanup(); }
});

test('initial state is unbound idle', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(fakeArea()) });
    const s = await c.getState();
    assert.equal(s.boundTabId, null);
    assert.equal(s.phase, 'idle');
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='bind sets boundTabId\|tick with no currentJob\|initial state is unbound' 2>&1 | tail -15`
Expected: FAIL — `../src/controller/controller.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/controller/controller.ts`:
```ts
/** CrawlController — the single logical orchestrator owning all client-side
 *  navigation + backend I/O (spec §2). Slice 1 ships ONLY the skeleton: state
 *  load/save via the mutex, plus a gated bind. No claim, no navigate, no RPC,
 *  no /status reconciliation yet — those are slices 2–4. The gate constant
 *  EXCLUSIVE_CONTROL_ENABLED (injected by esbuild, default false) makes the
 *  official build a runtime no-op: bind/tick load state and return. */

import { createMutex } from './mutex.js';
import type { Mutex } from './mutex.js';
import { createControllerStorage } from './storage.js';
import type { ControllerStorage, StorageArea } from './storage.js';
import type { ControllerState } from '../shared/state.js';

// Re-export so callers (background.ts, tests) can build a storage + controller
// from a single import entry point.
export { createControllerStorage };
export type { ControllerStorage, StorageArea };

declare const EXCLUSIVE_CONTROL_ENABLED: boolean;

export interface CrawlControllerDeps {
  storage?: ControllerStorage;
  area?: StorageArea;
}

export interface CrawlController {
  tick(now?: number): Promise<void>;
  bind(tabId: number, now?: number): Promise<void>;
  getState(): Promise<ControllerState>;
}

export function createCrawlController(deps: CrawlControllerDeps = {}): CrawlController {
  const storage: ControllerStorage = deps.storage ?? createControllerStorage(deps.area ?? inMemoryArea());
  const mutex: Mutex = createMutex();
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
        await ensureLoaded();
        if (!EXCLUSIVE_CONTROL_ENABLED) return;          // gated no-op
        // slice 1: no claim, no navigate, no reconcile. Idle stays idle.
        // (slices 2–4 fill the tick body.)
        void now;
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

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='bind sets boundTabId\|tick with no currentJob\|initial state is unbound' 2>&1 | tail -15`
Expected: PASS — all three tests green (the harness injects `EXCLUSIVE_CONTROL_ENABLED = true`, so the gated-ON path is exercised).

- [ ] **Step 5: Typecheck**

Run: `cd browserext && npm run typecheck`
Expected: exit 0. (`declare const EXCLUSIVE_CONTROL_ENABLED: boolean;` satisfies both tsc and the esbuild `define`.)

- [ ] **Step 6: Commit**

```bash
git add browserext/src/controller/controller.ts browserext/tests/controller/controller.test.mjs
git commit -m "feat(browserext): CrawlController skeleton with gated bind (slice 1)"
```

---

## Task 9: Minimal content-script bundle (marker + early-return, gated)

**Files:**
- Create: `browserext/src/content/index.ts`
- Create: `browserext/tests/content/index.test.mjs`

**Interfaces:**
- Consumes: `isAllowedFetchHost` from `../shared/hostPolicy.js` (Task 4); the global `EXCLUSIVE_CONTROL_ENABLED` build constant (default `false`).
- Produces: a content entry that, on `document_start`:
  1. If `EXCLUSIVE_CONTROL_ENABLED` is false → return immediately (no marker, no RPC, no side effect). The official slice-1 build touches nothing.
  2. If true → set `<html data-dext-extension-controller="v1">` immediately (before any other action), then if the page host is NOT an allowed fetch host → return (no panel, no RPC). Allowed host → (slice 1) stop here too; full lifecycle (PAGE_READY/panel) is slice 4–5.

  Exports `bootstrapContent(opts?)` for testability, taking an injectable `{ hostname, documentElement, now }` so tests don't need a real DOM.

- [ ] **Step 1: Write the failing test**

Create `browserext/tests/content/index.test.mjs`:
```js
import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// harness injects EXCLUSIVE_CONTROL_ENABLED = true.

function fakeDoc(hostname) {
  const attrs = new Map();
  const el = {
    getAttribute: (n) => (attrs.has(n) ? attrs.get(n) : null),
    setAttribute: (n, v) => attrs.set(n, v),
    removeAttribute: (n) => attrs.delete(n),
    _has: (n) => attrs.has(n),
    _get: (n) => attrs.get(n),
  };
  return { el, hostname };
}

test('gated ON: sets data-dext-extension-controller=v1 on allowed host', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('xjtu.edu.cn');
    await mod.bootstrapContent({ hostname: doc.hostname, documentElement: doc.el, now: 1000 });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
  } finally { await cleanup(); }
});

test('gated ON: sets marker even on disallowed host, then early-returns (no panel/RPC flag set)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('mp.weixin.qq.com');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    // marker is set on EVERY page (spec §5.2), but disallowed host must not proceed
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, false, 'disallowed host must early-return before onAllowedHost');
  } finally { await cleanup(); }
});

test('gated ON: allowed host proceeds to onAllowedHost hook (slice 1 stops there)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('foo.github.io');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, true);
  } finally { await cleanup(); }
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd browserext && npm test -- --test-name-pattern='gated ON' 2>&1 | tail -15`
Expected: FAIL — `../src/content/index.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `browserext/src/content/index.ts`:
```ts
/** Content-script entry (spec §4, §5.2). Runs at document_start on <all_urls>.
 *  GATED: when EXCLUSIVE_CONTROL_ENABLED is false (official slice 1–5 builds),
 *  it does nothing — no marker, no RPC, no backend call. When true, it sets the
 *  data-dext-extension-controller marker on <html> FIRST (before any other
 *  action; spec §4.6), then early-returns on disallowed hosts (no panel/RPC).
 *  Slice 1 stops at the marker + host gate; PAGE_READY/panel/capture are
 *  slices 4–5. bootstrapContent is exported with an injectable opts object so
 *  tests don't need a real DOM. */

import { isAllowedFetchHost } from '../shared/hostPolicy.js';

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
  /** Called only on allowed fetch hosts, after the marker is set. Slice 1
   *  does nothing here; slices 4–5 hang PAGE_READY/panel/capture off it. */
  onAllowedHost?: () => void;
}

export async function bootstrapContent(deps: ContentDeps): Promise<void> {
  // GATED: official slice 1–5 builds define EXCLUSIVE_CONTROL_ENABLED=false, so this
  // whole block is dead — no marker, no RPC, no backend. Wrap the body in the
  // positive form `if (GATE) {...}` (NOT `if (!GATE) return`) so esbuild emits
  // `if (false) {...}` and the body is skipped at runtime even un-minified.
  if (EXCLUSIVE_CONTROL_ENABLED) {
    // 1. Marker FIRST, before any other action (spec §4.6).
    deps.documentElement.setAttribute(MARKER_ATTR, MARKER_VALUE);
    // 2. Early-return on disallowed host — no panel, no RPC, no backend (spec §5.2).
    if (!isAllowedFetchHost(deps.hostname)) return;
    // 3. Allowed host: slice 1 stops here. Slices 4–5 add the lifecycle.
    deps.onAllowedHost?.();
  }
}

// Browser entry: wire to the real document. Guarded so the module is importable
// in node tests (where document is undefined).
if (typeof document !== 'undefined' && document.documentElement) {
  void bootstrapContent({
    hostname: typeof location !== 'undefined' ? location.hostname : '',
    documentElement: document.documentElement,
    now: Date.now(),
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd browserext && npm test -- --test-name-pattern='gated ON' 2>&1 | tail -15`
Expected: PASS — all three tests green.

- [ ] **Step 5: Typecheck (content config)**

Run: `cd browserext && npm run typecheck`
Expected: exit 0 (both configs; content config now has `src/content/index.ts` to compile, `lib` includes DOM).

- [ ] **Step 6: Run the build test from Task 2 — it should now go GREEN**

Run: `cd browserext && npm test -- --test-name-pattern='build emits\|gate off' 2>&1 | tail -15`
Expected: PASS — `dist/background.js` and `dist/content.js` exist; the default build (gate off) has `EXCLUSIVE_CONTROL_ENABLED` replaced with `false`.

- [ ] **Step 7: Commit**

```bash
git add browserext/src/content/index.ts browserext/tests/content/index.test.mjs
git commit -m "feat(browserext): minimal gated content bundle — marker + host early-return"
```

---

## Task 10: Wire Controller + content into background.ts (gated), update manifest

**Files:**
- Modify: `browserext/src/background.ts`
- Modify: `browserext/manifest.json`

**Interfaces:**
- Consumes: `createCrawlController` (Task 8). The gate constant.
- Produces: `background.ts` still wires Phase-1 `navMonitor`/`watchdog` unconditionally (they keep working), and additionally constructs the `CrawlController` (gated — when off, its `tick`/`bind` are no-ops). Manifest gains `webNavigation` permission and the `<all_urls>` `document_start` content script; the spike entry is removed.

- [ ] **Step 1: Update background.ts to construct the controller (gated)**

Edit `browserext/src/background.ts` — keep all Phase-1 wiring, add the controller. Replace the `wireBackground` function body's return and the self-invoke block. Add imports after the existing ones:

Add these imports (after the watchdog import, line ~17):
```ts
import { createCrawlController } from './controller/controller.js';
import type { CrawlController } from './controller/controller.js';
import { createControllerStorage } from './controller/storage.js';
```

Change the `WireDeps` interface and `wireBackground` signature to also return the controller:
```ts
export interface WireDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export function wireBackground(deps: WireDeps): {
  navMonitor: NavMonitor;
  watchdog: Watchdog;
  controller: CrawlController;
} {
  const navMonitor = createNavMonitor(deps);
  const watchdog = createWatchdog(deps);
  const controller = createCrawlController({
    storage: createControllerStorage(chrome.storage.local as unknown as import('./controller/storage.js').StorageArea),
  });
  navMonitor.start();
  watchdog.start();
  // Controller.tick is wired into the slice-2 reconciliation alarm; slice 1
  // only constructs it (gated, so tick/bind are no-ops in the official build).
  return { navMonitor, watchdog, controller };
}
```

Update the self-invoke block to also pass `chrome.storage.local`:
```ts
if (typeof chrome !== 'undefined' && chrome.storage?.local) {
  wireBackground({
    chrome: createRealChromeRuntime(),
    api: createFetchApi(API_BASE),
    storage: createChromeStorage(chrome.storage.local),
  });
}
```

(The `chrome.storage.local as unknown as StorageArea` cast is acceptable here — Phase-1 `createChromeStorage` already wraps the same area; the controller's `StorageArea` interface is structurally compatible. A cleaner shared area type is a slice-2 refactor, out of scope.)

- [ ] **Step 2: Update the background test to expect the controller in the return**

Edit `browserext/tests/background.test.mjs` — the destructuring now takes `controller` too. Update the assertion:
```js
const { navMonitor, watchdog, controller } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi, storage: fakeStorage });
// ... existing start assertions ...
assert.ok(controller, 'controller constructed');
assert.equal(typeof controller.tick, 'function');
assert.equal(typeof controller.bind, 'function');
```

- [ ] **Step 3: Update manifest.json — add webNavigation perm, content script, remove spike**

Replace `browserext/manifest.json` with:
```json
{
  "manifest_version": 3,
  "name": "dext crawl controller",
  "version": "0.1.0",
  "description": "Exclusive-control crawl orchestrator (Phase 2). Gated off by default in slice 1; runs alongside the dext userscript.",
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

- [ ] **Step 4: Run all tests + typecheck + build**

Run: `cd browserext && npm run typecheck && npm run build && npm test`
Expected:
- typecheck exit 0.
- build emits `dist/background.js` + `dist/content.js` (gate off).
- all tests pass, including the updated `background.test.mjs` and the `build.test.mjs`.

- [ ] **Step 5: Commit**

```bash
git add browserext/src/background.ts browserext/tests/background.test.mjs browserext/manifest.json
git commit -m "feat(browserext): construct gated CrawlController in background; manifest +content/webNavigation"
```

---

## Task 11: Userscript early-exit guard

**Files:**
- Modify: `userscripts/src/main.ts`
- Modify: `userscripts/dist/yanclaw-assistant.user.js` (rebuilt — this IS tracked, unlike browserext/dist)

**Interfaces:**
- Consumes: the `<html data-dext-extension-controller="v1">` marker set by the content script (Task 9).
- Produces: the userscript's `bootstrap()` returns immediately when the marker is present, before the blocked-host/top-frame check. This is the spec §5.1 guard — the userscript stands down when the extension owns the tab.

- [ ] **Step 1: Write the failing test (userscript side)**

Check how userscript tests are run first:
Run: `cd userscripts && cat package.json 2>/dev/null | head -30` (or `ls userscripts/tests 2>/dev/null`)
If userscript tests exist and run via node:test over transpiled TS, add a test mirroring the browserext harness. If the userscript has NO test runner, skip the failing-test step and verify by build + manual reasoning (document this in the commit body).

Assuming a harness exists (verify first), create the test asserting: a fake `document.documentElement` with `data-dext-extension-controller="v1"` causes `bootstrap` to return before mounting. If no harness, proceed to Step 2 and verify via build.

- [ ] **Step 2: Add the guard to userscripts/src/main.ts**

Edit `userscripts/src/main.ts` — insert at the very top of `bootstrap()`, before `isAssistantBlockedHost`:
```ts
async function bootstrap(): Promise<void> {
  // Phase-2 exclusive-control marker: if the dext extension owns this tab,
  // stand down immediately (spec §5.1). The marker is page-priority, not a
  // failover signal — its presence means the extension's content script is
  // already running here.
  if (
    document.documentElement?.getAttribute('data-dext-extension-controller') === 'v1'
  ) {
    return;
  }
  if (isAssistantBlockedHost() || !isTopFrame()) return;
  await waitForBody();
  if (isAllowedFetchHost()) {
    await fullBootstrap();
    return;
  }
  await lightweightRedirectBootstrap();
}
```

- [ ] **Step 3: Rebuild the userscript bundle**

Run: `cd userscripts && npm run build` (or the userscript's build command — verify via its package.json).
Expected: `userscripts/dist/yanclaw-assistant.user.js` regenerated with the guard at the top of the bootstrap function.

- [ ] **Step 4: Verify the guard is present in the built user.js**

Run: `grep -n "data-dext-extension-controller" userscripts/dist/yanclaw-assistant.user.js`
Expected: at least one hit inside the bootstrap function.

- [ ] **Step 5: Run userscript tests (if any)**

Run: `cd userscripts && npm test` (if a test script exists).
Expected: PASS (the guard is additive; existing tests don't set the marker so behavior is unchanged).

- [ ] **Step 6: Commit**

```bash
git add userscripts/src/main.ts userscripts/dist/yanclaw-assistant.user.js
git commit -m "feat(userscripts): early-exit guard when extension marker present (spec §5.1)"
```

---

## Task 12: Slice-1 acceptance — full suite green + gate-off verification

**Files:**
- No new files; this is the verification gate.

- [ ] **Step 1: Clean build + full test suite**

Run: `cd browserext && rm -rf dist && npm install && npm run typecheck && npm run build && npm test`
Expected:
- `npm install` succeeds (esbuild installed).
- typecheck exit 0 (both configs).
- build emits `dist/background.js` + `dist/content.js`.
- all tests pass (36 original + new slice-1 tests; the `build.test.mjs` confirms gate off).

- [ ] **Step 2: Confirm the default build is gated off (marker not set at runtime)**

Verify the gate constant is `false` in both bundles:
Run: `grep -c "EXCLUSIVE_CONTROL_ENABLED" browserext/dist/background.js browserext/dist/content.js`
Expected: 0 hits — esbuild `define` substitutes the literal `false` inline, so the identifier no longer appears; the branches referencing it are dead-code-eliminated or guarded by `false`. (If esbuild leaves the name, confirm the value is `false`: `grep "false" ...`.)

- [ ] **Step 3: Confirm Phase-1 behavior is unchanged (regression)**

Run: `cd browserext && npm test 2>&1 | grep -E "tests|pass|fail"`
Expected: `fail 0`. The Phase-1 `navMonitor`/`watchdog`/`status`/`storage`/`chrome`/`api` tests are untouched and green.

- [ ] **Step 4: Confirm Python suite is unaffected**

Run: `uv run pytest -q` (from repo root)
Expected: PASS (slice 1 touches only `browserext/` + `userscripts/src/main.ts` + rebuilt user.js; no Python changed). If any Python test fails, it is pre-existing and unrelated — note it but do not fix in this slice.

- [ ] **Step 5: Final commit (if any uncommitted acceptance artifacts; else skip)**

If the acceptance run produced no further changes, no commit. If a README/CLAUDE.md note is wanted for "slice 1 landed, gate off", add it here:
```bash
# Only if adding docs:
git add browserext/README.md
git commit -m "docs(browserext): note slice-1 foundation landed (gate off, Phase-1 probe intact)"
```

- [ ] **Step 6: Update memory with the slice-1 landing fact**

Write a project memory recording that slice 1 of the exclusive-control Phase 2 is landed (gated off), what the next slices are, and that `EXCLUSIVE_CONTROL_ENABLED` is the gate. This orients future sessions without re-deriving from git.

---

## Self-Review (completed by plan author)

**1. Spec coverage (slice-1 slice of §6.2 + amend §8.2 slice-1 row):**
- Toolchain (esbuild IIFE, split tsconfigs, CSS text-loader, recursive test harness) → Tasks 1, 2, 3. ✓
- Shared RPC/types → Task 5. ✓ (amend §8.2 slice 1: shared types use amended `PendingRpc`/`NavigationState`/`ControllerError` + PanelState transport response — all present in state.ts/rpc.ts.)
- `ControllerState` + coarse mutex + `chrome.storage.local` persistence → Tasks 5, 6, 7, 8. ✓
- Minimal content lifecycle (`<all_urls>` injection, set marker, non-allowed-host early-return) → Task 9. ✓ (manifest `<all_urls>` `document_start` in Task 10.)
- Userscript early-exit guard → Task 11. ✓
- Enable-gate constant → Tasks 2, 8, 9 (default `false`). ✓
- Gate off (no-op) → verified Task 12 step 2. ✓
- Phase-1 code untouched / `watchdog` NOT deleted this slice (spec says slice 2) → Task 10 keeps it; global constraint states it. ✓

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Task 11 step 1 has a conditional (verify userscript test harness first) with explicit fallback — that's a real instruction, not a placeholder. All code blocks contain complete code.

**3. Type consistency:**
- `createControllerStorage(area)` returns `ControllerStorage` with `load/save/saveIfChanged/clear` — used identically in Task 8's `createCrawlController`. ✓
- `CrawlController.tick(now?)`, `bind(tabId, now?)`, `getState()` — used in Task 10's background wiring and Task 8's tests. ✓
- `bootstrapContent(deps: ContentDeps)` with `onAllowedHost` hook — Task 9 test and impl match. ✓
- `initialControllerState(now)` — used in Task 7 storage load + Task 5 + Task 8. ✓
- `Mutex.acquire(): Promise<() => void>` — Task 6 impl + test + Task 8 usage consistent. ✓
- `EXCLUSIVE_CONTROL_ENABLED` declared `const ... : boolean` in controller.ts and content/index.ts; esbuild `define` injects string `'false'`/`'true'` → coerces to boolean. ✓
- `NavOutcome` in state.ts adds `'unexpected_status'` per amend §3.1 — but slice 3 owns the classifier rewrite; state.ts only declares the type. The existing `status.ts` (Phase 1) still has the old `NavOutcome` without `unexpected_status`. **Potential conflict**: two `NavOutcome` definitions. Resolution: `state.ts`'s `NavOutcome` is the navigation-state slot type (used by `NavigationState.http.outcome`); `status.ts`'s is the classifier return. They are NOT the same symbol and don't collide at compile time (different modules), but to avoid confusion the plan keeps them separate and slice 3 reconciles them (replacing `status.ts`'s classifier). This is acceptable for slice 1 — no task imports `status.ts`'s `NavOutcome` into the new code.

No blocking issues found. Plan is internally consistent and scoped to slice 1.
