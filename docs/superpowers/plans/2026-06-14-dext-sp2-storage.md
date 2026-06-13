# SP2 — Storage (schema + DB lifecycle + DB worker + dedup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build dext's data foundation — every SQLite table (SQLAlchemy 2.0 async / aiosqlite), per-university DB lifecycle (fresh/backup/resume), the single-writer DB worker, and dedup/upsert — as a pure data layer with **no network, no LLM, no HTML parsing**.

**Architecture:** A new `src/dext/storage/` package of five focused modules — `models.py` (ORM `Base` + all tables + the shared `StrEnum`s), `db.py` (async engine, PRAGMA wiring, session factory, `create_all`/`ensure_columns`, `StorageHandle`), `dedup.py` (pure `node_key_for`/`name_key`/academician detection + the session-based `save_professors`), `writer.py` (`DBWriter`: one coroutine draining an `asyncio.Queue`, serial commit, all write commands), and `lifecycle.py` (`resolve_db_path`/`open_fresh`/`open_resume`). The three shared enums (`NodeType`/`EdgeType`/`NodeStatus`) are defined in `models.py` and re-exported from `dext/types.py` (overview §5). All writes flow through the single DB worker; reads use independent sessions (WAL).

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 async, aiosqlite, pydantic v2, pytest + pytest-asyncio, uv. (sqlalchemy/aiosqlite already declared in `pyproject.toml`; `pytest-asyncio` is added in Task 1.)

**Spec:** `docs/superpowers/specs/2026-06-13-dext-02-storage-design.md` (authoritative schema mirrors source doc `target-graph-human-assisted-crawler.md` §5/§9/§13/§14; overview §2/§6/§7).

---

## Pre-verified facts (don't re-derive)

- SP1 is implemented and green: `dext.config.get_settings()` (Settings has `data_dir=Path("data/universities")`), `dext.seed` (`UniversitySeed`, `resolve_abbr`, `db_filename`). `git log` shows the SP1 commits; current tests pass with `uv run pytest`.
- `pyproject.toml` already declares `sqlalchemy>=2.0.30`, `aiosqlite>=0.19.0`, `pydantic>=2.7.0`. Dev group has only `pytest>=8.0.0` — **no `pytest-asyncio` yet** (added in Task 1).
- Pytest config in `pyproject.toml`: `testpaths=["tests"]`, `pythonpath=["src"]`. Tests import `dext.*` directly (no install step).
- `.python-version` is `3.11`, so `enum.StrEnum` (added in 3.11) is available.
- This **is** a git repo (SP1 ran `git init -b main`; commits exist on `main` after the `sp1-foundations` work). Branch SP2 off the current branch.
- Existing test style: plain `pytest`, `tmp_path`/`monkeypatch`, hermetic (`get_settings.cache_clear()`); UTF-8 Chinese literals used freely.

## Enum / schema reference (locked from spec §3 + source §5/§13)

**`NodeType`** (StrEnum): `org_listing_url, org_unit, faculty_list_url, pagination_url, faculty_followup_url, detail_url`
**`EdgeType`** (StrEnum): `seeded_from_manifest, discovered_on_page, belongs_to_org_unit, pagination_of, detail_candidate_of, blocked_by`
**`NodeStatus`** (StrEnum): `pending, in_progress, retry, done, failed, skipped`

Free-string status fields (stored as plain `String`, allowed values documented in comments, no DB enum):
- `university_meta.crawl_status`: `pending|in_progress|completed|failed`
- `crawl_runs.mode`: `fresh|resume`; `crawl_runs.status`: `running|completed|failed|cancelled`
- `org_units.status`: `pending|in_progress|completed|failed|no_faculty_page`
- `crawl_extraction_attempts.status`: `running|succeeded|retry|failed|skipped`
- `crawl_extraction_failures.resolver`: `retry|dropped|manual`

## File Structure

```
dext/
├─ .env.example               # CREATE (Task 1) — documents DEEPSEEK_API_KEY + all DEXT_* settings
├─ pyproject.toml             # MODIFY (Task 1) — add pytest-asyncio dev dep + asyncio_mode
├─ src/dext/
│  ├─ types.py                # CREATE (Task 2) — re-export enums + ProfessorPayload (shared DTOs)
│  └─ storage/
│     ├─ __init__.py          # CREATE (Task 1) — package marker
│     ├─ models.py            # CREATE (Task 2 enums, Task 3 tables) — Base, TimestampMixin, 11 tables, enums
│     ├─ db.py                # CREATE (Task 4) — engine, PRAGMA, session factory, create_all/ensure_columns, StorageHandle
│     ├─ dedup.py             # CREATE (Task 5 pure fns, Task 8 save_professors)
│     ├─ writer.py            # CREATE (Task 6 core+graph cmds, Task 7 aux cmds, Task 8 save cmd) — DBWriter + input specs
│     └─ lifecycle.py         # CREATE (Task 9) — resolve_db_path, open_fresh, open_resume
└─ tests/
   ├─ test_storage_types.py        # Task 2
   ├─ test_storage_models.py       # Task 3 (metadata introspection, no engine)
   ├─ test_storage_db.py           # Task 4 (async: PRAGMA, round-trip, JSON, ensure_columns)
   ├─ test_storage_dedup_keys.py   # Task 5 (pure)
   ├─ test_storage_writer.py       # Task 6 + 7 (async)
   ├─ test_storage_dedup_save.py   # Task 8 (async)
   └─ test_storage_lifecycle.py    # Task 9 (async)
```

Module boundaries: `models.py` imports only SQLAlchemy/enum. `db.py` imports `models`. `dedup.py` imports `models` + `dext.types`. `writer.py` imports `models` + `dedup`. `lifecycle.py` imports `db` + `models` + `writer`. `dext/types.py` imports enums from `models`. No cycles.

**Test command (from repo root `D:\pyprj\dext`):** `uv run pytest <path> -v`

---

### Task 1: Scaffolding — branch, async test deps, `.env.example`, storage package

**Files:**
- Modify: `pyproject.toml`
- Create: `.env.example`
- Create: `src/dext/storage/__init__.py`
- Create: `tests/test_storage_import.py`

- [ ] **Step 1: Branch for SP2 work**

Run (from `D:\pyprj\dext`):
```bash
git checkout -b sp2-storage
```
Expected: `Switched to a new branch 'sp2-storage'`.

- [ ] **Step 2: Add `pytest-asyncio` dev dependency and configure asyncio mode**

In `pyproject.toml`, change the `[dependency-groups]` dev list and the pytest config:
```toml
[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]
```
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
asyncio_mode = "auto"
```
`asyncio_mode = "auto"` lets `async def test_*` run without a per-test marker.

- [ ] **Step 3: Create `.env.example`**

Create `.env.example` (committed; the real `.env` stays gitignored). Only `DEEPSEEK_API_KEY` is needed, and only at SP5+ — SP2 reads none of this:
```dotenv
# dext environment configuration. Copy to `.env` and fill in.
# `.env` is gitignored; this example is committed. Settings live in src/dext/config.py.
# Only DEEPSEEK_API_KEY matters, and only from SP5 onward — SP1–SP4 read none of this.

# ── LLM (required before SP5; UNPREFIXED name) ───────────────────────────────
DEEPSEEK_API_KEY=

# ── LLM tuning (optional; DEXT_-prefixed) ────────────────────────────────────
# DEXT_LLM_BASE_URL=https://api.deepseek.com
# DEXT_LLM_MODEL=deepseek-chat
# DEXT_LLM_MODEL_RETRY=deepseek-reasoner
# DEXT_LLM_ENABLE_THINKING=false
# DEXT_LLM_WORKERS=4
# DEXT_INVALID_JSON_MAX_RETRY=2

# ── Paths ────────────────────────────────────────────────────────────────────
# DEXT_DATA_DIR=data/universities
# DEXT_SEED_PATH=entrances.yaml

# ── Fetch bridge (browser ↔ backend; fixed-contract port) ────────────────────
# DEXT_BRIDGE_HOST=127.0.0.1
# DEXT_BRIDGE_PORT=21520
# DEXT_FETCH_TIMEOUT_SECONDS=60

# ── Scheduling / retry ───────────────────────────────────────────────────────
# DEXT_MAX_DEPTH=4
# DEXT_MAX_ATTEMPTS=3
# DEXT_FOLLOWUP_PAGE_LIMIT=36
# DEXT_ATTEMPT_PENALTY=5.0

# ── Logging ──────────────────────────────────────────────────────────────────
# DEXT_LOG_LEVEL=INFO
```

- [ ] **Step 4: Create the storage package marker**

Create `src/dext/storage/__init__.py`:
```python
"""dext storage — schema, DB lifecycle, single-writer DB worker, dedup.

The only write path in the system. Pure data layer: no network, no LLM,
no HTML parsing (source doc §5/§9/§13/§14, overview §2/§7).
"""
```

- [ ] **Step 5: Write the import smoke test**

Create `tests/test_storage_import.py`:
```python
def test_storage_package_imports():
    import dext.storage  # noqa: F401
```

- [ ] **Step 6: Sync deps and run the smoke test**

Run:
```bash
uv run pytest tests/test_storage_import.py -v
```
Expected: `1 passed` (first run triggers a `uv` sync that installs `pytest-asyncio`).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example src/dext/storage/__init__.py tests/test_storage_import.py uv.lock
git commit -m "chore(sp2): scaffold storage package + pytest-asyncio + .env.example" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Shared enums + `dext.types`

**Files:**
- Create: `src/dext/storage/models.py` (enums only in this task)
- Create: `src/dext/types.py`
- Test: `tests/test_storage_types.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_types.py`:
```python
from dext.storage.models import NodeType, EdgeType, NodeStatus
from dext.types import (
    NodeType as TNodeType,
    EdgeType as TEdgeType,
    NodeStatus as TNodeStatus,
    ProfessorPayload,
)


def test_node_type_values():
    assert {t.value for t in NodeType} == {
        "org_listing_url", "org_unit", "faculty_list_url",
        "pagination_url", "faculty_followup_url", "detail_url",
    }


def test_edge_type_values():
    assert {e.value for e in EdgeType} == {
        "seeded_from_manifest", "discovered_on_page", "belongs_to_org_unit",
        "pagination_of", "detail_candidate_of", "blocked_by",
    }


def test_node_status_values():
    assert {s.value for s in NodeStatus} == {
        "pending", "in_progress", "retry", "done", "failed", "skipped",
    }


def test_strenum_serializes_as_plain_string():
    # StrEnum members ARE str, so they persist into String columns as the value.
    assert NodeStatus.pending == "pending"
    assert f"{NodeType.detail_url}" == "detail_url"


def test_types_reexports_same_enum_objects():
    assert TNodeType is NodeType
    assert TEdgeType is EdgeType
    assert TNodeStatus is NodeStatus


def test_professor_payload_defaults():
    p = ProfessorPayload(name="张三")
    assert p.name == "张三"
    assert p.title is None
    assert p.email is None
    assert p.research_areas is None
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_types.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.storage.models'`.

- [ ] **Step 3: Create `models.py` with the enums**

Create `src/dext/storage/models.py`:
```python
"""ORM models, shared enums, and the declarative Base for dext storage.

Schema mirrors source doc §13 (table-by-table) and §5 (graph enums). Enums are
StrEnum so members are plain strings that persist directly into String columns
and compare equal to their value (e.g. NodeStatus.pending == "pending").
"""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    org_listing_url = "org_listing_url"
    org_unit = "org_unit"
    faculty_list_url = "faculty_list_url"
    pagination_url = "pagination_url"
    faculty_followup_url = "faculty_followup_url"
    detail_url = "detail_url"


class EdgeType(StrEnum):
    seeded_from_manifest = "seeded_from_manifest"
    discovered_on_page = "discovered_on_page"
    belongs_to_org_unit = "belongs_to_org_unit"
    pagination_of = "pagination_of"
    detail_candidate_of = "detail_candidate_of"
    blocked_by = "blocked_by"


class NodeStatus(StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    retry = "retry"
    done = "done"
    failed = "failed"
    skipped = "skipped"
```

- [ ] **Step 4: Create `dext/types.py`**

Create `src/dext/types.py`:
```python
"""Cross-subproject shared value objects (overview §5).

Enums are DEFINED in dext.storage.models and re-exported here so other layers
(SP3–SP7) import them without depending on the ORM module directly.
ProfessorPayload is the contract SP5's extractor/sanitizer fills and SP2's
save_professors consumes; SP5 may add fields but must not rename these.
"""

from __future__ import annotations

from dataclasses import dataclass

from dext.storage.models import EdgeType, NodeStatus, NodeType

__all__ = ["NodeType", "EdgeType", "NodeStatus", "ProfessorPayload"]


@dataclass
class ProfessorPayload:
    """One extracted professor record (pre-dedup). All fields except name optional."""

    name: str
    title: str | None = None
    research_areas: str | None = None  # multi-value joined by "；"
    email: str | None = None
    phone: str | None = None
    homepage: str | None = None  # on-campus profile URL
    external_link: str | None = None  # external/3rd-party homepage
    bio: str | None = None
    enrollment_pref: str | None = None  # 博导/硕导 etc.
    publications: str | None = None
```

- [ ] **Step 5: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_types.py -v
```
Expected: `6 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/dext/storage/models.py src/dext/types.py tests/test_storage_types.py
git commit -m "feat(sp2): add shared NodeType/EdgeType/NodeStatus enums + ProfessorPayload" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: ORM table models (all 11 tables)

**Files:**
- Modify: `src/dext/storage/models.py` (append `Base`, `TimestampMixin`, all tables)
- Test: `tests/test_storage_models.py`

These tests introspect `Base.metadata` and need **no engine** (fast, pure).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_models.py`:
```python
from dext.storage.models import Base


def _table(name):
    return Base.metadata.tables[name]


def test_all_expected_tables_present():
    assert set(Base.metadata.tables) == {
        "university_meta", "crawl_runs", "org_units", "crawl_graph_nodes",
        "crawl_graph_edges", "crawl_page_cache", "crawl_extraction_attempts",
        "crawl_extraction_failures", "professors", "academicians",
        "professor_affiliations",
    }


def test_graph_nodes_columns():
    cols = set(_table("crawl_graph_nodes").columns.keys())
    assert {
        "id", "node_key", "type", "url", "org_unit_id", "org_unit_name",
        "status", "priority_score", "base_priority", "confidence", "depth",
        "attempt_count", "max_attempts", "last_error", "run_id", "claimed_at",
        "completed_at", "next_retry_at", "content_hash", "metadata_json",
        "created_at", "updated_at",
    } <= cols


def test_node_key_is_unique():
    assert _table("crawl_graph_nodes").columns["node_key"].unique is True


def test_claim_index_on_status_priority():
    idx_cols = {tuple(c.name for c in i.columns) for i in _table("crawl_graph_nodes").indexes}
    assert ("status", "priority_score") in idx_cols


def test_org_units_unique_name_and_url():
    t = _table("org_units")
    assert t.columns["name"].unique is True
    assert t.columns["url"].unique is True


def test_page_cache_primary_key_is_url():
    pk = [c.name for c in _table("crawl_page_cache").primary_key.columns]
    assert pk == ["url"]


def test_edges_unique_triple():
    from sqlalchemy import UniqueConstraint

    t = _table("crawl_graph_edges")
    uniques = {
        tuple(c.name for c in con.columns)
        for con in t.constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("from_node_id", "to_node_id", "edge_type") in uniques


def test_affiliation_and_academician_uniques():
    from sqlalchemy import UniqueConstraint

    aff = {
        tuple(c.name for c in con.columns)
        for con in Base.metadata.tables["professor_affiliations"].constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("professor_id", "org_unit_id") in aff

    aca = {
        tuple(c.name for c in con.columns)
        for con in Base.metadata.tables["academicians"].constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("name", "org_unit_id") in aca
    assert ("org_unit_id", "name_key") in aca


def test_professors_have_no_name_key_column():
    # By design (spec §3.9): professor dedup is code-only, no name_key hard key.
    assert "name_key" not in _table("professors").columns.keys()
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_models.py -v
```
Expected: FAIL — `ImportError: cannot import name 'Base'`.

- [ ] **Step 3: Append `Base`, mixin, and all tables to `models.py`**

Append to `src/dext/storage/models.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

SCHEMA_VERSION = 1


def utcnow_iso() -> str:
    """UTC timestamp as ISO-8601 text. Uniform format → lexicographic ordering
    matches chronological ordering (used for next_retry_at comparisons)."""
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(
        String, default=utcnow_iso, onupdate=utcnow_iso
    )


class UniversityMeta(TimestampMixin, Base):
    __tablename__ = "university_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    start_url: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    crawl_status: Mapped[str] = mapped_column(String, default="pending")  # pending|in_progress|completed|failed
    abbr: Mapped[str | None] = mapped_column(String)
    schema_version: Mapped[int] = mapped_column(Integer, default=SCHEMA_VERSION)
    last_run_id: Mapped[int | None] = mapped_column(Integer)


class CrawlRun(TimestampMixin, Base):
    __tablename__ = "crawl_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # fresh|resume
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="running")  # running|completed|failed|cancelled
    backup_path: Mapped[str | None] = mapped_column(String)
    settings_json: Mapped[dict | None] = mapped_column(JSON)
    summary_json: Mapped[dict | None] = mapped_column(JSON)


class OrgUnit(TimestampMixin, Base):
    __tablename__ = "org_units"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # synthetic "about:org_unit:<slug>" when the college has no homepage (NOT NULL + unique)
    url: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kind: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|in_progress|completed|failed|no_faculty_page
    discovered_from_url: Mapped[str | None] = mapped_column(String)


class GraphNode(TimestampMixin, Base):
    __tablename__ = "crawl_graph_nodes"
    __table_args__ = (
        Index("ix_nodes_status_priority", "status", "priority_score"),
        Index("ix_nodes_type", "type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # NodeType value
    url: Mapped[str] = mapped_column(String, nullable=False)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id"))
    org_unit_name: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)  # NodeStatus value
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    base_priority: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float | None] = mapped_column(Float)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(Integer)
    claimed_at: Mapped[str | None] = mapped_column(String)
    completed_at: Mapped[str | None] = mapped_column(String)
    next_retry_at: Mapped[str | None] = mapped_column(String)
    content_hash: Mapped[str | None] = mapped_column(String)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class GraphEdge(TimestampMixin, Base):
    __tablename__ = "crawl_graph_edges"
    __table_args__ = (
        UniqueConstraint("from_node_id", "to_node_id", "edge_type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"), nullable=False)
    to_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"), nullable=False)
    edge_type: Mapped[str] = mapped_column(String, nullable=False)  # EdgeType value
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class PageCache(TimestampMixin, Base):
    __tablename__ = "crawl_page_cache"
    # PK is the identity URL (synthetic URL for form pagination) — upsert by url.
    url: Mapped[str] = mapped_column(String, primary_key=True)
    final_url: Mapped[str | None] = mapped_column(String)
    status_code: Mapped[int | None] = mapped_column(Integer)
    text_snapshot: Mapped[str | None] = mapped_column(Text)
    links_json: Mapped[list | None] = mapped_column(JSON)
    link_signals_json: Mapped[list | None] = mapped_column(JSON)
    block_reason: Mapped[str | None] = mapped_column(String)
    html_snapshot: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String)
    snapshot_encoding: Mapped[str] = mapped_column(String, default="utf-8")
    title: Mapped[str | None] = mapped_column(String)
    fetch_action_json: Mapped[dict | None] = mapped_column(JSON)


class ExtractionAttempt(Base):
    __tablename__ = "crawl_extraction_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="running")  # running|succeeded|retry|failed|skipped
    prompt_hash: Mapped[str | None] = mapped_column(String)
    input_cache_url: Mapped[str | None] = mapped_column(String)
    raw_output_preview: Mapped[str | None] = mapped_column(Text)
    failure_type: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    finished_at: Mapped[str | None] = mapped_column(String)


class ExtractionFailure(Base):
    __tablename__ = "crawl_extraction_failures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    failure_type: Mapped[str | None] = mapped_column(String)
    resolver: Mapped[str | None] = mapped_column(String)  # retry|dropped|manual
    raw_arguments_preview: Mapped[str | None] = mapped_column(Text)
    professor_name_hint: Mapped[str | None] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)


class Professor(TimestampMixin, Base):
    __tablename__ = "professors"
    # No (org_unit_id, name_key) hard constraint by design (spec §3.9): dedup is
    # code-driven (dedup.save_professors); college membership lives in affiliations.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # normalized display name
    org_unit_name: Mapped[str | None] = mapped_column(String)  # redundant merged-college display
    title: Mapped[str | None] = mapped_column(String)
    research_areas: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    homepage: Mapped[str | None] = mapped_column(String)
    external_link: Mapped[str | None] = mapped_column(String)
    bio: Mapped[str | None] = mapped_column(Text)
    enrollment_pref: Mapped[str | None] = mapped_column(String)
    publications: Mapped[str | None] = mapped_column(Text)


class Academician(TimestampMixin, Base):
    __tablename__ = "academicians"
    __table_args__ = (
        UniqueConstraint("name", "org_unit_id"),
        UniqueConstraint("org_unit_id", "name_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_key: Mapped[str] = mapped_column(String, nullable=False)
    org_unit_id: Mapped[int] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    homepage: Mapped[str | None] = mapped_column(String)
    external_link: Mapped[str | None] = mapped_column(String)


class ProfessorAffiliation(Base):
    __tablename__ = "professor_affiliations"
    __table_args__ = (
        UniqueConstraint("professor_id", "org_unit_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), nullable=False)
    org_unit_id: Mapped[int] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_models.py -v
```
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/models.py tests/test_storage_models.py
git commit -m "feat(sp2): add all 11 ORM tables + constraints/indexes (source doc §13)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `db.py` — engine, PRAGMA, sessions, create_all/ensure_columns, StorageHandle

**Files:**
- Create: `src/dext/storage/db.py`
- Test: `tests/test_storage_db.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_db.py`:
```python
import pytest
from sqlalchemy import select

from dext.storage.db import (
    StorageError,
    create_engine_for_path,
    create_all,
    ensure_columns,
    make_session_factory,
)
from dext.storage.models import GraphNode, NodeStatus, NodeType


async def _engine(tmp_path):
    eng = create_engine_for_path(tmp_path / "t.db")
    await create_all(eng)
    return eng


async def test_pragmas_applied(tmp_path):
    eng = await _engine(tmp_path)
    async with eng.connect() as conn:
        jm = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
    assert jm.lower() == "wal"
    assert fk == 1
    await eng.dispose()


async def test_round_trip_insert_and_read(tmp_path):
    eng = await _engine(tmp_path)
    sf = make_session_factory(eng)
    async with sf() as s:
        s.add(GraphNode(node_key="k1", type=NodeType.detail_url, url="https://x", status=NodeStatus.pending))
        await s.commit()
    async with sf() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.node_key == "k1"))).scalar_one()
        assert node.type == "detail_url"
        assert node.status == "pending"
        assert node.attempt_count == 0
        assert node.max_attempts == 3
        assert node.created_at  # timestamp populated
    await eng.dispose()


async def test_json_column_preserves_unicode(tmp_path):
    # ensure_ascii=False must keep Chinese readable in the stored JSON text.
    eng = await _engine(tmp_path)
    sf = make_session_factory(eng)
    async with sf() as s:
        s.add(GraphNode(node_key="k2", type=NodeType.org_unit, url="about:org_unit:x",
                        metadata_json={"discovery_source": "数学学院"}))
        await s.commit()
    async with eng.connect() as conn:
        raw = (await conn.exec_driver_sql(
            "SELECT metadata_json FROM crawl_graph_nodes WHERE node_key='k2'"
        )).scalar()
    assert "数学学院" in raw  # not \uXXXX-escaped
    await eng.dispose()


async def test_ensure_columns_adds_missing_column(tmp_path):
    # Simulate an older DB missing a column, then heal it.
    eng = create_engine_for_path(tmp_path / "old.db")
    async with eng.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE org_units (id INTEGER PRIMARY KEY, name TEXT, url TEXT)")
    await ensure_columns(eng)  # should add kind/status/discovered_from_url/created_at/updated_at
    async with eng.connect() as conn:
        cols = {row[1] for row in (await conn.exec_driver_sql("PRAGMA table_info('org_units')")).all()}
    assert {"kind", "status", "discovered_from_url", "created_at", "updated_at"} <= cols
    await eng.dispose()


async def test_create_engine_rejects_empty_path():
    with pytest.raises(StorageError):
        create_engine_for_path("")
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_db.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.storage.db'`.

- [ ] **Step 3: Implement `db.py`**

Create `src/dext/storage/db.py`:
```python
"""Async engine, PRAGMA wiring, session factory, schema create/heal, StorageHandle.

One engine per university DB. PRAGMAs (WAL, busy_timeout, NORMAL, foreign_keys)
are set on every new DBAPI connection. JSON columns serialize with
ensure_ascii=False so Chinese text stays readable in the stored TEXT (overview §6).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from dext.storage.models import Base

if TYPE_CHECKING:  # avoid importing the writer at module load (no cycle)
    from dext.storage.writer import DBWriter

# SQLite type fallbacks for ensure_columns ALTER TABLE (KISS: nullable adds only).
_SQLITE_AFFINITY = {"INTEGER": "INTEGER", "FLOAT": "REAL", "TEXT": "TEXT", "JSON": "TEXT", "VARCHAR": "TEXT"}


class StorageError(Exception):
    """A storage-lifecycle problem (bad path, missing DB on resume, etc.)."""


def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def create_engine_for_path(db_path) -> AsyncEngine:
    """Create an async engine for a SQLite file path, with PRAGMAs + UTF-8 JSON."""
    if not str(db_path):
        raise StorageError("create_engine_for_path requires a non-empty path")
    abs_posix = Path(db_path).resolve().as_posix()  # cross-platform sqlite URL
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{abs_posix}",
        json_serializer=_json_dumps,
        json_deserializer=json.loads,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    # expire_on_commit=False: returned ids/attrs stay usable after commit.
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Idempotently create every table (no-op for tables that already exist)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_columns(engine: AsyncEngine) -> None:
    """Lightweight forward-migration: ADD COLUMN for any column missing from an
    existing table (resume path, spec §7). Nullable adds only — no Alembic."""
    async with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            info = (await conn.exec_driver_sql(f"PRAGMA table_info('{table.name}')")).all()
            if not info:
                continue  # table absent entirely → create_all handles it
            existing = {row[1] for row in info}
            for col in table.columns:
                if col.name in existing:
                    continue
                affinity = _SQLITE_AFFINITY.get(col.type.__class__.__name__.upper(), "TEXT")
                await conn.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {affinity}'
                )


class StorageHandle:
    """Owns one university DB: the engine, the single-writer DBWriter (running as
    a task), and a read-session factory. close() drains the writer then disposes."""

    def __init__(self, engine: AsyncEngine, writer: "DBWriter", session_factory, *, university_id=None):
        self.engine = engine
        self.writer = writer
        self.session_factory = session_factory
        self.university_id = university_id
        self._writer_task = None

    def start_writer(self) -> None:
        import asyncio

        self._writer_task = asyncio.create_task(self.writer.run())

    @asynccontextmanager
    async def session(self):
        """A fresh read/ad-hoc AsyncSession (WAL allows concurrent reads)."""
        async with self.session_factory() as s:
            yield s

    async def close(self) -> None:
        await self.writer.stop()
        if self._writer_task is not None:
            await self._writer_task
        await self.engine.dispose()


async def healthcheck(engine: AsyncEngine) -> bool:
    """True if the DB answers a trivial query (used by lifecycle)."""
    async with engine.connect() as conn:
        return (await conn.execute(text("SELECT 1"))).scalar() == 1
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_db.py -v
```
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/db.py tests/test_storage_db.py
git commit -m "feat(sp2): add async engine, PRAGMA, sessions, create_all/ensure_columns, StorageHandle" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `dedup.py` pure functions — node_key, name_key, academician detection

**Files:**
- Create: `src/dext/storage/dedup.py` (pure functions + SaveResult; `save_professors` added in Task 8)
- Test: `tests/test_storage_dedup_keys.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_dedup_keys.py`:
```python
from dext.storage.dedup import SaveResult, looks_like_academician, name_key, node_key_for
from dext.storage.models import NodeType


def test_node_key_org_unit_by_id():
    assert node_key_for(NodeType.org_unit, org_unit_id=7) == "org_unit:id:7"


def test_node_key_org_unit_by_name_when_no_id():
    assert node_key_for(NodeType.org_unit, normalized_name="数学学院") == "org_unit:name:数学学院"


def test_node_key_url_node_includes_org_unit_id():
    key = node_key_for(NodeType.detail_url, normalized_url="https://x/p1", org_unit_id=7)
    assert key == "detail_url:org:7:url:https://x/p1"


def test_node_key_url_node_without_org_unit_uses_none():
    key = node_key_for(NodeType.org_listing_url, normalized_url="https://x/list")
    assert key == "org_listing_url:org:none:url:https://x/list"


def test_form_pagination_pages_get_distinct_keys():
    # Different synthetic/identity URLs (per page) → different node_keys.
    k1 = node_key_for(NodeType.pagination_url, normalized_url="https://x?__ycl_page=1", org_unit_id=3)
    k2 = node_key_for(NodeType.pagination_url, normalized_url="https://x?__ycl_page=2", org_unit_id=3)
    assert k1 != k2


def test_node_key_missing_inputs_raise():
    import pytest

    with pytest.raises(ValueError):
        node_key_for(NodeType.org_unit)  # neither id nor name
    with pytest.raises(ValueError):
        node_key_for(NodeType.detail_url)  # no normalized_url


def test_name_key_strips_and_casefolds():
    assert name_key("  John Smith  ") == "john smith"


def test_name_key_fullwidth_to_halfwidth():
    # full-width ＡＢ and full-width space → half-width
    assert name_key("ＡＢＣ") == "abc"


def test_name_key_drops_decorative_dots():
    assert name_key("买买提·阿不都") == name_key("买买提阿不都")


def test_looks_like_academician():
    assert looks_like_academician("中国科学院院士")
    assert looks_like_academician("教授 中国工程院院士")
    assert not looks_like_academician("教授")
    assert not looks_like_academician(None)


def test_save_result_defaults_zero():
    r = SaveResult()
    assert (r.inserted, r.updated, r.affiliations_added, r.academicians, r.save_errors) == (0, 0, 0, 0, 0)
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_dedup_keys.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.storage.dedup'`.

- [ ] **Step 3: Implement the pure functions in `dedup.py`**

Create `src/dext/storage/dedup.py`:
```python
"""Dedup / upsert: stable node_key generation, conservative name_key, and the
save_professors merge logic (source doc §9/§14, spec §6).

Known, accepted limitations (recorded, not fixed): same-college same-name
records may falsely merge; the same person written differently (English name,
former name, traditional/simplified, ethnic-name separator dots) may fail to
merge. name_key is a normalized DISPLAY name, not a strong identity key.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from dext.storage.models import NodeType

# Decorative separator dots removed before comparison (ethnic-name middots etc.).
_DECORATIVE_DOTS = dict.fromkeys(map(ord, "·•∙・･‧"), None)


def node_key_for(
    node_type: NodeType,
    *,
    normalized_url: str | None = None,
    org_unit_id: int | str | None = None,
    normalized_name: str | None = None,
) -> str:
    """Stable, globally-unique work-unit key (source doc §13.3).

    - org_unit:  ``org_unit:id:<id>`` (preferred) else ``org_unit:name:<normalized_name>``
    - URL nodes: ``<type>:org:<org_unit_id|none>:url:<normalized_url>``

    `normalized_url` is produced by SP3 (fragment-stripped, param-sorted; the
    synthetic/identity URL for form pagination, which makes each page distinct).
    """
    if node_type == NodeType.org_unit:
        if org_unit_id is not None:
            return f"org_unit:id:{org_unit_id}"
        if normalized_name:
            return f"org_unit:name:{normalized_name}"
        raise ValueError("org_unit node_key needs org_unit_id or normalized_name")
    if not normalized_url:
        raise ValueError(f"{node_type} node_key needs normalized_url")
    owner = org_unit_id if org_unit_id is not None else "none"
    return f"{node_type.value}:org:{owner}:url:{normalized_url}"


def name_key(name: str) -> str:
    """Conservative, explainable comparison key (spec §6): strip ends → NFKC
    (full-width → half-width) → drop decorative dots → casefold. No pinyin /
    English-name mapping, and internal whitespace is preserved by design."""
    s = name.strip()
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DECORATIVE_DOTS)
    return s.casefold()


def looks_like_academician(title: str | None) -> bool:
    """Route to the academicians table when the title marks an academician."""
    return bool(title) and "院士" in title


@dataclass
class SaveResult:
    inserted: int = 0
    updated: int = 0
    affiliations_added: int = 0
    academicians: int = 0
    save_errors: int = 0
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_dedup_keys.py -v
```
Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/dedup.py tests/test_storage_dedup_keys.py
git commit -m "feat(sp2): add node_key/name_key/academician dedup primitives + SaveResult" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `writer.py` — DBWriter core + graph commands

**Files:**
- Create: `src/dext/storage/writer.py` (DBWriter machinery, input specs, graph commands)
- Test: `tests/test_storage_writer.py`

This task covers the single-writer machinery and the graph state-machine writes:
`upsert_org_unit`, `upsert_node`, `add_edge`, `mark_node`, `claim_next`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_writer.py`:
```python
import asyncio

import pytest
from sqlalchemy import select

from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import EdgeType, GraphEdge, GraphNode, NodeStatus, NodeType
from dext.storage.writer import DBWriter, NodeSpec, OrgUnitSpec


async def _writer(tmp_path):
    eng = create_engine_for_path(tmp_path / "w.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    return eng, sf, w, task


async def _close(eng, w, task):
    await w.stop()
    await task
    await eng.dispose()


async def test_upsert_node_returns_id_and_is_idempotent(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    spec = NodeSpec(node_key="n1", type=NodeType.detail_url, url="https://x/1", priority_score=1.0)
    id1 = await w.upsert_node(spec)
    id2 = await w.upsert_node(NodeSpec(node_key="n1", type=NodeType.detail_url, url="https://x/1", priority_score=5.0))
    assert id1 == id2
    async with sf() as s:
        rows = (await s.execute(select(GraphNode).where(GraphNode.node_key == "n1"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].priority_score == 5.0  # re-discovery bumps priority to the max
    await _close(eng, w, task)


async def test_concurrent_submits_are_serialized_and_each_future_resolves(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    specs = [NodeSpec(node_key=f"k{i}", type=NodeType.org_listing_url, url=f"https://x/{i}") for i in range(25)]
    ids = await asyncio.gather(*(w.upsert_node(s) for s in specs))
    assert len(set(ids)) == 25  # all distinct, none lost
    async with sf() as s:
        assert (await s.execute(select(GraphNode))).scalars().all().__len__() == 25
    await _close(eng, w, task)


async def test_add_edge_is_idempotent(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    a = await w.upsert_node(NodeSpec(node_key="a", type=NodeType.org_unit, url="about:org_unit:a"))
    b = await w.upsert_node(NodeSpec(node_key="b", type=NodeType.faculty_list_url, url="https://x/b"))
    e1 = await w.add_edge(a, b, EdgeType.discovered_on_page)
    e2 = await w.add_edge(a, b, EdgeType.discovered_on_page)
    assert e1 == e2
    async with sf() as s:
        assert len((await s.execute(select(GraphEdge))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_mark_node_terminal_sets_completed_at(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    nid = await w.upsert_node(NodeSpec(node_key="m", type=NodeType.detail_url, url="https://x/m"))
    await w.mark_node(nid, NodeStatus.done, content_hash="abc123")
    async with sf() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.id == nid))).scalar_one()
        assert node.status == "done"
        assert node.content_hash == "abc123"
        assert node.completed_at is not None
    await _close(eng, w, task)


async def test_claim_next_atomic_and_priority_order(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    await w.upsert_node(NodeSpec(node_key="lo", type=NodeType.detail_url, url="https://x/lo", priority_score=1.0))
    await w.upsert_node(NodeSpec(node_key="hi", type=NodeType.detail_url, url="https://x/hi", priority_score=9.0))
    claimed = await w.claim_next(run_id=1)
    assert claimed is not None and claimed.node_key == "hi"  # highest priority first
    async with sf() as s:
        node = (await s.execute(select(GraphNode).where(GraphNode.node_key == "hi"))).scalar_one()
        assert node.status == "in_progress"
        assert node.attempt_count == 1
        assert node.claimed_at is not None
        assert node.run_id == 1
    await _close(eng, w, task)


async def test_claim_next_honors_exclude_set(tmp_path):
    # SP6 single-run no-reclaim: excluded node_keys are skipped.
    eng, sf, w, task = await _writer(tmp_path)
    await w.upsert_node(NodeSpec(node_key="hi", type=NodeType.detail_url, url="https://x/hi", priority_score=9.0))
    await w.upsert_node(NodeSpec(node_key="lo", type=NodeType.detail_url, url="https://x/lo", priority_score=1.0))
    claimed = await w.claim_next(run_id=1, exclude_node_keys={"hi"})
    assert claimed.node_key == "lo"
    await _close(eng, w, task)


async def test_claim_next_returns_none_when_nothing_claimable(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    nid = await w.upsert_node(NodeSpec(node_key="d", type=NodeType.detail_url, url="https://x/d"))
    await w.mark_node(nid, NodeStatus.done)
    assert await w.claim_next(run_id=1) is None
    await _close(eng, w, task)


async def test_claim_skips_nodes_at_max_attempts(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    await w.upsert_node(NodeSpec(node_key="x", type=NodeType.detail_url, url="https://x/x", max_attempts=1))
    first = await w.claim_next(run_id=1)        # attempt_count 0 -> 1
    assert first is not None
    # put it back to retry; now attempt_count(1) >= max_attempts(1) → not claimable
    await w.mark_node(first.id, NodeStatus.retry)
    assert await w.claim_next(run_id=1) is None
    await _close(eng, w, task)


async def test_command_exception_propagates_via_future_and_worker_survives(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    # add_edge to non-existent nodes violates the FK → exception on that Future only.
    with pytest.raises(Exception):
        await w.add_edge(999, 998, EdgeType.pagination_of)
    # worker still alive: a valid command afterwards succeeds.
    nid = await w.upsert_node(NodeSpec(node_key="ok", type=NodeType.detail_url, url="https://x/ok"))
    assert isinstance(nid, int)
    await _close(eng, w, task)
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_writer.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.storage.writer'`.

- [ ] **Step 3: Implement `writer.py` (core + graph commands)**

Create `src/dext/storage/writer.py`:
```python
"""DBWriter — the single writer. One coroutine drains an asyncio.Queue and runs
each command (a closure over the worker's own AsyncSession), commits serially,
and resolves the caller's Future. claim_next is an atomic SELECT+UPDATE in that
same session, so the single-claimer + single-writer invariant makes it race-free
(spec §5). Write exceptions are NOT swallowed: they roll back and propagate to
the caller via the Future.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from sqlalchemy import or_, select

from dext.storage.models import (
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
    OrgUnit,
    utcnow_iso,
)

_TERMINAL = {NodeStatus.done, NodeStatus.failed, NodeStatus.skipped}


# ── Input specs (write-API DTOs; produced by SP6) ────────────────────────────
@dataclass
class OrgUnitSpec:
    name: str
    url: str
    kind: str = "college"
    status: str = "pending"
    discovered_from_url: str | None = None


@dataclass
class NodeSpec:
    node_key: str
    type: NodeType
    url: str
    org_unit_id: int | None = None
    org_unit_name: str | None = None
    status: NodeStatus = NodeStatus.pending
    priority_score: float = 0.0
    base_priority: float = 0.0
    confidence: float | None = None
    depth: int = 0
    max_attempts: int = 3
    run_id: int | None = None
    metadata: dict | None = None


@dataclass
class ClaimedNode:
    """Detached snapshot returned by claim_next (safe to use after commit)."""

    id: int
    node_key: str
    type: str
    url: str
    org_unit_id: int | None
    org_unit_name: str | None
    depth: int
    attempt_count: int
    priority_score: float
    content_hash: str | None
    metadata: dict | None


class DBWriter:
    def __init__(self, session_factory):
        self._session_factory = session_factory
        self._queue: asyncio.Queue = asyncio.Queue()

    # ── machinery ────────────────────────────────────────────────────────────
    async def run(self) -> None:
        async with self._session_factory() as session:
            while True:
                item = await self._queue.get()
                if item is None:  # stop sentinel
                    break
                op, fut = item
                try:
                    result = await op(session)
                    await session.commit()
                    if not fut.cancelled():
                        fut.set_result(result)
                except Exception as exc:  # noqa: BLE001 — propagate, don't swallow
                    await session.rollback()
                    if not fut.cancelled():
                        fut.set_exception(exc)

    async def _run(self, op: Callable[[object], Awaitable]):
        fut = asyncio.get_running_loop().create_future()
        await self._queue.put((op, fut))
        return await fut

    async def stop(self) -> None:
        await self._queue.put(None)

    # ── graph commands ─────────────────────────────────────────────────────��─
    async def upsert_org_unit(self, spec: OrgUnitSpec) -> int:
        return await self._run(lambda s: _upsert_org_unit(s, spec))

    async def upsert_node(self, spec: NodeSpec) -> int:
        return await self._run(lambda s: _upsert_node(s, spec))

    async def add_edge(self, from_id, to_id, edge_type: EdgeType, *, confidence=None, metadata=None) -> int:
        return await self._run(lambda s: _add_edge(s, from_id, to_id, edge_type, confidence, metadata))

    async def mark_node(self, node_id, status: NodeStatus, *, last_error=None,
                        content_hash=None, next_retry_at=None, attempt_inc=False) -> None:
        return await self._run(
            lambda s: _mark_node(s, node_id, status, last_error, content_hash, next_retry_at, attempt_inc)
        )

    async def claim_next(self, *, run_id, exclude_node_keys=None, types=None, now=None) -> ClaimedNode | None:
        return await self._run(lambda s: _claim_next(s, run_id, exclude_node_keys, types, now))


# ── command implementations (module-level; take the worker's session) ─────────
async def _upsert_org_unit(session, spec: OrgUnitSpec) -> int:
    existing = (await session.execute(select(OrgUnit).where(OrgUnit.name == spec.name))).scalar_one_or_none()
    if existing is not None:
        if not existing.url:
            existing.url = spec.url
        if not existing.kind:
            existing.kind = spec.kind
        if not existing.discovered_from_url:
            existing.discovered_from_url = spec.discovered_from_url
        await session.flush()
        return existing.id
    ou = OrgUnit(
        name=spec.name, url=spec.url, kind=spec.kind,
        status=spec.status, discovered_from_url=spec.discovered_from_url,
    )
    session.add(ou)
    await session.flush()
    return ou.id


async def _upsert_node(session, spec: NodeSpec) -> int:
    existing = (await session.execute(select(GraphNode).where(GraphNode.node_key == spec.node_key))).scalar_one_or_none()
    if existing is not None:
        # Re-discovery: never reset status/attempts. Fill missing ownership, take
        # the higher priority, shallow-merge metadata.
        existing.priority_score = max(existing.priority_score, spec.priority_score)
        existing.base_priority = max(existing.base_priority, spec.base_priority)
        if existing.org_unit_id is None and spec.org_unit_id is not None:
            existing.org_unit_id = spec.org_unit_id
        if not existing.org_unit_name and spec.org_unit_name:
            existing.org_unit_name = spec.org_unit_name
        if existing.confidence is None and spec.confidence is not None:
            existing.confidence = spec.confidence
        if spec.metadata:
            existing.metadata_json = {**(existing.metadata_json or {}), **spec.metadata}
        await session.flush()
        return existing.id
    node = GraphNode(
        node_key=spec.node_key, type=spec.type, url=spec.url,
        org_unit_id=spec.org_unit_id, org_unit_name=spec.org_unit_name,
        status=spec.status, priority_score=spec.priority_score, base_priority=spec.base_priority,
        confidence=spec.confidence, depth=spec.depth, max_attempts=spec.max_attempts,
        run_id=spec.run_id, metadata_json=spec.metadata,
    )
    session.add(node)
    await session.flush()
    return node.id


async def _add_edge(session, from_id, to_id, edge_type: EdgeType, confidence, metadata) -> int:
    existing = (
        await session.execute(
            select(GraphEdge).where(
                GraphEdge.from_node_id == from_id,
                GraphEdge.to_node_id == to_id,
                GraphEdge.edge_type == edge_type,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    edge = GraphEdge(
        from_node_id=from_id, to_node_id=to_id, edge_type=edge_type,
        confidence=confidence, metadata_json=metadata,
    )
    session.add(edge)
    await session.flush()
    return edge.id


async def _mark_node(session, node_id, status: NodeStatus, last_error, content_hash, next_retry_at, attempt_inc) -> None:
    node = (await session.execute(select(GraphNode).where(GraphNode.id == node_id))).scalar_one()
    node.status = status
    if last_error is not None:
        node.last_error = last_error
    if content_hash is not None:
        node.content_hash = content_hash
    if next_retry_at is not None:
        node.next_retry_at = next_retry_at
    if attempt_inc:
        node.attempt_count += 1
    if status in _TERMINAL:
        node.completed_at = utcnow_iso()
    await session.flush()


async def _claim_next(session, run_id, exclude_node_keys, types, now) -> ClaimedNode | None:
    now = now or utcnow_iso()
    stmt = (
        select(GraphNode)
        .where(
            GraphNode.status.in_([NodeStatus.pending, NodeStatus.retry]),
            GraphNode.attempt_count < GraphNode.max_attempts,
            or_(GraphNode.next_retry_at.is_(None), GraphNode.next_retry_at <= now),
        )
        .order_by(GraphNode.priority_score.desc(), GraphNode.id.asc())
    )
    if types:
        stmt = stmt.where(GraphNode.type.in_([t.value for t in types]))
    if exclude_node_keys:
        stmt = stmt.where(GraphNode.node_key.notin_(list(exclude_node_keys)))
    node = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if node is None:
        return None
    node.status = NodeStatus.in_progress
    node.claimed_at = now
    node.run_id = run_id
    node.attempt_count += 1  # a claim IS an attempt (gates max_attempts)
    await session.flush()
    return ClaimedNode(
        id=node.id, node_key=node.node_key, type=node.type, url=node.url,
        org_unit_id=node.org_unit_id, org_unit_name=node.org_unit_name,
        depth=node.depth, attempt_count=node.attempt_count,
        priority_score=node.priority_score, content_hash=node.content_hash,
        metadata=node.metadata_json,
    )
```

> Note: the box-drawing chars in the section comments are decorative only; if your editor mangles them, plain `# ---` is equivalent. Ensure the file is saved UTF-8.

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_writer.py -v
```
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/writer.py tests/test_storage_writer.py
git commit -m "feat(sp2): add DBWriter single-writer + graph commands (upsert/edge/mark/claim)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `writer.py` — page cache, extraction attempts/failures, run/meta commands

**Files:**
- Modify: `src/dext/storage/writer.py` (append `PageCachePayload` + commands)
- Modify: `tests/test_storage_writer.py` (append tests)

- [ ] **Step 1: Append the failing tests**

Append to `tests/test_storage_writer.py`:
```python
from dext.storage.models import (
    CrawlRun,
    ExtractionAttempt,
    ExtractionFailure,
    PageCache,
    UniversityMeta,
)
from dext.storage.writer import PageCachePayload


async def test_save_page_cache_upserts_by_identity_url(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    url = "https://x?__ycl_page=2"  # identity URL (synthetic for form pagination)
    await w.save_page_cache(PageCachePayload(url=url, title="第2页", status_code=200, links=["a", "b"]))
    await w.save_page_cache(PageCachePayload(url=url, title="第2页改", status_code=200, content_hash="h2"))
    async with sf() as s:
        rows = (await s.execute(select(PageCache).where(PageCache.url == url))).scalars().all()
        assert len(rows) == 1  # upsert, not duplicate
        assert rows[0].title == "第2页改"
        assert rows[0].content_hash == "h2"
        assert rows[0].snapshot_encoding == "utf-8"
    await _close(eng, w, task)


async def test_extraction_attempt_record_then_finish(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    nid = await w.upsert_node(NodeSpec(node_key="d", type=NodeType.detail_url, url="https://x/d"))
    aid = await w.record_extraction_attempt(graph_node_id=nid, attempt=1, input_cache_url="https://x/d")
    await w.finish_extraction_attempt(aid, status="succeeded", raw_output_preview="{...}")
    async with sf() as s:
        att = (await s.execute(select(ExtractionAttempt).where(ExtractionAttempt.id == aid))).scalar_one()
        assert att.status == "succeeded"
        assert att.finished_at is not None
    await _close(eng, w, task)


async def test_record_extraction_failure(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    await w.record_extraction_failure(failure_type="invalid_json", resolver="dropped",
                                      professor_name_hint="张三", source_url="https://x/d")
    async with sf() as s:
        rows = (await s.execute(select(ExtractionFailure))).scalars().all()
        assert len(rows) == 1 and rows[0].failure_type == "invalid_json"
    await _close(eng, w, task)


async def test_run_lifecycle_and_university_status(tmp_path):
    eng, sf, w, task = await _writer(tmp_path)
    async with sf() as s:  # a meta row must exist for last_run_id wiring
        s.add(UniversityMeta(name="测试大学", abbr="test"))
        await s.commit()
    rid = await w.start_run(mode="fresh", settings={"max_depth": 4})
    await w.update_university_status("in_progress")
    await w.finish_run(rid, status="completed", summary={"professors": 10})
    async with sf() as s:
        run = (await s.execute(select(CrawlRun).where(CrawlRun.id == rid))).scalar_one()
        meta = (await s.execute(select(UniversityMeta))).scalar_one()
        assert run.status == "completed" and run.finished_at is not None
        assert run.summary_json == {"professors": 10}
        assert meta.crawl_status == "in_progress"
        assert meta.last_run_id == rid
    await _close(eng, w, task)
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_writer.py -v -k "page_cache or extraction or run_lifecycle"
```
Expected: FAIL — `ImportError: cannot import name 'PageCachePayload'`.

- [ ] **Step 3: Append the payload + commands to `writer.py`**

Add `PageCachePayload` to the input-specs section of `src/dext/storage/writer.py` (after `NodeSpec`):
```python
@dataclass
class PageCachePayload:
    url: str  # identity URL (synthetic URL for form pagination) — the cache key
    final_url: str | None = None
    status_code: int | None = None
    text_snapshot: str | None = None
    links: list | None = None
    link_signals: list | None = None
    block_reason: str | None = None
    html_snapshot: str | None = None
    content_hash: str | None = None
    title: str | None = None
    fetch_action: dict | None = None
    snapshot_encoding: str = "utf-8"
```

Extend the imports at the top of `writer.py`:
```python
from dext.storage.models import (
    CrawlRun,
    EdgeType,
    ExtractionAttempt,
    ExtractionFailure,
    GraphEdge,
    GraphNode,
    NodeStatus,
    NodeType,
    OrgUnit,
    PageCache,
    UniversityMeta,
    utcnow_iso,
)
```

Add these methods to the `DBWriter` class (after `claim_next`):
```python
    async def save_page_cache(self, payload: "PageCachePayload") -> str:
        return await self._run(lambda s: _save_page_cache(s, payload))

    async def record_extraction_attempt(self, *, graph_node_id, attempt=1, status="running",
                                        prompt_hash=None, input_cache_url=None) -> int:
        return await self._run(
            lambda s: _record_attempt(s, graph_node_id, attempt, status, prompt_hash, input_cache_url)
        )

    async def finish_extraction_attempt(self, attempt_id, *, status, raw_output_preview=None, failure_type=None) -> None:
        return await self._run(
            lambda s: _finish_attempt(s, attempt_id, status, raw_output_preview, failure_type)
        )

    async def record_extraction_failure(self, *, failure_type, resolver=None,
                                        raw_arguments_preview=None, professor_name_hint=None, source_url=None) -> int:
        return await self._run(
            lambda s: _record_failure(s, failure_type, resolver, raw_arguments_preview, professor_name_hint, source_url)
        )

    async def start_run(self, *, mode, settings=None) -> int:
        return await self._run(lambda s: _start_run(s, mode, settings))

    async def finish_run(self, run_id, *, status, summary=None) -> None:
        return await self._run(lambda s: _finish_run(s, run_id, status, summary))

    async def update_university_status(self, status) -> None:
        return await self._run(lambda s: _update_university_status(s, status))
```

Add these command implementations at the end of `writer.py`:
```python
async def _save_page_cache(session, payload: PageCachePayload) -> str:
    existing = (await session.execute(select(PageCache).where(PageCache.url == payload.url))).scalar_one_or_none()
    target = existing or PageCache(url=payload.url)
    target.final_url = payload.final_url
    target.status_code = payload.status_code
    target.text_snapshot = payload.text_snapshot
    target.links_json = payload.links
    target.link_signals_json = payload.link_signals
    target.block_reason = payload.block_reason
    target.html_snapshot = payload.html_snapshot
    target.content_hash = payload.content_hash
    target.title = payload.title
    target.fetch_action_json = payload.fetch_action
    target.snapshot_encoding = payload.snapshot_encoding
    if existing is None:
        session.add(target)
    await session.flush()
    return target.url


async def _record_attempt(session, graph_node_id, attempt, status, prompt_hash, input_cache_url) -> int:
    row = ExtractionAttempt(
        graph_node_id=graph_node_id, attempt=attempt, status=status,
        prompt_hash=prompt_hash, input_cache_url=input_cache_url,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _finish_attempt(session, attempt_id, status, raw_output_preview, failure_type) -> None:
    row = (await session.execute(select(ExtractionAttempt).where(ExtractionAttempt.id == attempt_id))).scalar_one()
    row.status = status
    if raw_output_preview is not None:
        row.raw_output_preview = raw_output_preview
    if failure_type is not None:
        row.failure_type = failure_type
    row.finished_at = utcnow_iso()
    await session.flush()


async def _record_failure(session, failure_type, resolver, raw_arguments_preview, professor_name_hint, source_url) -> int:
    row = ExtractionFailure(
        failure_type=failure_type, resolver=resolver,
        raw_arguments_preview=raw_arguments_preview,
        professor_name_hint=professor_name_hint, source_url=source_url,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _start_run(session, mode, settings) -> int:
    run = CrawlRun(mode=mode, started_at=utcnow_iso(), status="running", settings_json=settings)
    session.add(run)
    await session.flush()
    meta = (await session.execute(select(UniversityMeta))).scalars().first()
    if meta is not None:
        meta.last_run_id = run.id
    await session.flush()
    return run.id


async def _finish_run(session, run_id, status, summary) -> None:
    run = (await session.execute(select(CrawlRun).where(CrawlRun.id == run_id))).scalar_one()
    run.status = status
    run.finished_at = utcnow_iso()
    if summary is not None:
        run.summary_json = summary
    await session.flush()


async def _update_university_status(session, status) -> None:
    meta = (await session.execute(select(UniversityMeta))).scalars().first()
    if meta is not None:
        meta.crawl_status = status
    await session.flush()
```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_writer.py -v
```
Expected: `13 passed` (9 from Task 6 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/writer.py tests/test_storage_writer.py
git commit -m "feat(sp2): add page-cache/extraction/run write commands to DBWriter" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: `dedup.save_professors` + `DBWriter.save_professors`

**Files:**
- Modify: `src/dext/storage/dedup.py` (append `save_professors` + helpers)
- Modify: `src/dext/storage/writer.py` (append `save_professors` command)
- Test: `tests/test_storage_dedup_save.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_dedup_save.py`:
```python
from sqlalchemy import select

from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import Academician, Professor, ProfessorAffiliation
from dext.storage.writer import DBWriter, OrgUnitSpec
from dext.types import ProfessorPayload


async def _setup(tmp_path):
    import asyncio

    eng = create_engine_for_path(tmp_path / "p.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    math = await w.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x/math"))
    phys = await w.upsert_org_unit(OrgUnitSpec(name="物理学院", url="https://x/phys"))
    return eng, sf, w, task, math, phys


async def _close(eng, w, task):
    await w.stop()
    await task
    await eng.dispose()


async def test_same_college_same_name_key_merges_and_fills(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    r1 = await w.save_professors([ProfessorPayload(name="张三", title="教授")], org_unit_id=math, org_unit_name="数学学院")
    r2 = await w.save_professors([ProfessorPayload(name="张三", email="z@x.edu")], org_unit_id=math, org_unit_name="数学学院")
    assert r1.inserted == 1
    assert r2.inserted == 0 and r2.updated == 1
    async with sf() as s:
        profs = (await s.execute(select(Professor))).scalars().all()
        assert len(profs) == 1
        assert profs[0].title == "教授" and profs[0].email == "z@x.edu"  # fields merged
    await _close(eng, w, task)


async def test_cross_college_email_match_adds_affiliation(tmp_path):
    eng, sf, w, task, math, phys = await _setup(tmp_path)
    await w.save_professors([ProfessorPayload(name="李四", email="l@x.edu")], org_unit_id=math, org_unit_name="数学学院")
    r = await w.save_professors([ProfessorPayload(name="李四", email="l@x.edu")], org_unit_id=phys, org_unit_name="物理学院")
    assert r.inserted == 0 and r.affiliations_added == 1
    async with sf() as s:
        assert len((await s.execute(select(Professor))).scalars().all()) == 1
        assert len((await s.execute(select(ProfessorAffiliation))).scalars().all()) == 2
    await _close(eng, w, task)


async def test_cross_college_homepage_match(tmp_path):
    eng, sf, w, task, math, phys = await _setup(tmp_path)
    await w.save_professors([ProfessorPayload(name="王五", homepage="https://x/wang")], org_unit_id=math, org_unit_name="数学学院")
    r = await w.save_professors([ProfessorPayload(name="王五", homepage="https://x/wang")], org_unit_id=phys, org_unit_name="物理学院")
    assert r.affiliations_added == 1
    async with sf() as s:
        assert len((await s.execute(select(Professor))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_distinct_people_create_separate_rows(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    r = await w.save_professors(
        [ProfessorPayload(name="赵六", email="a@x.edu"), ProfessorPayload(name="钱七", email="b@x.edu")],
        org_unit_id=math, org_unit_name="数学学院",
    )
    assert r.inserted == 2
    async with sf() as s:
        assert len((await s.execute(select(Professor))).scalars().all()) == 2
    await _close(eng, w, task)


async def test_academician_routed_to_academicians_table(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    r = await w.save_professors(
        [ProfessorPayload(name="孙院士", title="中国科学院院士")], org_unit_id=math, org_unit_name="数学学院"
    )
    assert r.academicians == 1 and r.inserted == 0
    async with sf() as s:
        assert len((await s.execute(select(Academician))).scalars().all()) == 1
        assert len((await s.execute(select(Professor))).scalars().all()) == 0
    await _close(eng, w, task)


async def test_academician_idempotent_on_repeat(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    await w.save_professors([ProfessorPayload(name="周院士", title="院士")], org_unit_id=math, org_unit_name="数学学院")
    r = await w.save_professors([ProfessorPayload(name="周院士", title="院士")], org_unit_id=math, org_unit_name="数学学院")
    assert r.academicians == 0  # (org_unit_id, name_key) already present
    async with sf() as s:
        assert len((await s.execute(select(Academician))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_repeated_save_is_idempotent_no_duplicate_affiliations(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    p = ProfessorPayload(name="吴九", email="w@x.edu")
    await w.save_professors([p], org_unit_id=math, org_unit_name="数学学院")
    await w.save_professors([p], org_unit_id=math, org_unit_name="数学学院")
    async with sf() as s:
        assert len((await s.execute(select(ProfessorAffiliation))).scalars().all()) == 1
    await _close(eng, w, task)
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_dedup_save.py -v
```
Expected: FAIL — `AttributeError: 'DBWriter' object has no attribute 'save_professors'`.

- [ ] **Step 3: Append `save_professors` to `dedup.py`**

Append to `src/dext/storage/dedup.py`:
```python
from sqlalchemy import select  # noqa: E402

from dext.storage.models import (  # noqa: E402
    Academician,
    Professor,
    ProfessorAffiliation,
)
from dext.types import ProfessorPayload  # noqa: E402

# Fields back-filled on merge (only when the existing value is empty).
_FILLABLE = (
    "title", "research_areas", "email", "phone",
    "homepage", "external_link", "bio", "enrollment_pref", "publications",
)


def _merge_fill(prof: Professor, p: ProfessorPayload) -> bool:
    changed = False
    for f in _FILLABLE:
        cur = getattr(prof, f)
        new = getattr(p, f, None)
        if (cur is None or cur == "") and new:
            setattr(prof, f, new)
            changed = True
    return changed


async def _ensure_affiliation(session, professor_id: int, org_unit_id: int, result: SaveResult) -> None:
    exists = (
        await session.execute(
            select(ProfessorAffiliation).where(
                ProfessorAffiliation.professor_id == professor_id,
                ProfessorAffiliation.org_unit_id == org_unit_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(ProfessorAffiliation(professor_id=professor_id, org_unit_id=org_unit_id))
        await session.flush()
        result.affiliations_added += 1


async def _save_academician(session, p, nk, org_unit_id, result: SaveResult) -> None:
    existing = (
        await session.execute(
            select(Academician).where(Academician.org_unit_id == org_unit_id, Academician.name_key == nk)
        )
    ).scalar_one_or_none()
    if existing is None and p.homepage:
        existing = (
            await session.execute(
                select(Academician).where(Academician.org_unit_id == org_unit_id, Academician.homepage == p.homepage)
            )
        ).scalar_one_or_none()
    if existing is None and p.external_link:
        existing = (
            await session.execute(
                select(Academician).where(
                    Academician.org_unit_id == org_unit_id, Academician.external_link == p.external_link
                )
            )
        ).scalar_one_or_none()
    if existing is not None:
        if not existing.title and p.title:
            existing.title = p.title
        if not existing.homepage and p.homepage:
            existing.homepage = p.homepage
        if not existing.external_link and p.external_link:
            existing.external_link = p.external_link
        await session.flush()
        result.updated += 1
        return
    session.add(Academician(
        name=p.name, name_key=nk, org_unit_id=org_unit_id,
        title=p.title, homepage=p.homepage, external_link=p.external_link,
    ))
    await session.flush()
    result.academicians += 1


async def save_professors(session, payloads, *, org_unit_id: int, org_unit_name: str) -> SaveResult:
    """Upsert a batch of extracted professors with dedup (source doc §14.1):
    same-college same name_key → merge; else cross-college email→homepage→
    external_link match → add affiliation; else insert. Academicians (title 含
    '院士') route to the academicians table. Each payload runs in a SAVEPOINT so
    one bad record is recorded + counted (save_errors) without aborting the batch.
    """
    result = SaveResult()
    for p in payloads:
        try:
            async with session.begin_nested():
                nk = name_key(p.name)
                if looks_like_academician(p.title):
                    await _save_academician(session, p, nk, org_unit_id, result)
                    continue

                # 1. same college, same name_key (professors carry no name_key column).
                affiliated = (
                    await session.execute(
                        select(Professor)
                        .join(ProfessorAffiliation, ProfessorAffiliation.professor_id == Professor.id)
                        .where(ProfessorAffiliation.org_unit_id == org_unit_id)
                    )
                ).scalars().all()
                match = next((c for c in affiliated if name_key(c.name) == nk), None)

                # 2. cross-college exact match: email → homepage → external_link.
                if match is None and p.email:
                    match = (await session.execute(select(Professor).where(Professor.email == p.email))).scalars().first()
                if match is None and p.homepage:
                    match = (await session.execute(select(Professor).where(Professor.homepage == p.homepage))).scalars().first()
                if match is None and p.external_link:
                    match = (await session.execute(select(Professor).where(Professor.external_link == p.external_link))).scalars().first()

                if match is not None:
                    await _ensure_affiliation(session, match.id, org_unit_id, result)
                    if _merge_fill(match, p):
                        await session.flush()
                        result.updated += 1
                    continue

                # 3. new professor + first affiliation.
                prof = Professor(
                    name=p.name, org_unit_name=org_unit_name, title=p.title,
                    research_areas=p.research_areas, email=p.email, phone=p.phone,
                    homepage=p.homepage, external_link=p.external_link, bio=p.bio,
                    enrollment_pref=p.enrollment_pref, publications=p.publications,
                )
                session.add(prof)
                await session.flush()
                await _ensure_affiliation(session, prof.id, org_unit_id, result)
                result.inserted += 1
        except Exception:  # noqa: BLE001 — record + count, never abort the batch
            result.save_errors += 1
            session.add(Academician.__mro__ and None)  # placeholder removed below
    return result
```

> Remove the stray placeholder line — the `except` block should record a failure row instead. Replace the `except` body with:
> ```python
>         except Exception:  # noqa: BLE001
>             result.save_errors += 1
>             session.add(ExtractionFailure(
>                 failure_type="save_error", resolver="dropped",
>                 raw_arguments_preview=repr(p)[:500],
>                 professor_name_hint=getattr(p, "name", None),
>                 source_url=getattr(p, "homepage", None),
>             ))
>             await session.flush()
> ```
> and add `ExtractionFailure` to the model imports at the top of `dedup.py`:
> ```python
> from dext.storage.models import Academician, ExtractionFailure, Professor, ProfessorAffiliation
> ```

- [ ] **Step 4: Add the `save_professors` command to `writer.py`**

Add to the `DBWriter` class (after `save_page_cache`):
```python
    async def save_professors(self, payloads, *, org_unit_id, org_unit_name):
        from dext.storage.dedup import save_professors as _save_professors

        return await self._run(
            lambda s: _save_professors(s, payloads, org_unit_id=org_unit_id, org_unit_name=org_unit_name)
        )
```

- [ ] **Step 5: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_dedup_save.py -v
```
Expected: `7 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/dext/storage/dedup.py src/dext/storage/writer.py tests/test_storage_dedup_save.py
git commit -m "feat(sp2): add save_professors dedup (merge/affiliation/academician split)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: `lifecycle.py` — resolve_db_path, open_fresh, open_resume

**Files:**
- Create: `src/dext/storage/lifecycle.py`
- Test: `tests/test_storage_lifecycle.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage_lifecycle.py`:
```python
import pytest
from sqlalchemy import select

from dext.config import Settings
from dext.seed import UniversitySeed
from dext.storage.db import StorageError
from dext.storage.lifecycle import open_fresh, open_resume, resolve_db_path
from dext.storage.models import GraphNode, NodeStatus, NodeType, UniversityMeta
from dext.storage.writer import NodeSpec


def _university():
    return UniversitySeed(
        name="北京航空航天大学", url="https://www.buaa.edu.cn/",
        location="北京", org_unit_listing_urls=["https://www.buaa.edu.cn/list"],
    )


def _settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "universities")


def test_resolve_db_path(tmp_path):
    s = _settings(tmp_path)
    assert resolve_db_path("buaa", s) == (tmp_path / "universities" / "buaa.db")


async def test_open_fresh_creates_db_and_meta_row(tmp_path):
    s = _settings(tmp_path)
    handle = await open_fresh(_university(), "buaa", s)
    try:
        assert resolve_db_path("buaa", s).exists()
        async with handle.session() as sess:
            meta = (await sess.execute(select(UniversityMeta))).scalar_one()
            assert meta.name == "北京航空航天大学"
            assert meta.abbr == "buaa"
            assert meta.start_url == "https://www.buaa.edu.cn/"
            assert meta.schema_version == 1
    finally:
        await handle.close()


async def test_open_fresh_backs_up_existing_db_then_rebuilds(tmp_path):
    s = _settings(tmp_path)
    h1 = await open_fresh(_university(), "buaa", s)
    await h1.writer.upsert_node(NodeSpec(node_key="old", type=NodeType.detail_url, url="https://x/old"))
    await h1.close()

    h2 = await open_fresh(_university(), "buaa", s)
    try:
        backups = list((s.data_dir / "backup").glob("*-buaa/buaa.db"))
        assert len(backups) == 1  # old DB copied to backup/<ts>-buaa/
        async with h2.session() as sess:
            assert (await sess.execute(select(GraphNode))).scalars().all() == []  # rebuilt empty
    finally:
        await h2.close()


async def test_open_resume_missing_db_raises(tmp_path):
    s = _settings(tmp_path)
    with pytest.raises(StorageError):
        await open_resume(_university(), "buaa", s)


async def test_open_resume_resets_in_progress_to_retry_keeps_done(tmp_path):
    s = _settings(tmp_path)
    h1 = await open_fresh(_university(), "buaa", s)
    inprog = await h1.writer.upsert_node(NodeSpec(node_key="ip", type=NodeType.detail_url, url="https://x/ip"))
    finished = await h1.writer.upsert_node(NodeSpec(node_key="dn", type=NodeType.detail_url, url="https://x/dn"))
    await h1.writer.mark_node(inprog, NodeStatus.in_progress)
    await h1.writer.mark_node(finished, NodeStatus.done)
    await h1.close()

    h2 = await open_resume(_university(), "buaa", s)
    try:
        async with h2.session() as sess:
            ip = (await sess.execute(select(GraphNode).where(GraphNode.node_key == "ip"))).scalar_one()
            dn = (await sess.execute(select(GraphNode).where(GraphNode.node_key == "dn"))).scalar_one()
            assert ip.status == "retry"   # crash-residue reset
            assert dn.status == "done"    # terminal preserved
    finally:
        await h2.close()
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
uv run pytest tests/test_storage_lifecycle.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'dext.storage.lifecycle'`.

- [ ] **Step 3: Implement `lifecycle.py`**

Create `src/dext/storage/lifecycle.py`:
```python
"""Per-university DB lifecycle: path resolution, fresh (backup + rebuild) and
resume (keep + reconcile). Backup is copy-then-delete (safety first): if the
copy fails, fresh aborts and the original DB is left intact (spec §4).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import update

from dext.seed import db_filename
from dext.storage.db import (
    StorageError,
    StorageHandle,
    create_all,
    create_engine_for_path,
    ensure_columns,
    make_session_factory,
)
from dext.storage.models import (
    ExtractionAttempt,
    GraphNode,
    NodeStatus,
    OrgUnit,
    SCHEMA_VERSION,
    UniversityMeta,
)
from dext.storage.writer import DBWriter


def resolve_db_path(abbr: str, settings) -> Path:
    """data_dir/<abbr>.db (path assembly; filename from SP1's db_filename)."""
    return Path(settings.data_dir) / db_filename(abbr)


def _backup_existing(db_path: Path, settings) -> Path:
    """Copy db_path (+ -wal/-shm) into backup/<ts>-<abbr>/, then delete originals.
    Raises (leaving the original intact) if the copy fails."""
    abbr = db_path.stem
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(settings.data_dir) / "backup" / f"{ts}-{abbr}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(db_path, backup_dir / db_path.name)
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(db_path.name + suffix)
            if sidecar.exists():
                shutil.copy2(sidecar, backup_dir / sidecar.name)
    except OSError as exc:  # copy failed → abort, keep original
        raise StorageError(f"backup of {db_path} failed, aborting fresh: {exc}") from exc
    # copy succeeded → remove originals
    for suffix in ("", "-wal", "-shm"):
        target = db_path.with_name(db_path.name + suffix) if suffix else db_path
        target.unlink(missing_ok=True)
    return backup_dir


async def _assemble_handle(engine) -> StorageHandle:
    session_factory = make_session_factory(engine)
    writer = DBWriter(session_factory)
    handle = StorageHandle(engine, writer, session_factory)
    handle.start_writer()
    return handle


async def open_fresh(university, abbr: str, settings) -> StorageHandle:
    db_path = resolve_db_path(abbr, settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        _backup_existing(db_path, settings)  # raises on failure → original kept
    engine = create_engine_for_path(db_path)
    await create_all(engine)
    handle = await _assemble_handle(engine)
    async with handle.session() as session:
        session.add(UniversityMeta(
            name=university.name,
            start_url=university.url,
            location=getattr(university, "location", None),
            abbr=abbr,
            schema_version=SCHEMA_VERSION,
            crawl_status="pending",
        ))
        await session.commit()
        handle.university_id = (await session.execute(
            __import__("sqlalchemy").select(UniversityMeta.id)
        )).scalar_one()
    return handle


async def open_resume(university, abbr: str, settings) -> StorageHandle:
    db_path = resolve_db_path(abbr, settings)
    if not db_path.exists():
        raise StorageError(f"no DB at {db_path}; run a fresh crawl first")
    engine = create_engine_for_path(db_path)
    await create_all(engine)      # idempotent (new tables only)
    await ensure_columns(engine)  # heal added columns (spec §7)
    # Reconcile crash residue BEFORE starting the writer (startup is single-threaded).
    async with engine.begin() as conn:
        await conn.execute(
            update(GraphNode).where(GraphNode.status == NodeStatus.in_progress).values(status=NodeStatus.retry)
        )
        await conn.execute(
            update(OrgUnit).where(OrgUnit.status == "in_progress").values(status="pending")
        )
        await conn.execute(
            update(ExtractionAttempt).where(ExtractionAttempt.status == "running").values(status="retry")
        )
    return await _assemble_handle(engine)
```

> Tidy-up: the `__import__("sqlalchemy").select(...)` in `open_fresh` is intentionally avoided in real code — add `from sqlalchemy import select` to the imports at the top of `lifecycle.py` and replace that line with:
> ```python
>         handle.university_id = (await session.execute(select(UniversityMeta.id))).scalar_one()
> ```

- [ ] **Step 4: Run to verify pass**

Run:
```bash
uv run pytest tests/test_storage_lifecycle.py -v
```
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/dext/storage/lifecycle.py tests/test_storage_lifecycle.py
git commit -m "feat(sp2): add DB lifecycle (resolve_db_path, open_fresh backup+rebuild, open_resume reconcile)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Full-suite verification & Definition of Done

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run:
```bash
uv run pytest -v
```
Expected: all green — 22 SP1 tests + SP2 (`6 types + 9 models + 5 db + 11 dedup-keys + 13 writer + 7 dedup-save + 6 lifecycle = 57`) → **79 passed**.

- [ ] **Step 2: Confirm no LLM/network/HTML imports leaked into storage**

Run:
```bash
grep -rEn "openai|aiohttp|requests|bs4|BeautifulSoup|html2text" src/dext/storage/ || echo "clean: no network/LLM/HTML imports in storage"
```
Expected: `clean: ...`. SP2 is a pure data layer.

- [ ] **Step 3: Confirm the public interface matches the spec (§8)**

Run:
```bash
uv run python -c "from dext.storage.lifecycle import resolve_db_path, open_fresh, open_resume; from dext.storage.writer import DBWriter; from dext.storage.dedup import node_key_for, name_key, save_professors, SaveResult; from dext.storage.db import StorageHandle; from dext.types import NodeType, EdgeType, NodeStatus, ProfessorPayload; print('public interface OK')"
```
Expected: `public interface OK`.

## Definition of Done

- `uv run pytest -v` → all green (target **79 passed**).
- Public interface exactly as spec §8 lists: `dext.storage.models` (ORM + enums); `dext.storage.lifecycle.{resolve_db_path, open_fresh, open_resume}`; `StorageHandle` with `.engine/.writer/.session()/.close()`; `dext.storage.writer.DBWriter` (`submit`-style command methods + `run()`); `dext.storage.dedup.{node_key_for, name_key, save_professors, SaveResult}`; enums re-exported from `dext.types`.
- PRAGMAs (WAL, busy_timeout=15000, NORMAL, foreign_keys=ON) applied; JSON columns keep Chinese readable (ensure_ascii=False).
- fresh backs up an existing DB (copy-then-delete) and rebuilds; backup failure aborts fresh and keeps the original. resume resets `in_progress`→`retry` and preserves `done`.
- DBWriter serializes concurrent submits, returns per-command Futures, and survives a command exception (propagated, not swallowed); `claim_next` is atomic and honors `exclude_node_keys` / `max_attempts`.
- dedup: same-college same-`name_key` merges; cross-college email/homepage/external_link adds an affiliation; academicians split out; repeated saves are idempotent.
- No DB enum / Alembic / connection-pool tuning / fuzzy dedup (spec §11 "不做").
- All work committed on branch `sp2-storage`.

## Self-Review (performed while writing this plan)

**1. Spec coverage** — every SP2 spec section maps to a task:
- §2 engine/PRAGMA/sessions (WAL, busy_timeout, NORMAL, foreign_keys; one engine; reads via own session) → Task 4.
- §3 all 11 tables with exact columns/uniques/indexes (university_meta, crawl_runs, org_units, crawl_graph_nodes, crawl_graph_edges, crawl_page_cache, crawl_extraction_attempts, crawl_extraction_failures, professors, academicians, professor_affiliations) → Task 3 (+ enums Task 2).
- §4 lifecycle (resolve_db_path, open_fresh backup-then-delete + rebuild + meta row, open_resume missing-DB error + create_all + reconcile) → Task 9.
- §5 DB worker (single coroutine queue, Future per command, claim atomic select+update, exceptions not swallowed) + full command set → Tasks 6–8.
- §6 dedup (name_key normalization, same-college merge, cross-college email→homepage→external_link, academician split, SaveResult, idempotency, known limitations in comments) → Tasks 5 + 8.
- §7 schema_version constant + ensure_columns (no Alembic) → Tasks 3/4 (+ resume calls it Task 9).
- §8 public interface → verified Task 10 Step 3.
- §9 node_key three rules + form-pagination distinct keys + cross-college shared-detail note → Task 5.
- §10 test list (build/PRAGMA; fresh backup + no-delete-on-failure; resume in_progress→retry/done kept; DBWriter serial/Future/non-fatal error; dedup merge/affiliation/academician/idempotent; node_key rules) → covered across Tasks 3–9.
- §11 "不做" (Alembic, multi-writer/pool, fuzzy dedup, snapshot versioning) → none introduced. ✓

**2. Bug-risk-memo guards** — `claim_next` honors `exclude_node_keys` (SP6 single-run no-reclaim) and `max_attempts` (Task 6 tests); `page_cache` keyed by identity URL so per-page form-pagination rows don't collide (Task 7 test); `node_key` includes `org_unit_id` so a cross-org shared detail URL yields distinct nodes by design, with affiliation-level merge in `save_professors` (Tasks 5/8); reconciliation runs before the writer starts (single-threaded by construction, Task 9). Synthetic-URL byte-matching and late-`/complete` idempotency are SP3/SP4 concerns, not SP2.

**3. Type/name consistency** — `NodeSpec`/`OrgUnitSpec`/`PageCachePayload`/`ClaimedNode`/`SaveResult`/`ProfessorPayload`, the `DBWriter` method names, model class names (`GraphNode`, `GraphEdge`, `PageCache`, `ExtractionAttempt`, `ExtractionFailure`, `Professor`, `Academician`, `ProfessorAffiliation`, `UniversityMeta`, `CrawlRun`, `OrgUnit`), and `utcnow_iso`/`SCHEMA_VERSION` are used identically across tasks and tests. Enum members are StrEnum so they bind to `String` columns as their value. Test imports match exported names.

**Note on two code blocks needing inline tidy-up:** Task 8 Step 3 and Task 9 Step 3 each contain a deliberately-flagged placeholder line followed by the exact replacement (the `except` failure-recording block, and the `select(UniversityMeta.id)` import fix). Apply the replacement as written — these are called out so the final code is clean.
