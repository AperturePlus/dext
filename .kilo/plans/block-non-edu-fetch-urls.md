# Plan: Block non edu.cn / .github.io fetch URLs at the backend

## Goal
Prevent the userscript from being handed a job whose target URL does not end in
`edu.cn` or `.github.io` (whether the URL is the direct candidate or the final
URL after redirect probing). Such URLs navigate the frontend tab to a
non-working host and block the whole run.

## Background (current flow)
- Frontend navigates via `window.location.href = job.url`
  (`userscripts/src/actions.ts:201`). `job.url` == `node.url` from the DB.
- Every newly-created `node.url` flows through
  `resolve_discovered_url` (`src/dext/engine/seeds.py:28`), which:
  - returns the direct URL unchanged when `redirect_guard is None` ("直达的"),
  - otherwise calls `RedirectGuard.probe_redirect` and takes `verdict.final_url`
    ("重定向探测出来的").
- Today a non-wechat offsite redirect gets `OFFSITE_OK` and is kept, so a random
  external host reaches the frontend and blocks it.
- The single hand-off point to the frontend is `HumanFetcherBridge.fetch`
  (`src/dext/bridge/fetcher.py:38`); there is currently no pre-enqueue host
  guard (only `explicit_port` checks in `complete`/`override`).

## Chosen scope: both layers
1. Discovery-layer gate (covers direct + redirect-probed, no bad URL becomes a node).
2. Hard guard at the fetch hand-off (defense-in-depth for pre-existing DB nodes
   from prior runs, and for manual `override`).

## Changes

### 1. `src/dext/url_policy.py` — new allowlist helper
Add a constant and a pure function (no IO, unit-testable):
```python
ALLOWED_FETCH_HOST_SUFFIXES = ("edu.cn", "github.io")

def is_allowed_fetch_host(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return True  # non-http (e.g. about:org_unit, bare test strings) aren't browser fetches
    host = (parts.hostname or "").lower()
    if not host:
        return False
    return (
        host == "edu.cn" or host.endswith(".edu.cn")
        or host == "github.io" or host.endswith(".github.io")
    )
```
- Dot-boundary check so `notedu.cn` / `evilgithub.io` are rejected.
- Non-http schemes pass (synthetic `about:org_unit:` nodes are never claimed/fetched;
  bare-string unit-test URLs keep working).

### 2. `src/dext/engine/seeds.py` — gate in `resolve_discovered_url`
After resolving (both the `redirect_guard is None` direct path and the probed
path), apply the allowlist to the final URL; if not allowed, return `(None, {})`
so no node is created. This is the single funnel for all discovered URLs
(seeds + handler `_create_child` + `handle_org_listing` college links + form/url
pagination). `classify_redirect` is left unchanged — its `OFFSITE_OK` for a bad
host is now neutralized by this gate; `github.io` offsite still passes.

### 3. `src/dext/bridge/fetcher.py` — hard guard at hand-off
- `HumanFetcherBridge.fetch`: before enqueuing, if
  `not is_allowed_fetch_host(url)`, log and return a blocked `FetchResult`
  (`block_reason="invalid_url:host_not_allowed"`, `requested_url=url`,
  `identity_url=identity_url or url`, empty html/title) **without** enqueueing.
  The driver's `_process_fresh_result` already treats a `block_reason` as a
  failed fetch, marks the node, and continues — so the run never blocks.
- `HumanFetcherBridge.override`: mirror the existing `explicit_port` rejection —
  if `not is_allowed_fetch_host(new_url)`, log and return `None` (rejects manual
  override to a bad host).

### 4. Tests
- `tests/test_bridge_fetcher.py`: update the ~8 tests that use `https://x/...`
  to `https://x.edu.cn/...` (and `https://x/wrong`→`https://x.edu.cn/wrong`,
  `https://x/right`→`https://x.edu.cn/right`) so they pass the new guard.
  Bare-string tests (`u1`, `u2`, `u`) are unaffected (non-http passes).
- Add: `test_fetch_blocks_disallowed_host` —
  `fetch(url="https://evil.com/p")` returns
  `block_reason="invalid_url:host_not_allowed"`, `stats().failed == 1`, and
  `next_job()` is None (nothing enqueued).
- Add: `test_override_rejects_disallowed_host` —
  `override(job.id, "https://evil.com/x")` returns None and the job url is
  unchanged.
- Add (seeds): `test_resolve_discovered_url_blocks_disallowed_direct` (guard
  None, url `https://evil.com/p` → `(None, {})`) and
  `test_resolve_discovered_url_blocks_disallowed_redirect` (resolver returns
  `https://evil.com/` → `(None, {})`), plus confirm `github.io` still resolves.
- Existing `test_bridge_redirect.py` is unchanged (classify_redirect untouched).

## Verification
- `pytest` (asyncio_mode=auto; no ruff/mypy configured).
- Specifically: `pytest tests/test_bridge_fetcher.py tests/test_engine_seeds.py
  tests/test_bridge_redirect.py tests/test_engine_handlers.py`.

## Out of scope
- No frontend/userscript changes.
- No `complete()` final_url allowlist (too late — navigation already happened;
  the block is prevented upstream).
- Allowlist suffixes are a module constant, not a Settings tunable.
