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

You can also pin the blue dext toolbar icon and click it on the target page. The
icon is the fastest start path: it binds that tab, enables auto mode, clears
pause, and reconciles immediately. Badge meanings: `ON` = backend connected,
`ERR` = backend unavailable (automatic retry continues), `BUSY` = another tab is
bound, and `N/A` = unsupported host. Granting Chrome access to all sites does not
change the application allowlist (`*.edu.cn` and `*.github.io`).

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
