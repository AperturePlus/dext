# CLAUDE.md — dext

Graph-driven **human-assisted** university-faculty crawler. A rewrite of `yanclaw`; the
**preserved browser-facing artifacts are the Tampermonkey userscript** in `userscripts/` and
the `browserext/` MV3 extension, whose HTTP contracts are **FIXED**. The Python backend
implements those contracts — **never change the script or extension to fit the backend.**

## Commands

- **Run tests:** `uv run pytest -q` (whole suite) · `uv run pytest tests/test_<area>.py -v` (one file)
- **Single test:** `uv run pytest tests/test_x.py::test_name -v`
- **browserext:** `cd browserext && npm install && npm test` (node:test, no pytest) · `npm run build` → `dist/`
- `uv` is the runner; it auto-syncs deps from `pyproject.toml` on first run. Python 3.11.

## Non-negotiable invariants (source doc §3; overview spec §1)

1. Browser is **visible** — never headless batch-crawl.
2. Fetch is **strictly single-threaded**: at most **one URL in flight** at any instant.
3. LLM extraction may run **concurrently**; **DB writes are single-writer** (one DB worker).
4. **One SQLite DB per university**, path derived from the English abbreviation.
5. **Full UTF-8** end to end (`ensure_ascii=False`, mojibake repair on inbound HTML).
6. The graph (`crawl_graph_nodes` in SQLite) is the **only** scheduling truth; the browser-facing
   `FetchJob` queue is ephemeral in-memory state, never a second state machine.

## Architecture — one asyncio event loop

Tasks (started by `crawl` CLI): 1× aiohttp **bridge** server (serves the script, holds the job
queue) → 1× **GraphDriver** (sole node-claimer, sole `fetch()` caller) → N× **LLM workers**
→ 1× **DB worker** (single writer). Reads use per-task aiosqlite connections (WAL).

## Subproject status & build order

Built **subproject-by-subproject** (each gets its own spec → plan → TDD execution), not one
master plan. Order: SP1 → {SP2, SP3, SP4} → SP5 → SP6 → SP7. SP1–SP7 are all implemented;
work has since progressed onto `tcN` (touch-up) and `sp7rcN` (release-candidate) branches off
`main`. Current work branch: **`tc3`** (104 commits ahead of `main`).

| SP | Package | Status |
|----|---------|--------|
| SP1 Foundations | `config.py`, `seed.py`, `types.py` | ✅ done |
| SP2 Storage | `storage/` (models, db, lifecycle, writer, dedup) | ✅ done |
| SP3 Page processing | `page/` (text, urls, links, pagination, candidates) — pure fns, uses **bs4** | ✅ done |
| SP4 Fetch bridge | `bridge/` (queue, fetcher, server, decision, mojibake, redirect, health, _httputil) | ✅ done |
| SP5 LLM | `llm/` (DeepSeek client, decider, extractor, sanitizer, retry) | ✅ done |
| SP6 Graph engine | `engine/` (driver, handlers, workers, names, priorities, retry, seeds) | ✅ done |
| SP7 CLI | `cli.py` (`crawl -u`, fresh/`--resume`/`-oid`/`--reset`) | ✅ done |
| browserext | `browserext/` — MV3 extension, sole official frontend (Phase 2 GA) | ✅ Phase 2 done (slice 6) |

Specs: `docs/superpowers/specs/` (SP0 overview is authoritative for cross-cutting decisions;
browserext has its own `2026-06-27-browserext-background-probe-{design,}.md`). Plans:
`docs/superpowers/plans/`.

## The fixed HTTP contract (SP4)

Source of truth: `userscripts/src/api.ts` + `userscripts/src/types.ts`. Base
`http://127.0.0.1:21520/api`, all UTF-8 JSON. Endpoints: `GET /jobs/next` (FetchJob|204),
`POST /jobs/{id}/complete|fail|skip|override`, `GET /status`, `GET /decision` (|204),
`POST /decision/{id}/resolve`. Shared DTOs that mirror `types.ts` live in `dext.types`
(`FetchAction`, `PaginationState`, `FetchResult`); `JobContext`/`FetchJob`/`PendingDecision`
live in `dext.bridge`.

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

## Testing policy

- TDD always: write the failing test, run it RED, implement minimally, run GREEN, **commit** (one
  conventional commit per green step: `feat(spN): …`, `test(spN): …`, `docs(spN): …`).
- **Real live LLM only** for any LLM-touching test — **never** `MockLLM`/`FakeLLM`/record-replay
  (overview §9). `DEEPSEEK_API_KEY` is in `.env` (present since 2026-06-14). **SP1–SP4 and
  browserext are LLM-free**; only SP5 (`llm/`), SP6 (`engine/`), and SP7 tests touch the key
  (`.env.example` documents every var). Validate the key before LLM tests; don't auto-downgrade.
- SP1–SP3 are pure-logic unit tests; SP4 uses `aiohttp.test_utils.TestServer`/`TestClient` (no
  `pytest-aiohttp` needed); pytest is `asyncio_mode="auto"` so async tests/fixtures need no marker.
  **browserext** uses `node --test` (its tests transpile `src/*.ts` on the fly via the TS API —
  see `browserext/tests/harness.mjs`; relative imports in `src/*.ts` carry `.js` so the tsc
  output resolves in the browser's ESM loader, which unlike Node does not auto-append extensions).
- Diagnosability first: every drop/skip/retry carries a reason code + count.

## Cross-cutting bug traps (guard these in every SP)

- **Late `/complete` after the 60 s job timeout** → must be idempotent (already-resolved/stale id →
  log + no-op, never crash). Script's per-request HTTP timeout is only 10 s — distinct from the job timeout.
- **Form-pagination `synthetic_url` must byte-match `formPagination.ts`** or script-reported vs
  backend-parsed states create duplicate graph nodes.
- **SP6 DONE detection race**: terminate only when no claimable node AND extract queue empty AND
  in-flight-extraction counter == 0 AND DB write queue drained.
- **Cross-org shared detail URL**: `node_key` includes `org_unit_id`, so the same detail URL under
  two units = two nodes; reuse a finished extraction via `page_cache` (by `identity_url`) but still
  record the second affiliation.
- **Single in-flight**: `/jobs/next` returns 204 whenever a job is assigned; `/status` counters
  (`completed/failed/skipped`) accumulate across ephemeral jobs.

## Conventions

- KISS / no premature optimization: no connection pools, no multi-writer DB, no fuzzy dedup, no
  generic crawler framework. Prefer many small focused modules over one tangled file.
- Each package's `__init__.py` re-exports its public interface (`__all__`); modules form an acyclic
  import DAG. Keep layers pure: `bridge` has **no** DB/LLM/page imports; `page` has no network/DB/LLM.
- Platform: Windows + Git Bash. `LF will be replaced by CRLF` git warnings are benign.
