# SP3 — Page processing (HTML→text/links/signals + pagination + candidate filter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build dext's page-processing layer — a package of **pure functions** that turn raw HTML + a URL into structured signals (text snapshot, normalized links + `link_signals`, `content_hash`, URL normalization, three kinds of pagination discovery, and diagnosable detail-candidate filtering) with **no network, no DB, no LLM, no global state**.

**Architecture:** A new `src/dext/page/` package of five focused modules — `text.py` (html2text snapshot + sha256), `urls.py` (stdlib URL normalization / same-site / offsite), `links.py` (BeautifulSoup4 DOM → `PageSnapshot` + `LinkSignal`), `pagination.py` (URL pagination, followup classification links, and a faithful byte-exact port of `userscripts/src/formPagination.ts`), and `candidates.py` (heuristic detail-candidate filter with per-reason drop counts). The two browser-contract DTOs SP3 produces (`PaginationState`, `FetchAction`) are added to `dext.types` (the shared-DTO home, alongside the enums and `ProfessorPayload`). Modules form a clean DAG: `text`/`urls` are leaves; `links` imports `text`+`urls`; `pagination`/`candidates` import `links`+`urls`(+`dext.types`).

**Tech Stack:** Python 3.11, BeautifulSoup4 (`html.parser` backend, **no lxml**), html2text, stdlib `urllib.parse`/`re`/`hashlib`/`unicodedata`, pytest. All SP3 tests are **synchronous** pure-function tests (no `pytest-asyncio` needed).

**Spec:** `docs/superpowers/specs/2026-06-13-dext-03-page-processing-design.md` (authoritative; source doc `target-graph-human-assisted-crawler.md` §8.4 / §9 / §11.1–11.4; overview §5/§6/§7). **Contract to byte-match:** `userscripts/src/formPagination.ts` + `userscripts/src/types.ts`.

---

## Pre-verified facts (don't re-derive)

- **Branch:** work happens on `sp3-page-processing` (already created off `sp2-storage`). `main` holds only the initial commit; SP1/SP2 live on their stacked branches, so SP3 **must** stay on top of `sp2-storage` to have `dext.types`, the storage package, and the configured pytest setup. Do **not** rebase onto `main`.
- **bs4 is NOT installed yet.** `pyproject.toml` declares `html2text>=2024.2.26` (resolved 2025.4.15) but no `beautifulsoup4`. Task 1 adds `beautifulsoup4>=4.12`.
- `dext.types` already exists (created in SP2) and re-exports `NodeType`/`EdgeType`/`NodeStatus` from `dext.storage.models` plus `ProfessorPayload`. SP3 appends `PaginationState` + `FetchAction` and extends `__all__`.
- Pytest config in `pyproject.toml`: `testpaths=["tests"]`, `pythonpath=["src"]`, `asyncio_mode="auto"`. Tests import `dext.*` directly. `asyncio_mode="auto"` is harmless for SP3's sync tests.
- Existing test style: plain `pytest`, inline UTF-8 Chinese literals, direct `from dext.X import ...`, `REPO_ROOT = Path(__file__).resolve().parent.parent` when a real asset is needed (SP3 uses **inline HTML string fixtures** — KISS, matches the codebase).
- **Test command (from repo root `D:\pyprj\dext`):** `uv run pytest <path> -v` (first run triggers a `uv` sync that installs `beautifulsoup4`).
- The whole layer is pure: a grep in Task 10 asserts **no** `openai`/`aiohttp`/`requests`/`sqlalchemy`/`aiosqlite` imports leaked into `src/dext/page/`.

## Locked contract — `formPagination.ts` port reference (Task 7)

These are copied verbatim from `userscripts/src/formPagination.ts`. The Python port **must** reproduce them and the synthetic-URL serialization byte-for-byte (else script-reported vs backend-parsed states create duplicate graph nodes — the #1 SP3 bug risk).

- Page-assignment regex (JS `/i`): `document\.forms\[['"]([^'"]+)['"]\]\.([A-Za-z0-9_]+)\.value\s*=\s*['"]?(\d+)['"]?`
- GOPAGE field regex (JS `/i`): `\b([A-Za-z0-9_]*?)GOPAGE\b`
- Current-page URL params (in order): `PAGENUM, page, p, pn, fromWenNOWPAGE` (and `.this-page` element text takes precedence).
- Anchor selector: `a[href^="javascript:"]` — **case-sensitive** literal prefix `javascript:`.
- `state_id = form:<NAME>:<FIELD>:<N>`; `label = "<NAME> 第 <N> 页"`; `fields = {<FIELD>: str(N)}`; `submit = True`; `url = current_url`.
- `total_pages = max(expanded_indexes ∪ collected_pages)`.
- Skip `page <= 1` and `page == current_page`. Dedup by `synthetic_url`. Sort by `(page_index, synthetic_url)`.
- GOPAGE expansion: if an `input[name]` matches the GOPAGE regex **and** (it has no owning `<form>` ancestor, or its form's `name` == this form) **and** (no prefix, or `field.lower().startswith(prefix.lower())`) → expand pages to `1..max(collected_pages)`; else use the sorted collected pages.
- **Synthetic URL** (must equal JS `new URL(u)` + `URLSearchParams.set` + `.toString()` with `hash=''`): delete all existing `__ycl_*` query params, then append `__ycl_kind=form`, `__ycl_form=<NAME>`, `__ycl_field=<FIELD>`, `__ycl_page=<N>` (in that order); re-serialize the **whole** query with the WHATWG `application/x-www-form-urlencoded` serializer (safe bytes = ASCII alnum + `* - . _`; space → `+`; everything else → uppercase `%XX`); lowercase host; drop default port (80/443); no fragment.

## File Structure

```
dext/
├─ pyproject.toml                     # MODIFY (Task 1) — add beautifulsoup4>=4.12
├─ src/dext/
│  ├─ types.py                        # MODIFY (Task 2) — append PaginationState + FetchAction, extend __all__
│  └─ page/
│     ├─ __init__.py                  # CREATE (Task 1 marker; Task 9 re-exports public interface)
│     ├─ text.py                      # CREATE (Task 4) — html_to_text, content_hash
│     ├─ urls.py                      # CREATE (Task 3) — normalize_url, same_site, is_offsite (+ query helpers)
│     ├─ links.py                     # CREATE (Task 5) — LinkSignal, PageSnapshot, build_snapshot
│     ├─ pagination.py                # CREATE (Task 6 url+followup, Task 7 form states + merge)
│     └─ candidates.py                # CREATE (Task 8) — FilterContext, FilterResult, filter_detail_candidates
└─ tests/
   ├─ test_page_import.py             # Task 1
   ├─ test_types_pagination.py        # Task 2
   ├─ test_page_urls.py               # Task 3
   ├─ test_page_text.py               # Task 4
   ├─ test_page_links.py              # Task 5
   ├─ test_page_pagination.py         # Task 6
   ├─ test_page_form_pagination.py    # Task 7
   └─ test_page_candidates.py         # Task 8
```

Module boundaries (no cycles): `text` imports `html2text`/`hashlib`; `urls` imports `urllib`/`re`; `links` imports `bs4`+`text`+`urls`; `pagination` imports `bs4`+`urls`+`dext.types`; `candidates` imports `links`+`urls`. `dext.types` imports nothing from `dext.page`.

---

### Task 1: Scaffold — bs4 dependency, `page` package, import smoke test

**Files:**
- Modify: `pyproject.toml`
- Create: `src/dext/page/__init__.py`
- Create: `tests/test_page_import.py`

- [ ] **Step 1: Confirm the branch**

Run (from `D:\pyprj\dext`):
```bash
git branch --show-current
```
Expected: `sp3-page-processing`. If not, run `git checkout sp3-page-processing` (it already exists). Do **not** create it off `main`.

- [ ] **Step 2: Add `beautifulsoup4` to dependencies**

In `pyproject.toml`, add the dependency to the `[project].dependencies` list (keep the others unchanged). Add this line after `"html2text>=2024.2.26",`:
```toml
    "beautifulsoup4>=4.12",
```

- [ ] **Step 3: Create the page package marker**

Create `src/dext/page/__init__.py`:
```python
"""dext page processing — pure HTML/URL → structured signals.

No network, no DB, no LLM, no global state (spec §1, overview §7). The public
interface is re-exported here at the end of SP3 (see __all__ below, populated in
the final task).
"""
```

- [ ] **Step 4: Write the import smoke test**

Create `tests/test_page_import.py`:
```python
def test_page_package_imports():
    import dext.page  # noqa: F401


def test_beautifulsoup_available():
    from bs4 import BeautifulSoup

    soup = BeautifulSoup("<a href='x'>t</a>", "html.parser")
    assert soup.find("a").get("href") == "x"
```

- [ ] **Step 5: Sync deps and run the smoke test**

Run:
```bash
uv run pytest tests/test_page_import.py -v
```
Expected: `2 passed` (first run triggers a `uv` sync that installs `beautifulsoup4`).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/dext/page/__init__.py tests/test_page_import.py uv.lock
git commit -m "chore(sp3): scaffold page package + beautifulsoup4 dependency" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Browser-contract DTOs — `PaginationState` + `FetchAction` in `dext.types`

**Files:**
- Modify: `src/dext/types.py`
- Test: `tests/test_types_pagination.py`

These mirror `userscripts/src/types.ts` exactly. They live in `dext.types` (shared-DTO home) so SP4/SP6 import them without depending on `dext.page`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_types_pagination.py`:
```python
from dataclasses import fields

from dext.types import FetchAction, PaginationState


def test_pagination_state_required_and_optional_fields():
    s = PaginationState(
        kind="form_submit",
        state_id="form:fromWen:fromWenNOWPAGE:2",
        label="fromWen 第 2 页",
        page_index=2,
        form_name="fromWen",
        fields={"fromWenNOWPAGE": "2"},
        submit=True,
        synthetic_url="https://x/xylb.jsp?__ycl_page=2",
        url="https://x/xylb.jsp",
    )
    assert s.kind == "form_submit"
    assert s.total_pages is None  # optional, defaults None
    assert s.fields == {"fromWenNOWPAGE": "2"}


def test_pagination_state_mirrors_types_ts_field_set():
    names = {f.name for f in fields(PaginationState)}
    assert names == {
        "kind", "state_id", "label", "page_index", "total_pages",
        "form_name", "fields", "submit", "synthetic_url", "url",
    }


def test_fetch_action_defaults_optional():
    a = FetchAction(kind="form_submit")
    assert a.form_name is None
    assert a.fields is None
    assert a.submit is None
    assert a.page_index is None


def test_dtos_exported_from_types_all():
    import dext.types as t

    assert "PaginationState" in t.__all__
    assert "FetchAction" in t.__all__
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_types_pagination.py -v
```
Expected: FAIL — `ImportError: cannot import name 'FetchAction' from 'dext.types'`.

- [ ] **Step 3: Append the DTOs to `dext/types.py`**

Append to `src/dext/types.py` (after `ProfessorPayload`):
```python
@dataclass
class FetchAction:
    """Browser action telling the userscript to fill a form field and submit
    (mirrors userscripts/src/types.ts FetchAction). Produced by SP3 form
    pagination, carried on FetchJob/graph node, consumed by SP4/SP6."""

    kind: str  # always "form_submit" for now
    form_name: str | None = None
    fields: dict[str, str] | None = None
    submit: bool | None = None
    synthetic_url: str | None = None
    label: str | None = None
    page_index: int | None = None
    state_id: str | None = None


@dataclass
class PaginationState:
    """One discovered form-pagination state (mirrors userscripts/src/types.ts
    PaginationState). `synthetic_url` is the stable backend identity URL; it must
    be byte-identical to what formPagination.ts computes for the same page."""

    kind: str  # always "form_submit"
    state_id: str  # "form:<NAME>:<FIELD>:<N>"
    label: str
    page_index: int
    form_name: str
    fields: dict[str, str]
    submit: bool
    synthetic_url: str
    url: str
    total_pages: int | None = None
```

And extend `__all__` at the top of the file:
```python
__all__ = [
    "NodeType",
    "EdgeType",
    "NodeStatus",
    "ProfessorPayload",
    "FetchAction",
    "PaginationState",
]
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_types_pagination.py -v
```
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/types.py tests/test_types_pagination.py
git commit -m "feat(sp3): add PaginationState/FetchAction DTOs to dext.types (types.ts mirror)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `urls.py` — normalize_url, same_site, is_offsite

**Files:**
- Create: `src/dext/page/urls.py`
- Test: `tests/test_page_urls.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_urls.py`:
```python
from dext.page.urls import is_offsite, normalize_url, same_site


def test_relative_resolves_to_absolute():
    assert normalize_url("szdw/2.htm", "https://x.edu.cn/teacher/index.htm") == \
        "https://x.edu.cn/teacher/szdw/2.htm"


def test_fragment_is_stripped():
    assert normalize_url("page.htm#top", "https://x.edu.cn/") == "https://x.edu.cn/page.htm"


def test_host_lowercased_and_default_port_dropped():
    assert normalize_url("HTTP://X.EDU.CN:80/a", "https://x.edu.cn/") == "http://x.edu.cn/a"
    assert normalize_url("https://x.edu.cn:443/a", "https://x.edu.cn/") == "https://x.edu.cn/a"


def test_nondefault_port_kept():
    assert normalize_url("https://x.edu.cn:8443/a", "https://x.edu.cn/") == "https://x.edu.cn:8443/a"


def test_trailing_path_slash_stripped_but_root_kept():
    assert normalize_url("https://x.edu.cn/szdw/", "https://x.edu.cn/") == "https://x.edu.cn/szdw"
    assert normalize_url("https://x.edu.cn/", "https://x.edu.cn/") == "https://x.edu.cn/"


def test_query_preserved_verbatim_no_reordering():
    # KISS + protects the form-pagination synthetic-URL byte-match: no param sort.
    u = "https://x.edu.cn/list.jsp?py=a&page=2"
    assert normalize_url(u, u) == u


def test_ycl_synthetic_params_preserved_and_idempotent():
    syn = "https://x.edu.cn/xylb.jsp?py=a&__ycl_kind=form&__ycl_form=fromWen&__ycl_field=fromWenNOWPAGE&__ycl_page=2"
    assert normalize_url(syn, syn) == syn  # untouched → node_key/cache key stays stable


def test_non_http_schemes_return_none():
    assert normalize_url("javascript:void(0)", "https://x.edu.cn/") is None
    assert normalize_url("mailto:a@x.edu.cn", "https://x.edu.cn/") is None
    assert normalize_url("tel:123", "https://x.edu.cn/") is None


def test_empty_or_none_href_returns_none():
    assert normalize_url("", "https://x.edu.cn/") is None
    assert normalize_url("   ", "https://x.edu.cn/") is None
    assert normalize_url(None, "https://x.edu.cn/") is None


def test_same_site_exact_host():
    assert same_site("https://x.edu.cn/a", "https://x.edu.cn/b")
    assert not same_site("https://a.x.edu.cn/a", "https://b.x.edu.cn/a")


def test_same_site_loose_registrable_domain():
    # subdomains of the same university collapse under loose matching (edu.cn aware).
    assert same_site("https://math.xjtu.edu.cn/p", "https://www.xjtu.edu.cn/q", loose=True)
    assert not same_site("https://math.xjtu.edu.cn/p", "https://www.pku.edu.cn/q", loose=True)


def test_is_offsite_uses_loose_registrable_domain():
    assert not is_offsite("https://math.xjtu.edu.cn/p", "https://www.xjtu.edu.cn/list.htm")
    assert is_offsite("https://third-party.com/p", "https://www.xjtu.edu.cn/list.htm")
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_page_urls.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.page.urls'`.

- [ ] **Step 3: Implement `urls.py`**

Create `src/dext/page/urls.py`:
```python
"""URL normalization and site comparison (spec §4). Pure stdlib.

normalize_url preserves the query string VERBATIM (no param reordering): this is
deliberate, because form-pagination synthetic URLs (__ycl_* params, see SP3 spec
§5.3) must round-trip byte-identically so they map to one stable graph node.
(SP2's dedup.py docstring mentions "param-sorted"; that was aspirational and is
intentionally NOT implemented here.)
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Common multi-label public suffixes (KISS, no public-suffix-list dependency).
_MULTI_SUFFIXES = frozenset(
    {
        "edu.cn", "com.cn", "org.cn", "net.cn", "gov.cn", "ac.cn",
        "edu.hk", "edu.tw", "edu.mo", "com.hk", "com.tw",
    }
)


def normalize_url(href: str | None, base_url: str) -> str | None:
    """Resolve `href` against `base_url` into a normalized identity URL, or None.

    Steps: strip ends → urljoin to absolute → reject non-http(s) → lowercase host
    → drop default port → strip a trailing path slash (root "/" kept) → drop
    fragment → keep query verbatim.
    """
    if href is None:
        return None
    raw = href.strip()
    if not raw:
        return None
    absolute = urljoin(base_url, raw)
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    netloc = host
    if parts.port is not None and parts.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _registrable_domain(host: str) -> str:
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return last_two


def same_site(a: str, b: str, *, loose: bool = False) -> bool:
    """Same host (default), or same registrable domain when `loose=True`
    (so subdomains of one university collapse together — spec §4 "宽松可配")."""
    ha, hb = _host(a), _host(b)
    if not ha or not hb:
        return False
    if loose:
        return _registrable_domain(ha) == _registrable_domain(hb)
    return ha == hb


def is_offsite(url: str, base: str) -> bool:
    """True if `url` is on a different university/site than `base`
    (loose registrable-domain comparison)."""
    return not same_site(url, base, loose=True)
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_page_urls.py -v
```
Expected: `12 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/urls.py tests/test_page_urls.py
git commit -m "feat(sp3): add URL normalization (verbatim query) + same_site/is_offsite" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `text.py` — html_to_text, content_hash

**Files:**
- Create: `src/dext/page/text.py`
- Test: `tests/test_page_text.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_text.py`:
```python
import hashlib

from dext.page.text import content_hash, html_to_text


def test_html_to_text_extracts_visible_chinese_text():
    out = html_to_text("<html><body><h1>数学学院</h1><p>张三 教授</p></body></html>")
    assert "数学学院" in out
    assert "张三 教授" in out


def test_html_to_text_drops_images_and_link_urls_keeps_anchor_text():
    out = html_to_text('<p><img src="a.png" alt="ALT"><a href="https://x/p">李四</a></p>')
    assert "李四" in out          # anchor text kept
    assert "https://x/p" not in out  # link URL dropped (ignore_links)
    assert "a.png" not in out     # image dropped (ignore_images)


def test_html_to_text_no_hard_wrapping():
    long_line = "教授" * 80
    out = html_to_text(f"<p>{long_line}</p>")
    # body_width=0 → the paragraph is not folded into multiple hard-wrapped lines.
    assert long_line in out.replace("\n", "")
    assert out.count("\n") < 5


def test_html_to_text_handles_empty():
    assert html_to_text("") == ""


def test_content_hash_matches_sha256_utf8():
    html = "<html>张三</html>"
    assert content_hash(html) == hashlib.sha256(html.encode("utf-8")).hexdigest()


def test_content_hash_is_deterministic_and_sensitive():
    assert content_hash("<a>x</a>") == content_hash("<a>x</a>")
    assert content_hash("<a>x</a>") != content_hash("<a>y</a>")
    assert len(content_hash("anything")) == 64
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_page_text.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.page.text'`.

- [ ] **Step 3: Implement `text.py`**

Create `src/dext/page/text.py`:
```python
"""HTML → clean text snapshot (for the SP5 LLM) and content hashing (spec §3).

html2text config (spec §3): no hard wrapping (body_width=0), ignore images,
ignore link URLs (anchor text is kept; URLs are captured separately in
link_signals), keep Unicode (unicode_snob) so Chinese is preserved.
"""

from __future__ import annotations

import hashlib

import html2text


def html_to_text(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.body_width = 0          # no hard line wrapping
    converter.ignore_images = True
    converter.ignore_links = True     # keep anchor text, drop URL noise
    converter.ignore_emphasis = True  # drop **/_ markers for cleaner text
    converter.unicode_snob = True     # keep Chinese rather than ASCII approximations
    return converter.handle(html or "").strip()


def content_hash(raw_html: str) -> str:
    """sha256 of the raw HTML as UTF-8 bytes (overview §6) — used to detect
    content change and avoid duplicate extraction."""
    return hashlib.sha256((raw_html or "").encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_page_text.py -v
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/text.py tests/test_page_text.py
git commit -m "feat(sp3): add html_to_text (html2text config) + content_hash" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `links.py` — LinkSignal, PageSnapshot, build_snapshot (bs4)

**Files:**
- Create: `src/dext/page/links.py`
- Test: `tests/test_page_links.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_links.py`:
```python
from dext.page.links import LinkSignal, PageSnapshot, build_snapshot

_HTML = """
<html><head><title>数学学院 师资</title></head><body>
  <h2>教授</h2>
  <div class="teacher-list">
    <a href="/szdw/zhangsan.htm">张三</a>
    <a href="https://math.x.edu.cn/szdw/lisi.htm">李四</a>
    <a href="/szdw/zhangsan.htm">张三(重复)</a>
    <a href="javascript:void(0)">下一页</a>
    <a href="https://other.com/x">外部</a>
  </div>
</body></html>
"""


def _snap():
    return build_snapshot(
        _HTML,
        requested_url="https://math.x.edu.cn/szdw/index.htm",
        final_url="https://math.x.edu.cn/szdw/index.htm",
        title="",
    )


def test_snapshot_basic_shape():
    snap = _snap()
    assert isinstance(snap, PageSnapshot)
    assert snap.title == "数学学院 师资"  # from <title> when title arg empty
    assert "张三" in snap.text_snapshot
    assert len(snap.content_hash) == 64


def test_links_are_normalized_absolute_and_deduped():
    snap = _snap()
    # javascript: dropped; /szdw/zhangsan.htm appears once; absolute + relative resolved.
    assert "https://math.x.edu.cn/szdw/zhangsan.htm" in snap.links
    assert "https://math.x.edu.cn/szdw/lisi.htm" in snap.links
    assert "https://other.com/x" in snap.links
    assert snap.links.count("https://math.x.edu.cn/szdw/zhangsan.htm") == 1
    assert all("javascript" not in u for u in snap.links)


def test_link_signals_keep_every_anchor_including_duplicate_urls():
    snap = _snap()
    # 5 anchors, javascript: one dropped (normalize → None) → 4 signals (dup URL kept).
    assert len(snap.link_signals) == 4
    zhang = [s for s in snap.link_signals if s.url.endswith("zhangsan.htm")]
    assert len(zhang) == 2  # both anchors to the same URL retained as separate signals


def test_link_signal_fields():
    snap = _snap()
    lisi = next(s for s in snap.link_signals if s.url.endswith("lisi.htm"))
    assert lisi.anchor_text == "李四"
    assert lisi.heading == "教授"                 # nearest preceding heading
    assert lisi.parent_class == "teacher-list"     # nearest ancestor with a class
    assert lisi.path_segments == ["szdw", "lisi.htm"]
    assert lisi.same_site is True                  # same host as final_url


def test_offsite_signal_marked_not_same_site():
    snap = _snap()
    ext = next(s for s in snap.link_signals if s.url == "https://other.com/x")
    assert ext.same_site is False


def test_build_snapshot_handles_empty_html():
    snap = build_snapshot("", "https://x.edu.cn/", "https://x.edu.cn/", "")
    assert snap.links == []
    assert snap.link_signals == []
    assert snap.title == ""
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_page_links.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.page.links'`.

- [ ] **Step 3: Implement `links.py`**

Create `src/dext/page/links.py`:
```python
"""DOM → PageSnapshot using BeautifulSoup4 (html.parser backend), spec §2/§3.

bs4 gives robust navigation (find_previous for the nearest heading, find_parent
for ancestor class) on the malformed HTML common to university sites — more
reliable and readable than a hand-rolled tag-stack parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from dext.page.text import content_hash, html_to_text
from dext.page.urls import normalize_url, same_site

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class LinkSignal:
    href: str  # raw href as written in the HTML
    url: str  # normalized absolute identity URL
    anchor_text: str
    heading: str | None  # nearest preceding/ancestor heading text
    parent_class: str | None  # class of the nearest ancestor that has one
    path_segments: list[str]
    same_site: bool  # same host as the page's base URL


@dataclass
class PageSnapshot:
    url: str  # normalized identity URL (from requested_url)
    final_url: str
    title: str
    text_snapshot: str
    links: list[str]  # normalized absolute URLs, de-duplicated, in document order
    link_signals: list[LinkSignal]  # one per usable anchor (duplicates kept)
    content_hash: str


def _nearest_heading(anchor) -> str | None:
    prev = anchor.find_previous(_HEADING_TAGS)
    if prev is None:
        return None
    text = prev.get_text(" ", strip=True)
    return text or None


def _parent_class(anchor) -> str | None:
    node = anchor.parent
    while node is not None and getattr(node, "name", None) is not None:
        classes = node.get("class") if hasattr(node, "get") else None
        if classes:
            return " ".join(classes)
        node = node.parent
    return None


def _path_segments(url: str) -> list[str]:
    return [seg for seg in urlsplit(url).path.split("/") if seg]


def build_snapshot(html: str, requested_url: str, final_url: str, title: str) -> PageSnapshot:
    soup = BeautifulSoup(html or "", "html.parser")
    base = final_url or requested_url
    identity = normalize_url(requested_url, requested_url) or requested_url

    page_title = title
    if not page_title and soup.title and soup.title.string:
        page_title = soup.title.string.strip()

    signals: list[LinkSignal] = []
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href:
            continue
        url = normalize_url(href, base)
        if url is None:
            continue
        signals.append(
            LinkSignal(
                href=href,
                url=url,
                anchor_text=anchor.get_text(" ", strip=True),
                heading=_nearest_heading(anchor),
                parent_class=_parent_class(anchor),
                path_segments=_path_segments(url),
                same_site=same_site(url, base),
            )
        )
        if url not in seen:
            seen.add(url)
            links.append(url)

    return PageSnapshot(
        url=identity,
        final_url=final_url or requested_url,
        title=page_title or "",
        text_snapshot=html_to_text(html or ""),
        links=links,
        link_signals=signals,
        content_hash=content_hash(html or ""),
    )
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_page_links.py -v
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/links.py tests/test_page_links.py
git commit -m "feat(sp3): add build_snapshot (bs4 DOM → PageSnapshot + LinkSignal)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `pagination.py` — find_url_pagination + find_followup_links

**Files:**
- Create: `src/dext/page/pagination.py` (URL pagination + followup; form states added in Task 7)
- Test: `tests/test_page_pagination.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_pagination.py`:
```python
from dext.page.links import build_snapshot
from dext.page.pagination import (
    FollowupCandidate,
    PaginationCandidate,
    find_followup_links,
    find_url_pagination,
)

_LIST_URL = "https://x.edu.cn/szdw/index.htm"
_PAGINATION_HTML = """
<html><body><div class="pager">
  <a href="/szdw/2.htm">2</a>
  <a href="/szdw/index_3.html">3</a>
  <a href="list.htm?page=2">下一页</a>
  <a href="https://x.edu.cn/news/100.htm">新闻</a>
  <a href="https://other.edu.cn/szdw/2.htm">别校</a>
</div></body></html>
"""


def _snap(html, url=_LIST_URL):
    return build_snapshot(html, url, url, "")


def test_find_url_pagination_recognizes_numeric_and_query_pages():
    cands = find_url_pagination(_snap(_PAGINATION_HTML), _LIST_URL)
    by_url = {c.url: c for c in cands}
    assert "https://x.edu.cn/szdw/2.htm" in by_url
    assert by_url["https://x.edu.cn/szdw/2.htm"].page_index == 2
    assert "https://x.edu.cn/szdw/index_3.html" in by_url
    assert by_url["https://x.edu.cn/szdw/index_3.html"].page_index == 3
    assert "https://x.edu.cn/szdw/list.htm?page=2" in by_url
    assert by_url["https://x.edu.cn/szdw/list.htm?page=2"].page_index == 2


def test_find_url_pagination_excludes_other_columns_and_other_sites():
    cands = find_url_pagination(_snap(_PAGINATION_HTML), _LIST_URL)
    urls = {c.url for c in cands}
    assert "https://x.edu.cn/news/100.htm" not in urls       # different column
    assert "https://other.edu.cn/szdw/2.htm" not in urls     # different site


def test_find_url_pagination_returns_pagination_candidate_type():
    cands = find_url_pagination(_snap(_PAGINATION_HTML), _LIST_URL)
    assert cands and all(isinstance(c, PaginationCandidate) for c in cands)


_FOLLOWUP_HTML = """
<html><body><ul class="cat">
  <a href="/szdw/jiaoshou.htm">教授</a>
  <a href="/szdw/fujiaoshou.htm">副教授</a>
  <a href="/szdw/bodao.htm">博士生导师</a>
  <a href="/szdw/index.htm">师资队伍</a>
  <a href="/news/1.htm">学院新闻</a>
</ul></body></html>
"""


def test_find_followup_links_recognizes_categories():
    cands = find_followup_links(_snap(_FOLLOWUP_HTML), _LIST_URL)
    labels = {c.label for c in cands}
    assert "教授" in labels
    assert "副教授" in labels
    assert "博士生导师" in labels
    assert all(isinstance(c, FollowupCandidate) for c in cands)


def test_find_followup_links_skips_news_and_the_list_itself():
    cands = find_followup_links(_snap(_FOLLOWUP_HTML), _LIST_URL)
    urls = {c.url for c in cands}
    assert "https://x.edu.cn/news/1.htm" not in urls           # not a category
    assert "https://x.edu.cn/szdw/index.htm" not in urls        # the list page itself


def test_find_followup_links_honors_limit():
    anchors = "".join(f'<a href="/szdw/c{i}.htm">教授{i}</a>' for i in range(50))
    snap = _snap(f"<html><body>{anchors}</body></html>")
    cands = find_followup_links(snap, _LIST_URL, limit=36)
    assert len(cands) == 36
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_page_pagination.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.page.pagination'`.

- [ ] **Step 3: Implement `pagination.py` (URL + followup)**

Create `src/dext/page/pagination.py`:
```python
"""Pagination & followup discovery (spec §5, source doc §11). Pure functions.

This module has two parts: simple URL/followup heuristics (this task) and the
faithful byte-exact port of userscripts/src/formPagination.ts (Task 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from dext.page.links import PageSnapshot

# Numeric-page patterns (first match wins). Examples: szdw/2.htm, index_3.html,
# list.htm?page=2.
_PAGE_INDEX_PATTERNS = (
    re.compile(r"[?&](?:page|p|pn|pageno|pagenum|curpage)=(\d+)\b", re.I),
    re.compile(r"index_(\d+)\.html?$", re.I),
    re.compile(r"_(\d+)\.html?$", re.I),
    re.compile(r"/(\d+)\.html?$", re.I),
)

# Conservative faculty-category keywords (anchor text) for followup pages.
_FOLLOWUP_KEYWORDS = (
    "教授", "副教授", "讲师", "助理教授", "研究员", "副研究员",
    "博士生导师", "硕士生导师", "博导", "硕导", "院士",
    "杰出人才", "特聘", "讲席", "专职教师", "兼职教师", "青年教师",
)

# Category anchors that are really the list page header, not a sub-page.
_FOLLOWUP_NEGATIVE = ("师资队伍", "教师名单", "师资力量", "全部")


@dataclass
class PaginationCandidate:
    url: str
    page_index: int | None
    label: str | None


@dataclass
class FollowupCandidate:
    url: str
    label: str


def _page_index_of(url: str) -> int | None:
    for pattern in _PAGE_INDEX_PATTERNS:
        match = pattern.search(url)
        if match:
            return int(match.group(1))
    return None


def _column_dir(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    directory = parts.path.rsplit("/", 1)[0] + "/"
    return (parts.hostname or "").lower(), directory


def find_url_pagination(snapshot: PageSnapshot, faculty_list_url: str) -> list[PaginationCandidate]:
    """Same-site, same-column links that look like numbered pages (source §11.1)."""
    base_host, base_dir = _column_dir(faculty_list_url)
    out: list[PaginationCandidate] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        parts = urlsplit(sig.url)
        if (parts.hostname or "").lower() != base_host:
            continue
        if not parts.path.startswith(base_dir):
            continue
        index = _page_index_of(sig.url)
        if index is None:
            continue
        if sig.url in seen:
            continue
        seen.add(sig.url)
        out.append(PaginationCandidate(url=sig.url, page_index=index, label=sig.anchor_text or None))
    return out


def find_followup_links(
    snapshot: PageSnapshot, faculty_list_url: str, limit: int = 36
) -> list[FollowupCandidate]:
    """Same-site faculty-category entries (source §11.2); capped at `limit`."""
    base_host = (urlsplit(faculty_list_url).hostname or "").lower()
    out: list[FollowupCandidate] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        if (urlsplit(sig.url).hostname or "").lower() != base_host:
            continue
        if sig.url == faculty_list_url:
            continue
        text = sig.anchor_text or ""
        if any(neg in text for neg in _FOLLOWUP_NEGATIVE):
            continue
        if not any(kw in text for kw in _FOLLOWUP_KEYWORDS):
            continue
        if sig.url in seen:
            continue
        seen.add(sig.url)
        out.append(FollowupCandidate(url=sig.url, label=text))
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_page_pagination.py -v
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/pagination.py tests/test_page_pagination.py
git commit -m "feat(sp3): add find_url_pagination + find_followup_links heuristics" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `pagination.py` — form-pagination port (byte-exact) + merge

**Files:**
- Modify: `src/dext/page/pagination.py` (append form-pagination + merge)
- Test: `tests/test_page_form_pagination.py`

This is the **faithful port of `userscripts/src/formPagination.ts`**. The synthetic URL must be byte-identical to the script's (see "Locked contract" above). Re-read that section before implementing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_form_pagination.py`:
```python
from dext.page.pagination import extract_form_pagination_states, merge_pagination_states
from dext.types import PaginationState

# WebPlus-style form pagination: javascript anchors set a form field then submit.
_FORM_HTML = """
<html><body>
  <div class="pages">
    <span class="this-page">1</span>
    <a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='2';document.forms['fromWen'].submit();">2</a>
    <a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='3';document.forms['fromWen'].submit();">3</a>
  </div>
</body></html>
"""
_CURRENT_URL = "https://example.edu.cn/xylb.jsp?py=a"


def test_form_states_parsed_skipping_page_one_and_current():
    states = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    assert [s.page_index for s in states] == [2, 3]  # page 1 = current, skipped
    assert all(isinstance(s, PaginationState) for s in states)


def test_synthetic_url_is_byte_exact():
    states = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    page2 = next(s for s in states if s.page_index == 2)
    # MUST equal formPagination.ts buildSyntheticUrl(...) byte-for-byte.
    assert page2.synthetic_url == (
        "https://example.edu.cn/xylb.jsp?py=a"
        "&__ycl_kind=form&__ycl_form=fromWen&__ycl_field=fromWenNOWPAGE&__ycl_page=2"
    )


def test_state_id_label_fields_and_total_pages():
    states = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    page3 = next(s for s in states if s.page_index == 3)
    assert page3.state_id == "form:fromWen:fromWenNOWPAGE:3"
    assert page3.label == "fromWen 第 3 页"
    assert page3.fields == {"fromWenNOWPAGE": "3"}
    assert page3.submit is True
    assert page3.form_name == "fromWen"
    assert page3.url == _CURRENT_URL
    assert page3.total_pages == 3


def test_gopage_expands_to_one_through_max():
    html = """
    <html><body>
      <a href="javascript:document.forms['fromWen'].fromWenNOWPAGE.value='5';document.forms['fromWen'].submit();">5</a>
      <input name="fromWenGOPAGE" />
    </body></html>
    """
    states = extract_form_pagination_states(html, "https://example.edu.cn/xylb.jsp")
    # GOPAGE present → expand 1..5, skip page 1 (no .this-page, no page param → current=1).
    assert [s.page_index for s in states] == [2, 3, 4, 5]
    assert states[0].total_pages == 5


def test_existing_ycl_params_are_replaced_not_duplicated():
    url = "https://example.edu.cn/xylb.jsp?__ycl_page=9&py=a"
    html = (
        "<a href=\"javascript:document.forms['f'].FIELD.value='2';"
        "document.forms['f'].submit();\">2</a>"
    )
    states = extract_form_pagination_states(html, url)
    syn = states[0].synthetic_url
    assert syn.count("__ycl_page=") == 1
    assert syn.endswith("__ycl_page=2")
    assert "py=a" in syn


def test_no_form_anchors_returns_empty():
    assert extract_form_pagination_states("<a href='/szdw/2.htm'>2</a>", _CURRENT_URL) == []


def test_merge_dedups_by_synthetic_url_reported_wins():
    parsed = extract_form_pagination_states(_FORM_HTML, _CURRENT_URL)
    reported = [
        PaginationState(
            kind="form_submit", state_id="form:fromWen:fromWenNOWPAGE:2",
            label="reported", page_index=2, form_name="fromWen",
            fields={"fromWenNOWPAGE": "2"}, submit=True,
            synthetic_url=parsed[0].synthetic_url, url=_CURRENT_URL, total_pages=3,
        )
    ]
    merged = merge_pagination_states(reported, parsed)
    page2 = [s for s in merged if s.page_index == 2]
    assert len(page2) == 1                 # deduped by synthetic_url
    assert page2[0].label == "reported"    # reported wins
    assert {s.page_index for s in merged} == {2, 3}
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_page_form_pagination.py -v
```
Expected: FAIL — `ImportError: cannot import name 'extract_form_pagination_states'`.

- [ ] **Step 3: Append the form-pagination port to `pagination.py`**

Extend the imports at the top of `src/dext/page/pagination.py`:
```python
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from dext.types import PaginationState
```
(Keep the existing `from urllib.parse import urlsplit` — merge it into the line above so `urlunsplit` is also imported; `re`, `dataclass`, and `PageSnapshot` imports stay.)

Append to the end of `src/dext/page/pagination.py`:
```python
# ── Form pagination — faithful port of userscripts/src/formPagination.ts ──────
_PAGE_ASSIGN_RE = re.compile(
    r"""document\.forms\[['"]([^'"]+)['"]\]\.([A-Za-z0-9_]+)\.value\s*=\s*['"]?(\d+)['"]?""",
    re.I,
)
_GOTO_FIELD_RE = re.compile(r"\b([A-Za-z0-9_]*?)GOPAGE\b", re.I)
_CURRENT_PAGE_PARAMS = ("PAGENUM", "page", "p", "pn", "fromWenNOWPAGE")
_FORM_DEFAULT_PORTS = {"http": 80, "https": 443}

# WHATWG application/x-www-form-urlencoded serializer safe set: ASCII alnum + * - . _
_FORM_SAFE = frozenset(
    b"*-._"
    + bytes(range(0x30, 0x3A))  # 0-9
    + bytes(range(0x41, 0x5B))  # A-Z
    + bytes(range(0x61, 0x7B))  # a-z
)


def _quote_form(value: str) -> str:
    out: list[str] = []
    for byte in value.encode("utf-8"):
        if byte == 0x20:
            out.append("+")
        elif byte in _FORM_SAFE:
            out.append(chr(byte))
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def _unquote_form(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value.replace("+", " "), encoding="utf-8", errors="replace")


def _parse_query_pairs(query: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not query:
        return pairs
    for chunk in query.split("&"):
        if chunk == "":
            continue
        if "=" in chunk:
            key, val = chunk.split("=", 1)
        else:
            key, val = chunk, ""
        pairs.append((_unquote_form(key), _unquote_form(val)))
    return pairs


def _build_synthetic_url(url: str, form_name: str, field_name: str, page_index: int) -> str:
    """Byte-identical to formPagination.ts buildSyntheticUrl (see Locked contract)."""
    parts = urlsplit(url)
    pairs = [(k, v) for (k, v) in _parse_query_pairs(parts.query) if not k.startswith("__ycl_")]
    pairs.append(("__ycl_kind", "form"))
    pairs.append(("__ycl_form", form_name))
    pairs.append(("__ycl_field", field_name))
    pairs.append(("__ycl_page", str(page_index)))
    query = "&".join(f"{_quote_form(k)}={_quote_form(v)}" for k, v in pairs)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    netloc = host
    if parts.port is not None and parts.port != _FORM_DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"
    return urlunsplit((scheme, netloc, parts.path, query, ""))


def _detect_current_page(soup: BeautifulSoup, current_url: str) -> int:
    element = soup.select_one(".this-page")
    if element is not None:
        text = element.get_text(strip=True)
        try:
            value = int(text)
            if value > 0:
                return value
        except ValueError:
            pass
    pairs = _parse_query_pairs(urlsplit(current_url).query)
    for key in _CURRENT_PAGE_PARAMS:
        for pair_key, pair_val in pairs:  # searchParams.get → first match
            if pair_key == key:
                try:
                    value = int(pair_val)
                    if value > 0:
                        return value
                except ValueError:
                    pass
                break
    return 1


def _has_goto(soup: BeautifulSoup, form_name: str, field_name: str) -> bool:
    for inp in soup.find_all("input"):
        name = inp.get("name") or ""
        match = _GOTO_FIELD_RE.search(name)
        if not match:
            continue
        prefix = match.group(1) or ""
        owning_form = inp.find_parent("form")
        form_ok = owning_form is None or (owning_form.get("name") or "") == form_name
        prefix_ok = not prefix or field_name.lower().startswith(prefix.lower())
        if form_ok and prefix_ok:
            return True
    return False


def _expand_page_indexes(
    soup: BeautifulSoup, form_name: str, field_name: str, pages: set[int]
) -> list[int]:
    if not _has_goto(soup, form_name, field_name):
        return sorted(pages)
    return list(range(1, max(pages) + 1))


def extract_form_pagination_states(html: str, current_url: str) -> list[PaginationState]:
    """Backend fallback parse of WebPlus/SiteWeaver form pagination (source §11.3).

    Faithful port of formPagination.ts: group javascript: anchors by (form, field),
    optionally expand via a GOPAGE input, skip page 1 / current page, and build a
    byte-exact synthetic identity URL per page.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    groups: dict[tuple[str, str], set[int]] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith("javascript:"):  # JS selector a[href^="javascript:"]
            continue
        match = _PAGE_ASSIGN_RE.search(href)
        if not match:
            continue
        page = int(match.group(3))
        if page <= 0:
            continue
        groups.setdefault((match.group(1), match.group(2)), set()).add(page)

    current_page = _detect_current_page(soup, current_url)
    states: list[PaginationState] = []
    seen: set[str] = set()
    for (form_name, field_name), pages in groups.items():
        expanded = _expand_page_indexes(soup, form_name, field_name, pages)
        total_pages = max([*expanded, *pages])
        for page in expanded:
            if page <= 1 or page == current_page:
                continue
            synthetic = _build_synthetic_url(current_url, form_name, field_name, page)
            if synthetic in seen:
                continue
            seen.add(synthetic)
            states.append(
                PaginationState(
                    kind="form_submit",
                    state_id=f"form:{form_name}:{field_name}:{page}",
                    label=f"{form_name} 第 {page} 页",
                    page_index=page,
                    total_pages=total_pages,
                    form_name=form_name,
                    fields={field_name: str(page)},
                    submit=True,
                    synthetic_url=synthetic,
                    url=current_url,
                )
            )
    states.sort(key=lambda s: (s.page_index, s.synthetic_url))
    return states


def merge_pagination_states(
    reported: list[PaginationState], parsed: list[PaginationState]
) -> list[PaginationState]:
    """Union of script-reported and backend-parsed states, de-duplicated by
    synthetic_url (reported wins — it reflects the live DOM). Sorted like
    extract_form_pagination_states output."""
    out: list[PaginationState] = []
    seen: set[str] = set()
    for state in [*reported, *parsed]:
        if state.synthetic_url in seen:
            continue
        seen.add(state.synthetic_url)
        out.append(state)
    out.sort(key=lambda s: (s.page_index, s.synthetic_url))
    return out
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_page_form_pagination.py -v
```
Expected: `7 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/pagination.py tests/test_page_form_pagination.py
git commit -m "feat(sp3): port formPagination.ts (byte-exact synthetic URL) + merge_pagination_states" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `candidates.py` — filter_detail_candidates with drop counts

**Files:**
- Create: `src/dext/page/candidates.py`
- Test: `tests/test_page_candidates.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_candidates.py`:
```python
from dext.page.candidates import FilterContext, FilterResult, filter_detail_candidates
from dext.page.links import LinkSignal, PageSnapshot

_LIST_URL = "https://x.edu.cn/szdw/index.htm"


def _sig(url, text="老师", same=True):
    return LinkSignal(
        href=url, url=url, anchor_text=text, heading=None,
        parent_class=None, path_segments=url.split("/")[3:], same_site=same,
    )


def _snap(signals):
    return PageSnapshot(
        url=_LIST_URL, final_url=_LIST_URL, title="", text_snapshot="",
        links=[s.url for s in signals], link_signals=signals, content_hash="h",
    )


def _ctx(**kw):
    return FilterContext(faculty_list_url=_LIST_URL, **kw)


def test_keeps_plain_detail_link():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/zhangsan.htm", "张三")]), _ctx())
    assert isinstance(res, FilterResult)
    assert [s.url for s in res.kept] == ["https://x.edu.cn/szdw/zhangsan.htm"]
    assert sum(res.dropped.values()) == 0


def test_drop_external():
    res = filter_detail_candidates(_snap([_sig("https://other.com/p", "张三", same=False)]), _ctx())
    assert res.kept == []
    assert res.dropped["external"] == 1


def test_drop_retired():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/lao.htm", "张三（退休）")]), _ctx())
    assert res.dropped["retired"] == 1


def test_drop_noise_by_text_and_path():
    sigs = [
        _sig("https://x.edu.cn/szdw/a.htm", "登录"),
        _sig("https://x.edu.cn/search/q.htm", "查询"),
    ]
    res = filter_detail_candidates(_snap(sigs), _ctx())
    assert res.dropped["noise"] == 2


def test_drop_directory():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/all.htm", "教师名单")]), _ctx())
    assert res.dropped["directory"] == 1


def test_drop_unrelated_path():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/xygk/intro.htm", "学院简介")]), _ctx())
    assert res.dropped["unrelated_path"] == 1


def test_drop_duplicate_within_page():
    sigs = [
        _sig("https://x.edu.cn/szdw/z.htm", "张三"),
        _sig("https://x.edu.cn/szdw/z.htm", "张三链接2"),
    ]
    res = filter_detail_candidates(_snap(sigs), _ctx())
    assert len(res.kept) == 1
    assert res.dropped["duplicate"] == 1


def test_drop_already_enriched():
    sig = _sig("https://x.edu.cn/szdw/z.htm", "张三")
    res = filter_detail_candidates(_snap([sig]), _ctx(already_enriched={"https://x.edu.cn/szdw/z.htm"}))
    assert res.kept == []
    assert res.dropped["already_enriched"] == 1


def test_dropped_dict_always_has_all_reason_codes():
    res = filter_detail_candidates(_snap([_sig("https://x.edu.cn/szdw/z.htm", "张三")]), _ctx())
    assert set(res.dropped) == {
        "noise", "directory", "retired", "external",
        "unrelated_path", "duplicate", "already_enriched",
    }
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_page_candidates.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.page.candidates'`.

- [ ] **Step 3: Implement `candidates.py`**

Create `src/dext/page/candidates.py`:
```python
"""Detail-candidate pre-filter (spec §6, source doc §8.4). Cheap, deterministic,
diagnosable: every drop has a reason code and a count. The real "is this a
teacher detail page" judgement is the LLM decider's (SP5); this layer only removes
obvious non-candidates and produces counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dext.page.links import LinkSignal, PageSnapshot
from dext.page.urls import is_offsite

_DROP_CODES = (
    "noise", "directory", "retired", "external",
    "unrelated_path", "duplicate", "already_enriched",
)

_RETIRED_TEXT = ("退休", "荣休", "离任", "名誉", "已故", "去世", "逝世")
_NOISE_TEXT = (
    "登录", "登陆", "搜索", "查询", "下载", "新闻", "通知", "公告", "首页",
    "返回", "上一页", "下一页", "更多", "联系我们", "版权", "管理", "english",
)
_NOISE_URL_TOKENS = ("/login", "/logout", "/search", "/download", "/rss")
_DIRECTORY_TEXT = ("师资队伍", "教师名单", "导师名单", "全部教师", "教师一览", "师资力量")

# People-ish path tokens that protect a same-site link from unrelated_path.
_PEOPLE_TOKENS = (
    "szdw", "teacher", "faculty", "jsdw", "jsml", "rcjs", "professor",
    "people", "master", "doctor", "导师", "教师", "师资", "教授",
)
# Common non-people site sections (top path segment) for unrelated_path.
_NONPEOPLE_SECTIONS = (
    "news", "xwzx", "tzgg", "notice", "xygk", "gk", "kxyj", "kycg",
    "research", "download", "lxwm", "contact", "zsjy", "jyxx", "xshd",
)


@dataclass
class FilterContext:
    faculty_list_url: str
    already_enriched: set[str] = field(default_factory=set)
    same_site_only: bool = True


@dataclass
class FilterResult:
    kept: list[LinkSignal]
    dropped: dict[str, int]


def _is_noise(sig: LinkSignal) -> bool:
    text = (sig.anchor_text or "").lower()
    if any(kw.lower() in text for kw in _NOISE_TEXT):
        return True
    low_url = sig.url.lower()
    return any(token in low_url for token in _NOISE_URL_TOKENS)


def _is_unrelated_path(url: str) -> bool:
    low_url = url.lower()
    if any(token in low_url for token in _PEOPLE_TOKENS):
        return False
    segments = [seg for seg in urlsplit(url).path.split("/") if seg]
    if not segments:
        return False
    return segments[0].lower() in _NONPEOPLE_SECTIONS


def filter_detail_candidates(snapshot: PageSnapshot, context: FilterContext) -> FilterResult:
    dropped = {code: 0 for code in _DROP_CODES}
    kept: list[LinkSignal] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        url = sig.url
        if url in context.already_enriched:
            dropped["already_enriched"] += 1
            continue
        if url in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(url)
        if context.same_site_only and is_offsite(url, context.faculty_list_url):
            dropped["external"] += 1
            continue
        if any(kw in (sig.anchor_text or "") for kw in _RETIRED_TEXT):
            dropped["retired"] += 1
            continue
        if _is_noise(sig):
            dropped["noise"] += 1
            continue
        if any(kw in (sig.anchor_text or "") for kw in _DIRECTORY_TEXT):
            dropped["directory"] += 1
            continue
        if _is_unrelated_path(url):
            dropped["unrelated_path"] += 1
            continue
        kept.append(sig)
    return FilterResult(kept=kept, dropped=dropped)
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_page_candidates.py -v
```
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/candidates.py tests/test_page_candidates.py
git commit -m "feat(sp3): add filter_detail_candidates with per-reason drop counts" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Public interface re-exports + full-suite verification & Definition of Done

**Files:**
- Modify: `src/dext/page/__init__.py` (re-export the public interface)
- Test: none new (verification only)

- [ ] **Step 1: Re-export the public interface from `dext.page`**

Replace the contents of `src/dext/page/__init__.py` with:
```python
"""dext page processing — pure HTML/URL → structured signals.

No network, no DB, no LLM, no global state (spec §1, overview §7).
Public interface (spec §7) re-exported below.
"""

from dext.page.candidates import FilterContext, FilterResult, filter_detail_candidates
from dext.page.links import LinkSignal, PageSnapshot, build_snapshot
from dext.page.pagination import (
    FollowupCandidate,
    PaginationCandidate,
    extract_form_pagination_states,
    find_followup_links,
    find_url_pagination,
    merge_pagination_states,
)
from dext.page.text import content_hash, html_to_text
from dext.page.urls import is_offsite, normalize_url, same_site

__all__ = [
    "build_snapshot",
    "html_to_text",
    "content_hash",
    "normalize_url",
    "same_site",
    "is_offsite",
    "PageSnapshot",
    "LinkSignal",
    "find_url_pagination",
    "find_followup_links",
    "extract_form_pagination_states",
    "merge_pagination_states",
    "PaginationCandidate",
    "FollowupCandidate",
    "filter_detail_candidates",
    "FilterContext",
    "FilterResult",
]
```

- [ ] **Step 2: Run the entire test suite**

Run:
```bash
uv run pytest -v
```
Expected: all green. SP1 + SP2 (79) + SP3 (`2 import + 4 dto + 12 urls + 6 text + 6 links + 6 pagination + 7 form + 9 candidates = 52`) → **131 passed**.

- [ ] **Step 3: Confirm the layer is pure (no network/DB/LLM imports leaked)**

Run:
```bash
grep -rEn "import (openai|aiohttp|requests|sqlalchemy|aiosqlite)|from (openai|aiohttp|requests|sqlalchemy|aiosqlite)" src/dext/page/ && echo "LEAK FOUND" || echo "clean: pure page layer"
```
Expected: `clean: pure page layer`.

- [ ] **Step 4: Confirm the public interface imports as one surface**

Run:
```bash
uv run python -c "from dext.page import build_snapshot, html_to_text, content_hash, normalize_url, same_site, is_offsite, PageSnapshot, LinkSignal, find_url_pagination, find_followup_links, extract_form_pagination_states, merge_pagination_states, PaginationCandidate, FollowupCandidate, filter_detail_candidates, FilterContext, FilterResult; from dext.types import PaginationState, FetchAction; print('public interface OK')"
```
Expected: `public interface OK`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/page/__init__.py
git commit -m "feat(sp3): re-export page public interface; SP3 complete" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

## Definition of Done

- `uv run pytest -v` → all green (target **131 passed**: 79 prior + 52 SP3).
- Public interface exactly as spec §7: `dext.page.{build_snapshot, html_to_text, content_hash, normalize_url, same_site, is_offsite, find_url_pagination, find_followup_links, extract_form_pagination_states, merge_pagination_states, filter_detail_candidates}` + dataclasses `PageSnapshot/LinkSignal/PaginationCandidate/FollowupCandidate/FilterContext/FilterResult`; `PaginationState`/`FetchAction` exported from `dext.types`.
- **Synthetic URL is byte-identical** to `formPagination.ts` (asserted in `test_synthetic_url_is_byte_exact`): existing `__ycl_*` replaced not duplicated, params in `kind/form/field/page` order, WHATWG form-urlencoded serialization, default port dropped, no fragment.
- `extract_form_pagination_states` faithfully reproduces the port contract: page-assign + GOPAGE regexes, `.this-page`→URL-param current-page detection, skip page≤1/current, GOPAGE 1..max expansion, `state_id`/`label`/`fields`/`total_pages`, dedup by synthetic_url, `(page_index, synthetic_url)` sort.
- `normalize_url` keeps query **verbatim** (no param sorting), strips fragment, lowercases host, drops default port, strips trailing path slash, returns None for non-http(s); `__ycl_*` synthetic URLs round-trip unchanged.
- `filter_detail_candidates` returns all seven drop-reason codes (`noise, directory, retired, external, unrelated_path, duplicate, already_enriched`) with correct counts; conservative heuristics (the LLM makes the real call in SP5).
- Pure layer: no `openai`/`aiohttp`/`requests`/`sqlalchemy`/`aiosqlite` imports in `src/dext/page/` (grep-verified).
- All work committed on branch `sp3-page-processing`.

## Self-Review (performed while writing this plan)

**1. Spec coverage** — every SP3 spec section maps to a task:
- §2 data structures (`LinkSignal`, `PageSnapshot`) → Task 5; (`PaginationState`) → Task 2.
- §3 text/links (`html_to_text`, `build_snapshot` via bs4, `content_hash`) → Tasks 4 + 5.
- §4 URL normalization (`normalize_url` relative→abs / fragment / host / port / trailing slash / verbatim query / `__ycl_` preserve / non-http→None, `same_site`, `is_offsite`) → Task 3.
- §5.1 URL pagination (`find_url_pagination`) → Task 6; §5.2 followup (`find_followup_links`, limit) → Task 6; §5.3 form pagination (`extract_form_pagination_states` + merge) → Task 7.
- §6 candidate filter (`filter_detail_candidates`, `FilterResult`, all seven drop codes) → Task 8.
- §7 public interface → re-exported + verified Task 9.
- §8 test list (text/links, URL normalization incl. `__ycl_` + `javascript:`→None, URL/followup/form pagination, candidate drop counts) → Tasks 3–8.
- §9 "不做" (no lxml, no ML, no JS rendering, no aggressive query canonicalization) → none introduced; bs4 uses `html.parser`, query kept verbatim. ✓

**2. Bug-risk-memo guards** — the synthetic-URL byte-match (memo's #1 SP3 trap) is enforced by `_build_synthetic_url` (faithful WHATWG form-urlencoded port) and a dedicated byte-equality test; the exact `PAGE_ASSIGN_RE`/`GOPAGE` regexes and `state_id = form:NAME:FIELD:N` from the memo are reproduced; `detectCurrentPage` replicates `.this-page`→`[PAGENUM,page,p,pn,fromWenNOWPAGE]`; `normalize_url` keeps `__ycl_*` query verbatim so reported vs parsed states map to one node. Late-`/complete` idempotency and cross-org detail dedup are SP4/SP6 concerns, not SP3.

**3. Type/name consistency** — `PageSnapshot`/`LinkSignal` fields used identically in `links.py`, `pagination.py`, `candidates.py`, and tests; `PaginationCandidate`/`FollowupCandidate`/`FilterContext`/`FilterResult` and the function names (`build_snapshot`, `html_to_text`, `content_hash`, `normalize_url`, `same_site`, `is_offsite`, `find_url_pagination`, `find_followup_links`, `extract_form_pagination_states`, `merge_pagination_states`, `filter_detail_candidates`) match across tasks, tests, and the `__init__` re-export. `PaginationState` field set matches `types.ts`. `find_followup_links(snapshot, faculty_list_url, limit=36)` signature is consistent. No forward references to undefined symbols.

**Known, accepted limitations (documented, not bugs):** `localeCompare` vs Python code-point sort is equivalent for the ASCII synthetic URLs in scope; non-ASCII URL *paths* (rare on these sites) are not re-encoded to match WHATWG path serialization — only the query is, which is what `URLSearchParams` controls; followup/noise/directory keyword lists are deliberately conservative because the LLM (SP5) makes the final detail-page call.
