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
