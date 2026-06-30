# slice-0 spike — cross-world localStorage readability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate the load-bearing assumption of the Phase 2 fallback model — that a Tampermonkey userscript can read a value an extension content script writes to the page's `localStorage` — before writing any other slice's plan.

**Architecture:** A throwaway, plain-JavaScript probe pair: (1) an extension content script that writes a fresh timestamp to `localStorage` every 2 s, (2) a Tampermonkey userscript that reads it and logs freshness to the console. The gate is a manual browser observation, not a unit test — the thing being validated is a cross-world runtime property that no `node:test` can reach. On GREEN, slice 1 (heartbeat) proceeds using this localStorage channel; on RED, the spec's §6 fallback path (CustomEvent broadcast, or retreat to all-or-nothing) is taken instead.

**Tech Stack:** MV3 content scripts (plain JS, no TS build step), Tampermonkey userscript (`@grant none` so it runs in the page context and shares the page's `localStorage`). No backend, no LLM, no Python.

## Global Constraints

- **Spec source of truth:** [docs/superpowers/specs/2026-06-28-browserext-content-migration-design.md](../specs/2026-06-28-browserext-content-migration-design.md) §3.2 and §5.1. The spike is the spec's first gate ("闸门：通过才写 slice 1 plan；不通过则 §6 改通道").
- **Not merged to main:** spec §5.1 — "最小验证，**不**并入主分支". All spike work lives on a dedicated `spike/ext-alive-localstorage` branch off `tc3`; nothing from this branch is merged to `main`. The probe code is throwaway.
- **localStorage key (verbatim from spec §3.2):** `ycl_ext_alive_v1`. Write cadence 2 s, TTL 6 s.
- **Allowed fetch host suffixes (verbatim, from [userscripts/src/hostPolicy.ts](../../../userscripts/src/hostPolicy.ts)):** `edu.cn`, `github.io`. Both content script and reader userscript match only these.
- **Single-tab / single-instance assumption** (spec §0.1) holds; the spike does not exercise owner election.
- **Platform:** Windows + Git Bash. LF→CRLF git warnings are benign (CLAUDE.md §Conventions).
- **No backend required:** the spike CS writes `localStorage` directly; the reader reads it. The dext backend need not be running. (If it is running, harmless — the spike touches no backend endpoints.)

---

## File Structure

| File | Responsibility | Disposition |
|---|---|---|
| `browserext/spike/aliveProbe.js` | Extension content script. Every 2 s writes `Date.now()` to `localStorage['ycl_ext_alive_v1']`. Plain JS (no TS, no build). | Throwaway — spike branch only |
| `browserext/spike/aliveProbeReader.user.js` | Tampermonkey userscript. Every 1 s reads the key and console.logs value + freshness. `@grant none`. | Throwaway — spike branch only |
| `browserext/manifest.json` | Add a `content_scripts` entry pointing at `spike/aliveProbe.js`, matching `*://*.edu.cn/*` + `*://*.github.io/*`, `run_at: document_idle`. | Spike-branch edit only; reverted/abandoned with the branch |

Why plain JS and a `spike/` dir (not `src/*.ts`): the production build is `tsc` with `include: ["src/**/*.ts"]` ([browserext/tsconfig.json](../../../browserext/tsconfig.json)). A plain-JS file under `spike/` is invisible to `tsc`, needs no build step, and is visually quarantined as throwaway — matching the spec's "不并入主分支" intent. The reusable, TDD'd `alive.ts` module (with TTL logic) is deliberately **not** part of this spike; it is slice 1's first task, where it gets a proper test cycle.

---

### Task 1: Spike branch + content script that writes the alive timestamp

**Files:**
- Create: `browserext/spike/aliveProbe.js`

**Interfaces:**
- Produces: a content script that, when loaded into a `*.edu.cn` / `*.github.io` page, sets `localStorage['ycl_ext_alive_v1']` to `String(Date.now())` once on inject and every 2 s thereafter. No exports (it is a content script, not a module).

- [ ] **Step 1: Create the spike branch off `tc3`**

Run:
```bash
cd d:/pyprj/dext
git checkout tc3
git checkout -b spike/ext-alive-localstorage
```
Expected: a new branch `spike/ext-alive-localstorage` created off `tc3`; working tree clean (the in-flight `tc3` modifications to `browserext/src/*` and `tsconfig.json` should already be committed — if `git status` shows uncommitted changes, stop and ask the user how to proceed before branching).

- [ ] **Step 2: Create the content script**

Create `browserext/spike/aliveProbe.js` with exactly:

```javascript
// SPIKE (slice-0): validates that a Tampermonkey userscript can read a value
// an extension content script writes to page localStorage. Throwaway — not
// part of the production build; lives on the spike branch only. See
// docs/superpowers/plans/2026-06-28-browserext-slice0-spike.md and spec §5.1.
//
// A content script shares the page's localStorage (and DOM) across its
// isolated world. This probe exploits that to publish an "alive" timestamp
// every 2s — which the reader userscript (a different world) must be able to
// see for the Phase 2 fallback model to hold.
(function () {
  const KEY = 'ycl_ext_alive_v1';
  const INTERVAL_MS = 2000;

  function writeAlive() {
    try {
      localStorage.setItem(KEY, String(Date.now()));
    } catch {
      // ignore quota / access errors — spike is best-effort
    }
  }

  writeAlive();
  setInterval(writeAlive, INTERVAL_MS);
})();
```

- [ ] **Step 3: Verify the production build is unaffected**

The spike file is plain JS under `spike/`, invisible to `tsc`. Confirm no regression:

Run:
```bash
cd browserext && npm run build && npm test
```
Expected: `tsc` succeeds with no errors; `npm test` reports all existing Phase 1 tests passing. (If `tsc` complains, the spike file was accidentally placed under `src/` — move it back to `spike/`.)

- [ ] **Step 4: Commit**

```bash
cd d:/pyprj/dext
git add browserext/spike/aliveProbe.js
git commit -m "test(browserext): add slice-0 spike content script that writes alive timestamp

Throwaway probe (spike branch, not for main). Writes Date.now() to
localStorage['ycl_ext_alive_v1'] every 2s to validate cross-world
readability — the load-bearing assumption of the Phase 2 fallback model."
```

---

### Task 2: Reader userscript that reads + logs freshness

**Files:**
- Create: `browserext/spike/aliveProbeReader.user.js`

**Interfaces:**
- Consumes: `localStorage['ycl_ext_alive_v1']` written by the content script from Task 1.
- Produces: a Tampermonkey userscript that, on a `*.edu.cn` / `*.github.io` page, console.logs `[dext-spike] alive: ts=<ms> ageMs=<int> fresh=<bool>` once on inject and every 1 s thereafter. `fresh` is `true` when `Date.now() - ts < 6000`.

- [ ] **Step 1: Create the reader userscript**

Create `browserext/spike/aliveProbeReader.user.js` with exactly:

```javascript
// ==UserScript==
// @name         dext spike — alive reader
// @namespace    https://github.com/AperturePlus/dext
// @version      0.0.1
// @description  SPIKE (slice-0): reads the extension content script's alive timestamp from localStorage and logs freshness. Throwaway.
// @match        *://*.edu.cn/*
// @match        *://*.github.io/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

// @grant none → runs in the page context, sharing the page's localStorage
// with the extension content script (a separate isolated world). If this
// reader can see the value the CS writes, the Phase 2 fallback channel is
// viable.
(function () {
  const KEY = 'ycl_ext_alive_v1';
  const TTL_MS = 6000;
  const POLL_MS = 1000;

  function read() {
    let raw = null;
    try {
      raw = localStorage.getItem(KEY);
    } catch {
      // ignore
    }
    if (raw === null) {
      console.log('[dext-spike] alive: <absent>');
      return;
    }
    const ts = Number(raw);
    const age = Number.isFinite(ts) ? Date.now() - ts : NaN;
    const fresh = Number.isFinite(age) && age < TTL_MS;
    console.log('[dext-spike] alive: ts=' + raw + ' ageMs=' + age + ' fresh=' + fresh);
  }

  read();
  setInterval(read, POLL_MS);
})();
```

- [ ] **Step 2: Sanity-check the userscript header parses**

There is no unit test for a userscript header. Verify it is well-formed by eye: the `==UserScript==`/`==/UserScript==` delimiters are on their own lines, every `@` directive has a value, and `@match` patterns are valid (`*://*.edu.cn/*`, `*://*.github.io/*`). Tampermonkey rejects malformed headers on install — that rejection is itself a failure mode the manual gate (Task 3) will catch.

- [ ] **Step 3: Commit**

```bash
cd d:/pyprj/dext
git add browserext/spike/aliveProbeReader.user.js
git commit -m "test(browserext): add slice-0 spike reader userscript

Throwaway probe (spike branch). Reads localStorage['ycl_ext_alive_v1']
every 1s and console.logs freshness (TTL 6s). @grant none so it runs in
the page context sharing localStorage with the extension content script."
```

---

### Task 3: Wire the content script into the manifest

**Files:**
- Modify: `browserext/manifest.json`

**Interfaces:**
- Produces: a manifest whose `content_scripts` declaratively injects `spike/aliveProbe.js` into `*.edu.cn` / `*.github.io` pages at `document_idle`. No new permission required — `host_permissions` already includes `<all_urls>` (Phase 1).

- [ ] **Step 1: Read the current manifest**

Run:
```bash
cat browserext/manifest.json
```
Expected: the Phase 1 manifest with `manifest_version: 3`, `background.service_worker: "dist/background.js"`, `permissions: ["webRequest","alarms","tabs","storage"]`, `host_permissions: ["http://127.0.0.1:21520/*","<all_urls>"]`, `minimum_chrome_version: "110"`. Confirm there is **no** existing `content_scripts` key (Phase 1 has none).

- [ ] **Step 2: Add the `content_scripts` entry**

Add the `content_scripts` array (after `background` or anywhere top-level; keep the rest of the file byte-identical). The full resulting `browserext/manifest.json`:

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
  "content_scripts": [
    {
      "matches": ["*://*.edu.cn/*", "*://*.github.io/*"],
      "js": ["spike/aliveProbe.js"],
      "run_at": "document_idle"
    }
  ],
  "permissions": ["webRequest", "alarms", "tabs", "storage"],
  "host_permissions": [
    "http://127.0.0.1:21520/*",
    "<all_urls>"
  ],
  "minimum_chrome_version": "110"
}
```

- [ ] **Step 3: Validate the manifest is well-formed JSON**

Run:
```bash
node -e "JSON.parse(require('fs').readFileSync('browserext/manifest.json','utf8')); console.log('manifest OK')"
```
Expected: prints `manifest OK`. (If it throws, fix the JSON — a stray trailing comma or missing bracket.)

- [ ] **Step 4: Confirm the build still passes**

Run:
```bash
cd browserext && npm run build && npm test
```
Expected: `tsc` succeeds; all Phase 1 tests pass. (`tsc` does not read `manifest.json`, but this confirms the spike branch hasn't disturbed the production build.)

- [ ] **Step 5: Commit**

```bash
cd d:/pyprj/dext
git add browserext/manifest.json
git commit -m "test(browserext): inject slice-0 spike content script via manifest

Adds a content_scripts entry (spike branch only) loading spike/aliveProbe.js
into *.edu.cn / *.github.io pages at document_idle. No new permission —
host_permissions already covers <all_urls>."
```

---

### Task 4: Manual verification gate (the spec's §5.1 闸门)

**Files:**
- None created or modified. This task is a browser observation with an exact expected output, then a branch decision. It is the spike's deliverable.

**Interfaces:**
- Consumes: the three artifacts from Tasks 1–3 (content script, reader userscript, manifest entry).
- Produces: a recorded PASS/FAIL result. On PASS, the localStorage channel is confirmed viable for slice 1. On FAIL, the spec §6 fallback path is triggered.

- [ ] **Step 1: Load the spike extension**

In Chrome/Edge:
1. `chrome://extensions` → enable Developer mode.
2. If the dext extension is already loaded (from Phase 1), click its "Reload" icon. Otherwise "Load unpacked" → select `d:/pyprj/dext/browserext/`.
3. Confirm it loads without errors (no red error text on the extension card).

Expected: extension card shows "dext background probe", enabled, no errors.

- [ ] **Step 2: Install the reader userscript in Tampermonkey**

1. Open the Tampermonkey dashboard → "Utilities" → "Import from file" (or just open `d:/pyprj/dext/browserext/spike/aliveProbeReader.user.js` in the browser — Tampermonkey intercepts `.user.js` URLs and offers to install).
2. Install `dext spike — alive reader` v0.0.1.
3. Confirm it is enabled.

Expected: Tampermonkey lists the script as enabled; no header-parse error.

- [ ] **Step 3: GREEN check 1 — CS write → userscript reads (fresh)**

1. Open a new tab to any real `*.edu.cn` page (e.g. `https://www.scu.edu.cn/` or any reachable university page). The page must be on a host matching `*://*.edu.cn/*` so both the content script and the reader inject.
2. Open DevTools → Console.
3. Watch the console for ~6 seconds.

Expected (PASS): console prints a line roughly every 1 s:
```
[dext-spike] alive: ts=1751112345678 ageMs=12 fresh=true
[dext-spike] alive: ts=1751112347680 ageMs=14 fresh=true
...
```
- `ts` is a millisecond timestamp that **advances** (a new value every ~2 s, reflecting the content script's write cadence).
- `ageMs` is small (under ~2000 ms when both timers are ticking).
- `fresh` is `true`.

If instead the console prints `[dext-spike] alive: <absent>` indefinitely: the userscript cannot see the CS's `localStorage` write — this is a **RED** result (go to Step 5).

- [ ] **Step 4: GREEN check 2 — disable extension → readers sees stale**

1. Go to `chrome://extensions` and **disable** the dext extension (toggle off).
2. Switch back to the `*.edu.cn` tab (do NOT reload it — the already-installed reader userscript keeps polling; the disabled extension's content script stops writing).
3. Watch the console for ~8 seconds.

Expected (PASS): after ~6 s, the lines change to:
```
[dext-spike] alive: ts=1751112347680 ageMs=6400 fresh=false
[dext-spike] alive: ts=1751112347680 ageMs=7400 fresh=false
...
```
- `ts` is **frozen** at the last value the content script wrote before being disabled (no longer advancing).
- `ageMs` grows past 6000.
- `fresh` flips to `false` once `ageMs >= 6000`.

If `ts` keeps advancing even after the extension is disabled: the reader is reading a value written by something else (not the CS) — investigate before declaring PASS. (Most likely cause: a second copy of the extension or a stale tab; close other edu.cn tabs and re-check.)

- [ ] **Step 5: Record the result and decide the next channel**

- **If both checks PASS (GREEN):** the cross-world `localStorage` channel is viable. Save the finding to memory (it is a non-obvious project fact future sessions need), then proceed: the next plan is slice 1 (heartbeat), which will TDD a real `alive.ts` module over this channel. The spike branch is **not** merged to `main`; it can be deleted once slice 1's plan is written (the spike's reusable logic — none — is recreated properly in slice 1).

  Save memory:
  - File: `C:\Users\xc150\.claude\projects\d--pyprj-dext\memory\dext-ext-alive-localstorage-confirmed.md`
  - Body: "Phase 2 fallback channel confirmed viable: a Tampermonkey userscript (`@grant none`, page context) can read a value an extension content script writes to page `localStorage`. Validated 2026-06-28 via slice-0 spike on `*.edu.cn`. Implication: slice 1 (heartbeat) and the per-slice fallback model (spec §3.2) can rely on the `ycl_ext_alive_v1` localStorage key. Type: project."
  - Add a one-line pointer to `MEMORY.md`: `- [dext ext alive localStorage confirmed](dext-ext-alive-localstorage-confirmed.md) — cross-world localStorage readable; Phase 2 fallback channel viable`

  Then:
  ```bash
  cd d:/pyprj/dext
  git checkout tc3
  # spike branch retained for reference until slice 1 plan is written; delete after:
  # git branch -D spike/ext-alive-localstorage   (only after slice 1 plan lands)
  ```

- **If either check FAILS (RED):** the `localStorage` channel is not viable. Per spec §6, switch channel:
  - Preferred alternative: a `CustomEvent` dispatched on `window` by the content script (userscript listens on `window` for the same event type — works because content scripts and page/userscripts share the DOM `window` for events even across isolated worlds).
  - Fallback alternative: retreat to all-or-nothing (spec §3, "方案 C") — abandon per-slice coexistence; each layer is installed alone, switching requires uninstall/reinstall.
  - Record the RED result and the chosen alternative in the spec's §6, then re-plan slice 1 against the new channel. Do **not** write slice 1 against `localStorage`.
  ```bash
  cd d:/pyprj/dext
  git checkout tc3
  git branch -D spike/ext-alive-localstorage
  ```

- [ ] **Step 6: Report the gate result to the user**

State plainly which checks passed/failed, quote one console line as evidence (e.g. `"observed: [dext-spike] alive: ts=... ageMs=... fresh=true"`), and name the next step (slice 1 plan via writing-plans, or spec §6 channel change). Do not claim success without the observed console line.

---

## Self-Review (run after writing; fixes inline above)

**1. Spec coverage (spec §5.1):**
- §5.1 step 1 "扩展 CS 注入 *.edu.cn，每 2s 写 ycl_ext_alive_v1" → Task 1 (content script, 2 s cadence, key `ycl_ext_alive_v1`). ✓
- §5.1 step 2 "一段只读该 key 的 userscript，在 console 打印读到值与新鲜度" → Task 2 (reader userscript, logs value + freshness). ✓
- §5.1 step 3 "CS 写 → userscript 读到；卸载扩展 → userscript 读到陈旧" → Task 4 Step 3 (write→read fresh) + Step 4 (disable→stale). ✓
- §5.1 闸门 "通过才写 slice 1 plan；不通过则 §6 改通道" → Task 4 Step 5 (GREEN → slice 1; RED → §6 channel change). ✓
- §3.2 key `ycl_ext_alive_v1`, write 2 s, TTL 6 s → Task 1 (2 s) + Task 2 (TTL 6 s, key verbatim). ✓
- §3.2 allowed host suffixes edu.cn/github.io → manifest matches (Task 3) + reader `@match` (Task 2). ✓
- §5.1 "不并入主分支" → Global Constraints + spike branch in Task 1 Step 1 + Task 4 Step 5 (branch not merged; deleted or retained-for-reference). ✓

**2. Placeholder scan:** No "TBD"/"TODO"/"implement later". Every code step shows full code. Manual-verification steps show exact expected console output. No "similar to Task N". ✓

**3. Type consistency:** Only one shared name — `ycl_ext_alive_v1` — used identically in Task 1 (write), Task 2 (read), and the spec. `TTL_MS = 6000` and `INTERVAL_MS = 2000` match spec §3.2. No cross-task function/type names to drift (the spike has no shared module surface — each probe is self-contained). ✓

**4. Scope check:** This plan covers exactly the slice-0 spike (spec §5.1). It does not touch slice 1 (heartbeat) — that is the next plan, gated on Task 4 GREEN. Single, focused deliverable. ✓
