# dext_recommend R4 Professor Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the catalog-backed `ProfessorFactPort` (R4): a read-only `CatalogProfessorFactAdapter` + `CatalogSqliteFactReader` that assembles immutable `ProfessorDetail` (with a composed `FactBundle`) and batch `ProfessorFact` rows from the pinned ACTIVE build's catalog SQLite, never importing `dext_graph`, never blocking the event loop, never leaking contacts into the fact bundle.

**Architecture:** Two new layers mirror the existing R2 catalog release adapter — a `CatalogSqliteFactReader` (raw `sqlite3.Row` maps, `mode=ro` + `query_only=ON`, offloaded via `asyncio.to_thread` + `asyncio.wait_for`) and a `CatalogProfessorFactAdapter` that implements `ProfessorFactPort` (domain mapping, dedup/chunk, visibility/permission gates, FactBundle assembly). All SQL is bound to the caller's `ActiveBuildSnapshot.build_id`; no staging reads, no cross-build assembly, no cache. Evidence/provenance helpers live in a new `facts/evidence.py` (pure functions) and `facts/source_urls.py` (canonical URL normalization). Tests use temporary SQLite fixtures with the real published table/column names and typical JSON payloads.

**Tech Stack:** Python 3.11, `sqlite3` (stdlib, read-only URI), `asyncio.to_thread`/`asyncio.wait_for`, dataclasses (frozen+slots), `json.loads`, pytest (`asyncio_mode="auto"`), `uv` runner. Source of truth: `docs/superpowers/specs/2026-07-02-dext-recommend-04b-professor-facts-impl-design.md` (R4b) and `docs/superpowers/specs/2026-06-30-dext-recommendation-04-professor-facts-design.md` (R4 high-level). Published schema of record: `src/dext_graph/catalog/models.py` (`CATALOG_SCHEMA_VERSION = 6`).

## Global Constraints

- **Run tests:** `uv run pytest tests/dext_recommend/ -q` (single-module green is the bar; do NOT require full-repo green).
- **One test at a time:** `uv run pytest tests/dext_recommend/test_<name>.py::test_name -v`.
- **TDD strict:** write failing test → run RED → implement minimal → run GREEN → commit one conventional commit (`feat(rec): …` / `test(rec): …` / `docs(rec): …` / `fix(rec): …`).
- **No live catalog in tests:** all R4 tests build a temporary SQLite fixture with the real published table/column names and constraints. Never point at `data/catalog/catalog.db` for unit tests. Live acceptance against a real ACTIVE build is a separate, manual gate (R4b §6 last bullet) — do not block unit green on it.
- **Import boundary (invariant):** `dext_recommend` (any submodule, incl. `adapters._catalog_fact_reader`) MUST NOT import `dext_graph` / `dext` / `dext_monitor` / `dext_competition`. The published schema is duplicated as a frozen constant in the reader. `dext_grounded` is allowed (shared contract).
- **Single in-flight / read-only:** every reader connection is `mode=ro` + `PRAGMA query_only=ON`. No writes, no WAL mutation. The whole blocking read is offloaded with `asyncio.to_thread` and bounded by an adapter timeout via `asyncio.wait_for`.
- **Build pin:** every SQL statement is bound to the caller's `snapshot.build_id`. Never read staging, never mix builds, never refresh the snapshot mid-request. `profile_hash=null` is allowed in detail output but must carry a `profile_hash_missing` risk flag; R4 never caches.
- **Immutability:** all DTOs (`ProfessorFact`, `ProfessorDetail`, `FactBundle`, `FactItem`, `SourceRef`) are `@dataclass(frozen=True, slots=True)`; tuples via `__post_init__`; mappings frozen via `freeze_mapping`. `FactItem` with no `source_refs` MUST be `ContentClass.UNCERTAIN` (grounded contract).
- **Contacts isolation (invariant):** contacts (`email`/`phone`) NEVER enter `FactItem`, `FactBundle.source_refs`, or any `FactBundle` field. They appear only on `ProfessorDetail.contacts` and only when both `include_contacts=True` AND `viewer_permissions.include_contacts=True` (double gate). The adapter guarantees `contacts={}` on its own side regardless of caller.
- **Error safety:** JSON/schema/SQLite operational errors are normalized to a safe `ReadinessSourceError("catalog", reason)` (no raw payload, no contact leak, no credentials). `get_detail` on missing/inactive/excluded entity raises `ProfessorFactNotFound(LookupError)`. Review entity returned only when `viewer_permissions.can_view_review=True`.
- **Branch:** work on `next1` (current). Do not create a new branch unless asked.
- **Worktree hygiene:** before Task 1, run `git status --short`. This repo may already contain unrelated dirty files; preserve them. Stage only files listed by the current task, review `git diff --cached` before each commit, and do not “clean up” unrelated user edits. If a task overlaps an already-dirty file (notably `src/dext_recommend/ports/_fakes.py`), inspect the pre-existing diff and keep it intact.
- **Commit signing off:** use `git -c commit.gpgsign=false commit -m "..."` to avoid gpg prompts.
- **Platform:** Windows + Git Bash. `LF will be replaced by CRLF` warnings are benign.

---

## File Structure

**Created (src/dext_recommend/):**
- `adapters/_catalog_fact_reader.py` — `CatalogSqliteFactReader` + `CatalogProfessorFactReader` Protocol: raw SQL against published tables, returns `sqlite3.Row` maps; ro/query_only connections; `to_thread`+`wait_for`.
- `adapters/_catalog_fact_schema.py` — frozen published-schema contract: `MIN_FACT_CATALOG_SCHEMA_VERSION = 1`, required-tables/columns capability list, SQL constants, JSON-key constants. The single place R4 pins the catalog contract (no `dext_graph` import).
- `adapters/catalog_professor_facts.py` — `CatalogProfessorFactAdapter(ProfessorFactPort)`: `hydrate` + `get_detail`; dedup/chunk, authority mapping, visibility/permission gates, FactBundle composition, contact double-gate.
- `facts/evidence.py` — pure helpers: `select_statement_snippets`, `select_publication_snippets`, `build_fact_items`, `weak_explanation` text; no I/O.
- `facts/source_urls.py` — pure `canonicalize_source_url` (strip tracking params, keep fetched/verified time) + `dedupe_source_urls`.
- `facts/_ids.py` — pure `chunk_entity_ids(ids, limit)` (SQLite parameter limit, default 900) and `dedupe_entity_ids(ids)`.
- `facts/__init__.py` — re-export the public fact helpers.

**Modified (src/dext_recommend/):**
- `ports/professor_facts.py` — add `ProfessorFactNotFound(LookupError)`; add `fact_bundle: FactBundle` field to `ProfessorDetail` (R4b §4); add `FactBundle`/`FactItem`/`ContentClass` re-exports for the port module's consumers; keep `__all__` in sync.
- `ports/__init__.py` — re-export `ProfessorFactNotFound` (and keep existing port exports intact).
- `ports/_fakes.py` — `FakeProfessorFactPort` already exists; extend to support `ProfessorFactNotFound` raise + `fact_bundle` on returned details (minimal, only if a test needs it).
- `adapters/__init__.py` — re-export `CatalogProfessorFactAdapter`, `CatalogSqliteFactReader`, `CatalogProfessorFactReader`.
- `__init__.py` — re-export `ProfessorFactNotFound`, `CatalogProfessorFactAdapter`, `CatalogSqliteFactReader`.
- `config.py` — add `fact_read_timeout: float = Field(default=5.0, gt=0.0)` and `fact_chunk_size: int = Field(default=900, ge=1)` (R4 reader knobs).

**Created (tests/dext_recommend/):**
- `_factfixtures.py` — shared SQLite fixture builder: `build_catalog_db(tmp_path, *, build_rows, professors, profiles, observations, statements, mentions, topic_links, topics, findings, source_documents, source_tasks, entity_observations)` with the real published schema DDL.
- `test_recommend_catalog_fact_reader.py` — reader-level SQL/timeout/thread-offload/query_only/schema-capability tests.
- `test_recommend_catalog_fact_adapter.py` — adapter `hydrate` + `get_detail` + FactBundle invariants + visibility/permission + error-safety tests.
- `test_recommend_facts_evidence.py` — pure evidence/snippet/FactItem tests.
- `test_recommend_facts_source_urls.py` — pure URL canonicalization tests.

**Modified (tests/dext_recommend/):**
- `test_recommend_import_boundary.py` — add `dext_recommend.adapters._catalog_fact_reader`, `dext_recommend.adapters._catalog_fact_schema`, `dext_recommend.adapters.catalog_professor_facts`, `dext_recommend.facts.evidence`, `dext_recommend.facts.source_urls`, `dext_recommend.facts._ids` to the import-boundary + adapter-no-`dext_graph` checks.
- `test_recommend_fake_ports.py` — extend `FakeProfessorFactPort` assertions to cover `fact_bundle` presence on `ProfessorDetail` (if the existing fake is reused by R4-aware tests).

---

## Task 1: Published-schema contract module + capability check

**Files:**
- Create: `src/dext_recommend/adapters/_catalog_fact_schema.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_reader.py`

**Interfaces:**
- Produces: `MIN_FACT_CATALOG_SCHEMA_VERSION = 1`; `REQUIRED_FACT_TABLES: tuple[str, ...]`; `REQUIRED_FACT_COLUMNS: Mapping[str, tuple[str, ...]]`; `FACT_SQLITE_PARAM_LIMIT = 999`; `SCHEMA_VERSION_SQL`, `TABLE_LIST_SQL`, `COLUMN_LIST_SQL` constants. No imports from `dext_graph`.

- [ ] **Step 1: Write the failing test — schema constants are present and dext_graph-free**

Create `tests/dext_recommend/test_recommend_catalog_fact_reader.py`:

```python
import sys
import importlib


def test_catalog_fact_schema_constants():
    from dext_recommend.adapters._catalog_fact_schema import (
        MIN_FACT_CATALOG_SCHEMA_VERSION, REQUIRED_FACT_TABLES,
        REQUIRED_FACT_COLUMNS, FACT_SQLITE_PARAM_LIMIT,
    )
    assert MIN_FACT_CATALOG_SCHEMA_VERSION == 1
    for t in (
        "canonical_professors", "professor_profiles", "professor_observations",
        "entity_observations", "research_statements", "publication_mentions",
        "statement_topic_links", "topics", "quality_findings",
        "source_documents", "build_source_tasks",
    ):
        assert t in REQUIRED_FACT_TABLES, f"missing required table {t}"
    # profile payload JSON keys are part of the contract
    assert "payload_json" in REQUIRED_FACT_COLUMNS["professor_profiles"]
    assert "payload_json" in REQUIRED_FACT_COLUMNS["professor_observations"]
    assert "review_status" in REQUIRED_FACT_COLUMNS["statement_topic_links"]
    assert FACT_SQLITE_PARAM_LIMIT == 999


def test_catalog_fact_schema_does_not_import_dext_graph():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        importlib.import_module("dext_recommend.adapters._catalog_fact_schema")
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_catalog_fact_schema_constants tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_catalog_fact_schema_does_not_import_dext_graph -v`
Expected: FAIL with `ModuleNotFoundError: dext_recommend.adapters._catalog_fact_schema`

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/adapters/_catalog_fact_schema.py`:

```python
"""Frozen published-catalog contract for R4 professor-fact reads.

The runtime must NOT import dext_graph; this module is the only place R4 pins
the published table/column names and JSON payload keys it depends on. Mirror
of dext_graph.catalog.models CATALOG_SCHEMA_VERSION>=1 published tables.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# R4b §2: facts contract minimum schema version. A catalog that satisfies the
# version but lacks the topic/evidence/profile tables is still unusable for R4;
# the required-tables capability check below enforces that.
MIN_FACT_CATALOG_SCHEMA_VERSION = 1

# SQLite default host parameter limit is 999; keep a safety margin.
FACT_SQLITE_PARAM_LIMIT = 999

REQUIRED_FACT_TABLES: tuple[str, ...] = (
    "canonical_professors",
    "professor_profiles",
    "professor_observations",
    "entity_observations",
    "research_statements",
    "publication_mentions",
    "statement_topic_links",
    "topics",
    "quality_findings",
    "source_documents",
    "build_source_tasks",
)

REQUIRED_FACT_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "canonical_professors": (
        "entity_id", "build_id", "name", "title_raw", "title_family",
        "role_status", "role_reason_codes", "master_eligibility",
        "phd_eligibility", "research_areas_text", "bio", "email", "phone",
        "profile_url", "external_url", "active", "completeness",
    ),
    "professor_profiles": (
        "build_id", "entity_id", "profile_hash", "payload_json", "created_at",
    ),
    "professor_observations": (
        "id", "university_id", "source_url", "source_page_kind",
        "source_document_id", "payload_json", "active",
    ),
    "entity_observations": (
        "entity_id", "observation_id", "build_id",
    ),
    "research_statements": (
        "id", "build_id", "entity_id", "observation_id", "normalized_text",
        "language",
    ),
    "publication_mentions": (
        "id", "build_id", "entity_id", "observation_id", "normalized_text",
        "year", "confidence", "needs_review",
    ),
    "statement_topic_links": (
        "build_id", "statement_id", "taxonomy_version", "topic_id",
        "relation_type", "evidence_span", "review_status", "provenance_ref",
    ),
    "topics": ("taxonomy_version", "id", "canonical_name", "kind", "status"),
    "quality_findings": (
        "id", "build_id", "severity", "code", "entity_id", "details_json",
        "resolved",
    ),
    "source_documents": ("id", "url", "fetched_at"),
    "build_source_tasks": ("build_id", "university_id", "university_name"),
})

# JSON keys inside professor_profiles.payload_json (R4b §2 authority matrix).
PROFILE_PAYLOAD_KEYS = (
    "university_id", "org_unit_ids", "city", "topic_ids", "role_status",
    "master_eligibility", "phd_eligibility", "title_family", "profile_hash",
    "provenance_ref",
)

# JSON keys inside professor_observations.payload_json (R4b §2 org-unit display).
OBSERVATION_PAYLOAD_AFFILIATION_KEYS = ("org_unit_name",)

SCHEMA_VERSION_SQL = "SELECT value FROM catalog_meta WHERE key='schema_version'"
TABLE_LIST_SQL = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
)
COLUMN_LIST_SQL = "PRAGMA table_info({table})"


__all__ = [
    "FACT_SQLITE_PARAM_LIMIT",
    "MIN_FACT_CATALOG_SCHEMA_VERSION",
    "OBSERVATION_PAYLOAD_AFFILIATION_KEYS",
    "PROFILE_PAYLOAD_KEYS",
    "REQUIRED_FACT_COLUMNS",
    "REQUIRED_FACT_TABLES",
    "SCHEMA_VERSION_SQL",
    "TABLE_LIST_SQL",
    "COLUMN_LIST_SQL",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_catalog_fact_schema_constants tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_catalog_fact_schema_does_not_import_dext_graph -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_catalog_fact_schema.py tests/dext_recommend/test_recommend_catalog_fact_reader.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 frozen published-catalog schema contract module"
```

---

## Task 2: SQLite fixture builder (test helper)

**Files:**
- Create: `tests/dext_recommend/_factfixtures.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_reader.py` (add a smoke test that builds a DB and reads it back)

**Interfaces:**
- Produces: `build_catalog_db(tmp_path, **rows) -> Path` with the real published DDL; `insert_fact_rows(conn, **rows)` helper. Row kwargs default to `()`. JSON columns accept pre-serialized strings.

- [ ] **Step 1: Write the failing test — fixture builder produces a readable catalog**

Add to `tests/dext_recommend/test_recommend_catalog_fact_reader.py`:

```python
import sqlite3
from contextlib import closing

from tests.dext_recommend._factfixtures import build_catalog_db


def test_build_catalog_db_creates_real_schema(tmp_path):
    path = build_catalog_db(
        tmp_path,
        schema_version=6,
        graph_builds=[("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}")],
        canonical_professors=[
            ("e1", "b1", "Prof A", "Prof.", "professor", "included", "[]",
             "confirmed", "unknown", None, None, None, None, "https://x/p", None, 1, 0.9),
        ],
        professor_profiles=[
            ("b1", "e1", "h1", "tv", "ti", "np", 10,
             '{"university_id":"u1","org_unit_ids":["ou_cs"],"city":null,"topic_ids":[],"profile_hash":"h1"}',
             "2026-01-01T00:00:00+00:00"),
        ],
    )
    with closing(sqlite3.connect(path)) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        for required in ("canonical_professors", "professor_profiles",
                         "research_statements", "publication_mentions",
                         "statement_topic_links", "quality_findings"):
            assert required in names
        ver = conn.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
        assert ver is not None and ver[0] == "6"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_build_catalog_db_creates_real_schema -v`
Expected: FAIL with `ModuleNotFoundError: tests.dext_recommend._factfixtures`

- [ ] **Step 3: Write minimal implementation**

Create `tests/dext_recommend/_factfixtures.py`:

```python
"""Shared SQLite fixture builder for R4 professor-fact tests.

Mirrors the real published catalog DDL (dext_graph.catalog.models, schema
version 6) with the tables/columns R4 reads. Constraints match production so
fixture inserts exercise the same CHECK/PRIMARY KEY rules. JSON columns take
pre-serialized strings (callers use json.dumps).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

# A trimmed-but-faithful subset of the published DDL: every table R4 reads,
# with the columns and CHECK constraints R4 relies on. Mirrors
# src/dext_graph/catalog/models.py CATALOG_SCHEMA_VERSION=6.
_SCHEMA = """
CREATE TABLE catalog_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE graph_builds (
    id TEXT PRIMARY KEY, status TEXT NOT NULL,
    curation_version TEXT NOT NULL, taxonomy_version TEXT,
    graph_schema_version INTEGER NOT NULL, vector_schema_version INTEGER NOT NULL,
    settings_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT, finished_at TEXT, last_error TEXT
);
CREATE TABLE build_source_tasks (
    build_id TEXT NOT NULL, university_id TEXT NOT NULL,
    university_name TEXT NOT NULL, abbr TEXT NOT NULL,
    source_path TEXT NOT NULL, ordinal INTEGER NOT NULL,
    status TEXT NOT NULL, source_snapshot_id TEXT,
    snapshot_reused INTEGER NOT NULL DEFAULT 0,
    rows_read INTEGER NOT NULL DEFAULT 0,
    observations_written INTEGER NOT NULL DEFAULT 0,
    observations_inserted INTEGER NOT NULL DEFAULT 0,
    observations_reused INTEGER NOT NULL DEFAULT 0,
    documents_seen INTEGER NOT NULL DEFAULT 0,
    documents_inserted INTEGER NOT NULL DEFAULT 0,
    findings INTEGER NOT NULL DEFAULT 0,
    rejected_rows INTEGER NOT NULL DEFAULT 0,
    deactivation_eligible INTEGER NOT NULL DEFAULT 0,
    observations_inactivated INTEGER NOT NULL DEFAULT 0,
    last_error TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY (build_id, university_id),
    UNIQUE (build_id, ordinal)
);
CREATE TABLE source_documents (
    id TEXT PRIMARY KEY, university_id TEXT NOT NULL, url TEXT NOT NULL,
    content_hash TEXT NOT NULL, title TEXT, text_zstd BLOB, fetched_at TEXT,
    first_seen_build TEXT NOT NULL, last_seen_build TEXT NOT NULL,
    UNIQUE (url, content_hash)
);
CREATE TABLE professor_observations (
    id TEXT PRIMARY KEY, university_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL, source_professor_id INTEGER,
    source_url TEXT, source_page_kind TEXT NOT NULL,
    extraction_batch_size INTEGER, source_content_hash TEXT,
    source_document_id TEXT, org_unit_source_id INTEGER,
    name_raw TEXT NOT NULL, name_key TEXT NOT NULL,
    payload_json TEXT NOT NULL, row_hash TEXT NOT NULL,
    provenance_grade TEXT NOT NULL,
    first_seen_build TEXT NOT NULL, last_seen_build TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);
CREATE TABLE quality_findings (
    id TEXT PRIMARY KEY, build_id TEXT NOT NULL, severity TEXT NOT NULL,
    code TEXT NOT NULL, entity_id TEXT, observation_id TEXT,
    details_json TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE entities (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    merged_into_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE entity_observations (
    entity_id TEXT NOT NULL, observation_id TEXT NOT NULL,
    match_method TEXT NOT NULL, match_score REAL, build_id TEXT NOT NULL,
    PRIMARY KEY (build_id, observation_id)
);
CREATE TABLE canonical_professors (
    entity_id TEXT NOT NULL, build_id TEXT NOT NULL, name TEXT NOT NULL,
    title_raw TEXT, title_family TEXT NOT NULL,
    role_status TEXT NOT NULL, role_reason_codes TEXT NOT NULL,
    master_eligibility TEXT NOT NULL, phd_eligibility TEXT NOT NULL,
    research_areas_text TEXT, bio TEXT, email TEXT, phone TEXT,
    profile_url TEXT, external_url TEXT,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    completeness REAL NOT NULL,
    PRIMARY KEY (entity_id, build_id)
);
CREATE TABLE research_statements (
    id TEXT NOT NULL, build_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    observation_id TEXT NOT NULL, raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL, language TEXT NOT NULL,
    statement_hash TEXT NOT NULL,
    PRIMARY KEY (build_id, id)
);
CREATE TABLE publication_mentions (
    id TEXT NOT NULL, build_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    observation_id TEXT NOT NULL, raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL, doi TEXT, year INTEGER,
    confidence REAL NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (build_id, id, observation_id)
);
CREATE TABLE professor_profiles (
    build_id TEXT NOT NULL, entity_id TEXT NOT NULL, profile_hash TEXT NOT NULL,
    template_version TEXT NOT NULL, tokenizer_identity TEXT NOT NULL,
    normalized_profile TEXT NOT NULL, token_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (build_id, entity_id)
);
CREATE TABLE taxonomy_versions (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, parent_version TEXT,
    manifest_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE topics (
    taxonomy_version TEXT NOT NULL, id TEXT NOT NULL,
    canonical_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, created_method TEXT NOT NULL,
    PRIMARY KEY (taxonomy_version, id)
);
CREATE TABLE statement_topic_links (
    build_id TEXT NOT NULL, statement_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL, topic_id TEXT NOT NULL,
    relation_type TEXT NOT NULL, evidence_span TEXT NOT NULL,
    method TEXT NOT NULL, confidence REAL NOT NULL,
    review_status TEXT NOT NULL, provenance_ref TEXT NOT NULL,
    PRIMARY KEY (build_id, statement_id, topic_id, relation_type, evidence_span)
);
"""


def _entities_rows(rows):
    # entities rows: (id, kind, status, merged_into_id, created_at, updated_at)
    return rows


def build_catalog_db(tmp_path: Path, *, schema_version: int = 6, **row_sets) -> Path:
    """Create a catalog DB at tmp_path/catalog.db with the published DDL and rows.

    Each kwarg name matches a table; values are iterables of tuples in that
    table's column order (see _INSERTS below). Missing kwargs default to no rows.
    """
    path = tmp_path / "catalog.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO catalog_meta(key, value) VALUES ('schema_version', ?)",
            (str(schema_version),),
        )
        _insert_rows(conn, row_sets)
        conn.commit()
    return path


_INSERTS = {
    "graph_builds": (
        "INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,"
        "graph_schema_version,vector_schema_version,settings_json) VALUES (?,?,?,?,?,?,?)"
    ),
    "build_source_tasks": (
        "INSERT INTO build_source_tasks(build_id,university_id,university_name,"
        "abbr,source_path,ordinal,status,updated_at) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "source_documents": (
        "INSERT INTO source_documents(id,university_id,url,content_hash,fetched_at,"
        "first_seen_build,last_seen_build) VALUES (?,?,?,?,?,?,?)"
    ),
    "professor_observations": (
        "INSERT INTO professor_observations(id,university_id,source_snapshot_id,"
        "source_url,source_page_kind,name_raw,name_key,payload_json,row_hash,"
        "provenance_grade,first_seen_build,last_seen_build,active) "
        "VALUES (?,?,?,?,?,'fixture-name','fixture-key',?,?,?,?,?,?)"
    ),
    "quality_findings": (
        "INSERT INTO quality_findings(id,build_id,severity,code,entity_id,"
        "observation_id,details_json,resolved) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "entities": (
        "INSERT INTO entities(id,kind,status,merged_into_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?)"
    ),
    "entity_observations": (
        "INSERT INTO entity_observations(entity_id,observation_id,match_method,"
        "build_id) VALUES (?,?,?,?)"
    ),
    "canonical_professors": (
        "INSERT INTO canonical_professors(entity_id,build_id,name,title_raw,"
        "title_family,role_status,role_reason_codes,master_eligibility,"
        "phd_eligibility,research_areas_text,bio,email,phone,profile_url,"
        "external_url,active,completeness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "research_statements": (
        "INSERT INTO research_statements(id,build_id,entity_id,observation_id,"
        "raw_text,normalized_text,language,statement_hash) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "publication_mentions": (
        "INSERT INTO publication_mentions(id,build_id,entity_id,observation_id,"
        "raw_text,normalized_text,year,confidence,needs_review) "
        "VALUES (?,?,?,?,?,?,?,?,?)"
    ),
    "professor_profiles": (
        "INSERT INTO professor_profiles(build_id,entity_id,profile_hash,"
        "template_version,tokenizer_identity,normalized_profile,token_count,"
        "payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)"
    ),
    "taxonomy_versions": (
        "INSERT INTO taxonomy_versions(id,status,parent_version,manifest_hash,"
        "created_at) VALUES (?,?,?,?,?)"
    ),
    "topics": (
        "INSERT INTO topics(taxonomy_version,id,canonical_name,normalized_name,"
        "kind,status,created_method) VALUES (?,?,?,?,?,?,?)"
    ),
    "statement_topic_links": (
        "INSERT INTO statement_topic_links(build_id,statement_id,taxonomy_version,"
        "topic_id,relation_type,evidence_span,method,confidence,review_status,"
        "provenance_ref) VALUES (?,?,?,?,?,?,?,?,?,?)"
    ),
}


def _insert_rows(conn: sqlite3.Connection, row_sets: dict) -> None:
    for table, rows in row_sets.items():
        if table == "schema_version":
            continue
        if table not in _INSERTS:
            raise KeyError(f"unknown fixture table: {table}")
        rows = list(rows)
        if not rows:
            continue
        conn.executemany(_INSERTS[table], rows)


def dumps(value) -> str:
    """json.dumps with ensure_ascii=False for fixture payloads."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = ["build_catalog_db", "dumps"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_build_catalog_db_creates_real_schema -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/dext_recommend/_factfixtures.py tests/dext_recommend/test_recommend_catalog_fact_reader.py
git -c commit.gpgsign=false commit -m "test(rec): R4 SQLite fixture builder mirroring published catalog DDL"
```

---

## Task 3: CatalogSqliteFactReader — read-only connection + schema capability check

**Files:**
- Create: `src/dext_recommend/adapters/_catalog_fact_reader.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_reader.py`

**Interfaces:**
- Produces: `CatalogSqliteFactReader(path, *, timeout: float)`. Async `check_capability() -> int` (returns schema_version, raises `ReadinessSourceError` on missing file / missing tables / missing columns / unparseable version). Uses `mode=ro` + `PRAGMA query_only=ON` + `PRAGMA busy_timeout=5000`. All blocking work via `asyncio.to_thread` bounded by `asyncio.wait_for`.

- [ ] **Step 1: Write the failing test — check_capability returns version, missing file raises safe error**

Add to `tests/dext_recommend/test_recommend_catalog_fact_reader.py`:

```python
import asyncio
import pytest

from dext_recommend.adapters._catalog_fact_reader import CatalogSqliteFactReader
from dext_recommend.ports.release_readback import ReadinessSourceError

from tests.dext_recommend._factfixtures import build_catalog_db


def _active_build():
    return ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}")


def test_check_capability_returns_schema_version(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    version = asyncio.run(reader.check_capability())
    assert version == 6


def test_check_capability_missing_file_raises_safe(tmp_path):
    reader = CatalogSqliteFactReader(tmp_path / "missing.db", timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.check_capability())
    assert exc.value.source == "catalog"
    # no raw path leak of credentials; the reason is safe
    assert "not found" in exc.value.reason.lower() or "missing" in exc.value.reason.lower()


def test_check_capability_missing_required_table_raises_safe(tmp_path):
    # build a DB then drop a required table
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    import sqlite3 as _sqlite3
    with _sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE statement_topic_links")
        conn.commit()
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.check_capability())
    assert "statement_topic_links" in exc.value.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_check_capability_returns_schema_version tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_check_capability_missing_file_raises_safe tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_check_capability_missing_required_table_raises_safe -v`
Expected: FAIL with `ModuleNotFoundError: dext_recommend.adapters._catalog_fact_reader`

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/adapters/_catalog_fact_reader.py`:

```python
"""Catalog SQLite dialect seam for R4 professor-fact reads.

Read-only URI mode + query_only pragma guarantees no writes. Every blocking
read is offloaded with asyncio.to_thread and bounded by asyncio.wait_for so
the event loop never blocks on SQLite I/O. Never imports dext_graph; the
published schema is pinned in _catalog_fact_schema.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from dext_recommend.adapters._catalog_fact_schema import (
    COLUMN_LIST_SQL, MIN_FACT_CATALOG_SCHEMA_VERSION, REQUIRED_FACT_COLUMNS,
    REQUIRED_FACT_TABLES, SCHEMA_VERSION_SQL, TABLE_LIST_SQL,
)
from dext_recommend.ports.release_readback import ReadinessSourceError


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ReadinessSourceError("catalog", f"catalog not found: {resolved}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class CatalogProfessorFactReader(Protocol):
    async def check_capability(self) -> int: ...


class CatalogSqliteFactReader:
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        self._path = Path(path)
        self._timeout = timeout

    def _connect_ro(self) -> sqlite3.Connection:
        return _connect_ro(self._path)

    async def check_capability(self) -> int:
        def _check() -> int:
            with closing(self._connect_ro()) as conn:
                version_row = conn.execute(SCHEMA_VERSION_SQL).fetchone()
                if version_row is None:
                    raise ReadinessSourceError(
                        "catalog", "schema_version not recorded in catalog_meta",
                    )
                try:
                    version = int(version_row[0])
                except (TypeError, ValueError) as exc:
                    raise ReadinessSourceError(
                        "catalog", f"unparseable schema_version: {version_row[0]!r}",
                    ) from exc
                if version < MIN_FACT_CATALOG_SCHEMA_VERSION:
                    raise ReadinessSourceError(
                        "catalog",
                        f"schema_version {version} < required "
                        f"{MIN_FACT_CATALOG_SCHEMA_VERSION}",
                    )
                present = {
                    str(r[0]) for r in conn.execute(TABLE_LIST_SQL)
                }
                missing_tables = [
                    t for t in REQUIRED_FACT_TABLES if t not in present
                ]
                if missing_tables:
                    raise ReadinessSourceError(
                        "catalog",
                        f"missing required tables: {missing_tables}",
                    )
                for table, required_cols in REQUIRED_FACT_COLUMNS.items():
                    actual = {
                        str(r[1]) for r in conn.execute(
                            COLUMN_LIST_SQL.format(table=table)
                        )
                    }
                    missing_cols = [c for c in required_cols if c not in actual]
                    if missing_cols:
                        raise ReadinessSourceError(
                            "catalog",
                            f"table {table} missing columns: {missing_cols}",
                        )
                return version
        return await asyncio.wait_for(
            asyncio.to_thread(_check), self._timeout,
        )


__all__ = ["CatalogProfessorFactReader", "CatalogSqliteFactReader"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_check_capability_returns_schema_version tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_check_capability_missing_file_raises_safe tests/dext_recommend/test_recommend_catalog_fact_reader.py::test_check_capability_missing_required_table_raises_safe -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_catalog_fact_reader.py tests/dext_recommend/test_recommend_catalog_fact_reader.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 CatalogSqliteFactReader read-only connection + capability check"
```

---

## Task 4: Reader — `read_fact_rows` for hydrate (batch authority fields)

**Files:**
- Modify: `src/dext_recommend/adapters/_catalog_fact_reader.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_reader.py`

**Interfaces:**
- Produces: `async read_fact_rows(build_id, entity_ids) -> tuple[Mapping[str, Any], ...]` — one row per entity joined across `canonical_professors` (active, non-excluded) + `professor_profiles.payload_json` (university_id, org_unit_ids, city, topic_ids). Stable order by `entity_id`. Empty list → returns `()`. Chunked by `FACT_SQLITE_PARAM_LIMIT - 1` (the build_id placeholder).
- Consumes: `FACT_SQLITE_PARAM_LIMIT` from Task 1.

- [ ] **Step 1: Write the failing test — read_fact_rows joins profile payload + dedups + chunks**

Add to `tests/dext_recommend/test_recommend_catalog_fact_reader.py`:

```python
from tests.dext_recommend._factfixtures import build_catalog_db, dumps


def _profile_payload(entity_id, *, university_id="u1", org_unit_ids=("ou_cs",),
                     city=None, topic_ids=(), profile_hash=None):
    return dumps({
        "university_id": university_id,
        "org_unit_ids": list(org_unit_ids),
        "city": city,
        "topic_ids": list(topic_ids),
        "profile_hash": profile_hash or f"h_{entity_id}",
    })


def test_read_fact_rows_returns_active_non_excluded(tmp_path):
    profs = [
        # e1 included, has profile
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e1", None, 1, 0.9),
        # e2 excluded -> must NOT appear
        ("e2", "b1", "B", "Prof.", "professor", "excluded", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e2", None, 1, 0.5),
        # e3 inactive -> must NOT appear
        ("e3", "b1", "C", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e3", None, 0, 0.5),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile_payload("e1"), "2026-01-01T00:00:00+00:00"),
        ("b1", "e2", "h2", "tv", "ti", "np", 10, _profile_payload("e2"), "2026-01-01T00:00:00+00:00"),
        ("b1", "e3", "h3", "tv", "ti", "np", 10, _profile_payload("e3"), "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_fact_rows("b1", ["e1", "e2", "e3"]))
    ids = [r["entity_id"] for r in rows]
    assert ids == ["e1"]  # e2 excluded, e3 inactive


def test_read_fact_rows_parses_payload_authority_fields(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10,
         _profile_payload("e1", university_id="u_tsinghua",
                          org_unit_ids=("ou_cs", "ou_ai"),
                          city="Beijing", topic_ids=("t1", "t2")),
         "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_fact_rows("b1", ["e1"]))
    assert len(rows) == 1
    r = rows[0]
    assert r["entity_id"] == "e1"
    assert r["display_name"] == "A"
    assert r["university_id"] == "u_tsinghua"
    assert tuple(r["org_unit_ids"]) == ("ou_cs", "ou_ai")
    assert r["city_name"] == "Beijing"
    assert tuple(r["topic_ids"]) == ("t1", "t2")
    assert r["profile_hash"] == "h1"
    assert r["master_eligibility"] == "confirmed"
    assert r["role_status"] == "included"


def test_read_fact_rows_empty_ids_returns_empty(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_fact_rows("b1", []))
    assert rows == ()


def test_read_fact_rows_chunked_over_param_limit(tmp_path):
    # 1200 entities, reader must chunk below FACT_SQLITE_PARAM_LIMIT
    profs = [
        (f"e{i}", "b1", f"N{i}", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5)
        for i in range(1200)
    ]
    profiles = [
        ("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10,
         _profile_payload(f"e{i}"), "2026-01-01T00:00:00+00:00")
        for i in range(1200)
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=10.0)
    ids = [f"e{i}" for i in range(1200)]
    rows = asyncio.run(reader.read_fact_rows("b1", ids))
    got = {r["entity_id"] for r in rows}
    assert len(got) == 1200
    assert got == set(ids)


def test_read_fact_rows_invalid_payload_raises_safe_error(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, "not-json{", "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.read_fact_rows("b1", ["e1"]))
    assert exc.value.source == "catalog"
    # raw payload must NOT leak into the reason
    assert "not-json{" not in exc.value.reason
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py -k read_fact_rows -v`
Expected: FAIL with `AttributeError: 'CatalogSqliteFactReader' object has no attribute 'read_fact_rows'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/dext_recommend/adapters/_catalog_fact_reader.py` (before `__all__`):

```python
import json

from dext_recommend.adapters._catalog_fact_schema import FACT_SQLITE_PARAM_LIMIT


_FACT_ROWS_SQL = """
SELECT cp.entity_id, cp.name AS display_name, cp.title_family,
       cp.role_status, cp.master_eligibility, cp.phd_eligibility,
       cp.profile_url, cp.profile_url AS external_url,
       pp.profile_hash, pp.payload_json AS profile_payload_json,
       cp.research_areas_text
FROM canonical_professors cp
LEFT JOIN professor_profiles pp
  ON pp.build_id=cp.build_id AND pp.entity_id=cp.entity_id
WHERE cp.build_id=? AND cp.active=1 AND cp.role_status!='excluded'
  AND cp.entity_id IN (%s)
ORDER BY cp.entity_id
"""


def _safe_json(payload: str | None, *, entity_id: str) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ReadinessSourceError(
            "catalog", f"invalid profile payload_json for entity {entity_id}",
        ) from exc
    if not isinstance(value, dict):
        raise ReadinessSourceError(
            "catalog", f"profile payload_json not an object for entity {entity_id}",
        )
    return value


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v is not None)
    return (str(value),)
```

Add the method to the `CatalogSqliteFactReader` class:

```python
    async def read_fact_rows(
        self, build_id: str, entity_ids: list[str],
    ) -> tuple[Mapping[str, Any], ...]:
        if not entity_ids:
            return ()
        # reserve 1 placeholder for build_id
        chunk_size = max(1, FACT_SQLITE_PARAM_LIMIT - 1)

        def _read_chunk(chunk: tuple[str, ...]) -> tuple[Mapping[str, Any], ...]:
            placeholders = ",".join("?" for _ in chunk)
            sql = _FACT_ROWS_SQL % placeholders
            with closing(self._connect_ro()) as conn:
                rows = conn.execute(sql, (build_id, *chunk)).fetchall()
            out: list[Mapping[str, Any]] = []
            for raw in rows:
                d = dict(raw)
                payload = _safe_json(
                    d.pop("profile_payload_json", None),
                    entity_id=d["entity_id"],
                )
                d["university_id"] = payload.get("university_id")
                d["org_unit_ids"] = _coerce_str_tuple(payload.get("org_unit_ids"))
                d["city_name"] = payload.get("city")
                d["topic_ids"] = _coerce_str_tuple(payload.get("topic_ids"))
                if not d.get("profile_hash"):
                    d["profile_hash"] = None
                ph = payload.get("profile_hash")
                if ph:
                    d["profile_hash"] = str(ph)
                out.append(d)
            return tuple(out)

        async def _read_all() -> tuple[Mapping[str, Any], ...]:
            ordered = tuple(dict.fromkeys(entity_ids))
            results: list[Mapping[str, Any]] = []
            for i in range(0, len(ordered), chunk_size):
                chunk = ordered[i:i + chunk_size]
                results.extend(await asyncio.to_thread(_read_chunk, chunk))
            return tuple(results)

        return await asyncio.wait_for(_read_all(), self._timeout)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py -k read_fact_rows -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_catalog_fact_reader.py tests/dext_recommend/test_recommend_catalog_fact_reader.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 read_fact_rows batch authority-field read with chunking + safe JSON"
```

---

## Task 5: Reader — `read_detail_rows` for get_detail (single-entity full evidence)

**Files:**
- Modify: `src/dext_recommend/adapters/_catalog_fact_reader.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_reader.py`

**Interfaces:**
- Produces: `async read_detail_rows(build_id, entity_id) -> CatalogProfessorDetailRows | None`. Returns `None` when the entity is missing/inactive/excluded. The returned object holds the canonical_professors row, profile payload (parsed), and tuples of raw rows for statements, mentions, topic links (with topic canonical_name), findings, observation/source-url rows. Review entities are returned (the adapter gates on `viewer_permissions`); excluded/inactive return `None`.

- [ ] **Step 1: Write the failing test — read_detail_rows returns full evidence for an active included entity**

Add to `tests/dext_recommend/test_recommend_catalog_fact_reader.py`:

```python
def test_read_detail_rows_returns_full_evidence(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", "areas", "bio1", "e@x", "123", "https://x/p", "https://x/e", 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10,
         _profile_payload("e1", university_id="u1", org_unit_ids=("ou_cs",), city="Beijing"),
         "2026-01-01T00:00:00+00:00"),
    ]
    observations = [
        ("obs1", "u1", "snap1", "https://x/p", "single_profile",
         dumps({"affiliations": [{"org_unit_name": "Dept CS"}]}),
         "rh1", "direct", "b1", "b1", 1),
    ]
    entity_observations = [("e1", "obs1", "strong", "b1")]
    statements = [
        ("s1", "b1", "e1", "obs1", "raw works on NLP", "works on NLP", "en", "sh1"),
    ]
    mentions = [
        ("m1", "b1", "e1", "obs1", "raw paper", "Paper A", 2024, 0.9, 0),
    ]
    topics = [("tax-v1", "t1", "NLP", "nlp", "method", "active", "llm")]
    topic_links = [
        ("b1", "s1", "tax-v1", "t1", "PRIMARY_TOPIC", "span", "llm", 0.95, "approved",
         "catalog:research-statement:b1:s1"),
    ]
    findings = [
        ("f1", "b1", "warning", "incomplete_profile", "e1", None,
         dumps({"note": "missing phone"}), 0),
    ]
    source_docs = [
        ("doc1", "u1", "https://x/p", "ch1", "2026-01-01T00:00:00+00:00", "b1", "b1"),
    ]
    source_tasks = [
        ("b1", "u1", "Tsinghua", "tsinghua", "/src", 0, "COMPLETED", "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
        professor_observations=observations, entity_observations=entity_observations,
        research_statements=statements, publication_mentions=mentions,
        topics=topics, statement_topic_links=topic_links,
        quality_findings=findings, source_documents=source_docs,
        build_source_tasks=source_tasks, entities=[("e1", "professor", "active", None, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")],
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_detail_rows("b1", "e1"))
    assert rows is not None
    assert rows.canonical["display_name"] == "A"
    assert rows.profile_payload["university_id"] == "u1"
    assert rows.university_name == "Tsinghua"
    assert len(rows.statements) == 1
    assert rows.statements[0]["normalized_text"] == "works on NLP"
    assert len(rows.mentions) == 1
    assert len(rows.topic_links) == 1
    assert rows.topic_links[0]["canonical_name"] == "NLP"
    assert rows.topic_links[0]["review_status"] == "approved"
    assert len(rows.findings) == 1
    assert len(rows.source_urls) == 1
    assert "https://x/p" in rows.source_urls[0]["source_url"]


def test_read_detail_rows_excluded_returns_none(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "excluded", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    assert asyncio.run(reader.read_detail_rows("b1", "e1")) is None


def test_read_detail_rows_missing_returns_none(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    assert asyncio.run(reader.read_detail_rows("b1", "nope")) is None


def test_read_detail_rows_review_entity_returned_for_gating(tmp_path):
    # review entities ARE returned by the reader; the adapter gates on viewer perms
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "review", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_detail_rows("b1", "e1"))
    assert rows is not None
    assert rows.canonical["role_status"] == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py -k read_detail_rows -v`
Expected: FAIL with `AttributeError: 'CatalogSqliteFactReader' object has no attribute 'read_detail_rows'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/dext_recommend/adapters/_catalog_fact_reader.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CatalogProfessorDetailRows:
    canonical: Mapping[str, Any]            # canonical_professors row (dict)
    profile_payload: dict[str, Any]          # parsed professor_profiles.payload_json
    profile_hash: str | None
    university_name: str | None              # from build_source_tasks by university_id
    observations: tuple[Mapping[str, Any], ...]   # active professor_observations rows
    statements: tuple[Mapping[str, Any], ...]
    mentions: tuple[Mapping[str, Any], ...]
    topic_links: tuple[Mapping[str, Any], ...]  # joined with topics.canonical_name
    findings: tuple[Mapping[str, Any], ...]      # unresolved, this build, this entity
    source_urls: tuple[Mapping[str, Any], ...]   # canonical source url rows w/ fetched_at


_CANONICAL_SQL = """
SELECT entity_id, name AS display_name, title_raw, title_family, role_status,
       role_reason_codes, master_eligibility, phd_eligibility,
       research_areas_text AS research_areas, bio, email, phone,
       profile_url, external_url, active, completeness
FROM canonical_professors
WHERE build_id=? AND entity_id=? AND active=1 AND role_status!='excluded'
"""

_PROFILE_SQL = """
SELECT profile_hash, payload_json
FROM professor_profiles
WHERE build_id=? AND entity_id=?
"""

_OBSERVATIONS_SQL = """
SELECT po.id, po.university_id, po.source_url, po.source_document_id,
       po.payload_json AS observation_payload_json
FROM professor_observations po
JOIN entity_observations eo
  ON eo.observation_id=po.id AND eo.build_id=? AND eo.entity_id=?
WHERE po.active=1
ORDER BY po.id
"""

_STATEMENTS_SQL = """
SELECT id, observation_id, normalized_text, language
FROM research_statements
WHERE build_id=? AND entity_id=?
ORDER BY id
"""

_MENTIONS_SQL = """
SELECT id, observation_id, normalized_text, doi, year, confidence, needs_review
FROM publication_mentions
WHERE build_id=? AND entity_id=?
ORDER BY id, observation_id
"""

_TOPIC_LINKS_SQL = """
SELECT l.statement_id, l.topic_id, l.relation_type, l.evidence_span,
       l.review_status, l.provenance_ref, t.canonical_name, t.kind, t.status
FROM statement_topic_links l
JOIN topics t
  ON t.taxonomy_version=l.taxonomy_version AND t.id=l.topic_id
JOIN research_statements s
  ON s.build_id=l.build_id AND s.id=l.statement_id
WHERE s.build_id=? AND s.entity_id=?
  AND t.status='active'
ORDER BY l.statement_id, l.topic_id
"""

_FINDINGS_SQL = """
SELECT id, severity, code, details_json, resolved
FROM quality_findings
WHERE build_id=? AND entity_id=? AND resolved=0
ORDER BY id
"""

_UNIVERSITY_NAME_SQL = """
SELECT university_name FROM build_source_tasks WHERE build_id=? AND university_id=?
"""

_SOURCE_URLS_SQL = """
SELECT DISTINCT po.source_url, po.source_document_id, sd.fetched_at
FROM professor_observations po
JOIN entity_observations eo
  ON eo.observation_id=po.id AND eo.build_id=?
LEFT JOIN source_documents sd ON sd.id=po.source_document_id
WHERE eo.entity_id=? AND po.active=1 AND po.source_url IS NOT NULL
ORDER BY po.source_url
"""
```

Add the method to the `CatalogSqliteFactReader` class:

```python
    async def read_detail_rows(
        self, build_id: str, entity_id: str,
    ) -> CatalogProfessorDetailRows | None:
        def _read() -> CatalogProfessorDetailRows | None:
            with closing(self._connect_ro()) as conn:
                canon = conn.execute(
                    _CANONICAL_SQL, (build_id, entity_id),
                ).fetchone()
                if canon is None:
                    return None
                canon = dict(canon)
                profile_row = conn.execute(
                    _PROFILE_SQL, (build_id, entity_id),
                ).fetchone()
                profile_payload: dict[str, Any] = {}
                profile_hash: str | None = None
                if profile_row is not None:
                    profile_hash = str(profile_row["profile_hash"] or "") or None
                    profile_payload = _safe_json(
                        profile_row["payload_json"], entity_id=entity_id,
                    )
                university_name: str | None = None
                uni_id = profile_payload.get("university_id")
                if uni_id is not None:
                    u = conn.execute(
                        _UNIVERSITY_NAME_SQL, (build_id, str(uni_id)),
                    ).fetchone()
                    if u is not None:
                        university_name = u["university_name"]
                observations = tuple(
                    dict(r) for r in conn.execute(
                        _OBSERVATIONS_SQL, (build_id, entity_id),
                    )
                )
                statements = tuple(
                    dict(r) for r in conn.execute(_STATEMENTS_SQL, (build_id, entity_id))
                )
                mentions = tuple(
                    dict(r) for r in conn.execute(_MENTIONS_SQL, (build_id, entity_id))
                )
                topic_links = tuple(
                    dict(r) for r in conn.execute(_TOPIC_LINKS_SQL, (build_id, entity_id))
                )
                findings = tuple(
                    dict(r) for r in conn.execute(_FINDINGS_SQL, (build_id, entity_id))
                )
                source_urls = tuple(
                    dict(r) for r in conn.execute(_SOURCE_URLS_SQL, (build_id, entity_id))
                )
                return CatalogProfessorDetailRows(
                    canonical=canon,
                    profile_payload=profile_payload,
                    profile_hash=profile_hash,
                    university_name=university_name,
                    observations=observations,
                    statements=statements,
                    mentions=mentions,
                    topic_links=topic_links,
                    findings=findings,
                    source_urls=source_urls,
                )
        return await asyncio.wait_for(asyncio.to_thread(_read), self._timeout)
```

Update `__all__`:

```python
__all__ = [
    "CatalogProfessorDetailRows", "CatalogProfessorFactReader",
    "CatalogSqliteFactReader",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py -k read_detail_rows -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_catalog_fact_reader.py tests/dext_recommend/test_recommend_catalog_fact_reader.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 read_detail_rows single-entity full evidence read"
```

---

## Task 6: Reader — read-only / query_only / thread-offload / timeout invariants

**Files:**
- Test: `tests/dext_recommend/test_recommend_catalog_fact_reader.py`

**Interfaces:**
- Produces: assertion that `_connect_ro` rejects writes; event-loop heartbeat proof that reads do not block. No new production code (verify existing).

- [ ] **Step 1: Write the failing tests — query_only blocks writes; heartbeat stays alive during read**

Add to `tests/dext_recommend/test_recommend_catalog_fact_reader.py`:

```python
import time


def test_fact_reader_connect_ro_blocks_writes(tmp_path):
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=[
            ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
             "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ],
        professor_profiles=[
            ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile_payload("e1"),
             "2026-01-01T00:00:00+00:00"),
        ],
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    conn = reader._connect_ro()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO graph_builds(id,status,curation_version,graph_schema_version,vector_schema_version,settings_json) VALUES('x','ACTIVE','c',1,1,'{}')")
    finally:
        conn.close()


async def test_fact_reader_does_not_block_event_loop(tmp_path):
    # 800 entities; while read_fact_rows runs, a heartbeat task must keep ticking.
    profs = [
        (f"e{i}", "b1", f"N{i}", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5)
        for i in range(800)
    ]
    profiles = [
        ("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10, _profile_payload(f"e{i}"),
         "2026-01-01T00:00:00+00:00")
        for i in range(800)
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=10.0)
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.005)
            ticks += 1

    ids = [f"e{i}" for i in range(800)]
    hb = asyncio.create_task(heartbeat())
    await reader.read_fact_rows("b1", ids)
    await hb
    # if SQLite blocked the loop, ticks would be 0-1; thread offload => many ticks
    assert ticks >= 10


def test_fact_reader_timeout_raises_safe_error(tmp_path):
    # use a path that exists but force a tiny timeout while reading many rows
    profs = [
        (f"e{i}", "b1", f"N{i}", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5)
        for i in range(2000)
    ]
    profiles = [
        ("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10, _profile_payload(f"e{i}"),
         "2026-01-01T00:00:00+00:00")
        for i in range(2000)
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=0.001)
    with pytest.raises((ReadinessSourceError, asyncio.TimeoutError)):
        asyncio.run(reader.read_fact_rows("b1", [f"e{i}" for i in range(2000)]))
```

- [ ] **Step 2: Run tests to confirm current state**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py -k "blocks_writes or not_block_event_loop or timeout_raises" -v`
Expected: The write-block + heartbeat tests PASS (Tasks 3-5 already implemented the invariants). The timeout test may PASS or need the safe-error normalization — if `asyncio.TimeoutError` leaks, fold it into `ReadinessSourceError`.

- [ ] **Step 3 (conditional): If timeout leaks raw TimeoutError, normalize it**

In `src/dext_recommend/adapters/_catalog_fact_reader.py`, wrap `asyncio.wait_for` calls to convert `asyncio.TimeoutError` to `ReadinessSourceError`. Also normalize `sqlite3.Error` from the blocking read functions to a safe `ReadinessSourceError("catalog", "sqlite read failed")`-style reason; do not include SQL text, raw JSON payload, emails, phones, credentials, or full DB paths in those reasons.

```python
# at top of file, add a helper:
async def _guarded_read(coro, timeout: float):
    try:
        return await asyncio.wait_for(coro, timeout)
    except asyncio.TimeoutError as exc:
        raise ReadinessSourceError(
            "catalog", f"read timed out after {timeout}s", retryable=True,
        ) from exc
```

Then replace the `return await asyncio.wait_for(...)` in `check_capability`, `read_fact_rows` (the outer `_read_all`), and `read_detail_rows` with `return await _guarded_read(<inner>, self._timeout)`. (For `read_fact_rows`, wrap the `_read_all()` coroutine: `return await _guarded_read(_read_all(), self._timeout)`.) Re-run the timeout test until PASS.

For `check_capability`, missing `catalog_meta` or malformed schema should still become `ReadinessSourceError` rather than a raw `sqlite3.OperationalError`. Use narrow `except sqlite3.Error as exc` blocks around the connection/query body and raise a sanitized message such as `ReadinessSourceError("catalog", f"sqlite capability check failed: {exc.__class__.__name__}")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_reader.py -k "blocks_writes or not_block_event_loop or timeout_raises" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_catalog_fact_reader.py tests/dext_recommend/test_recommend_catalog_fact_reader.py
git -c commit.gpgsign=false commit -m "test(rec): R4 reader query-only/thread-offload/timeout invariants + safe timeout"
```

---

## Task 7: Pure helpers — `facts/_ids.py` (dedup + chunk)

**Files:**
- Create: `src/dext_recommend/facts/_ids.py`
- Test: `tests/dext_recommend/test_recommend_facts_evidence.py`

**Interfaces:**
- Produces: `dedupe_entity_ids(ids) -> tuple[str, ...]` (stable, order-preserving); `chunk_entity_ids(ids, limit) -> tuple[tuple[str, ...], ...]` (each chunk length <= limit; limit must be >= 1).

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_facts_evidence.py`:

```python
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids


def test_dedupe_entity_ids_preserves_order():
    assert dedupe_entity_ids(["e2", "e1", "e2", "e3", "e1"]) == ("e2", "e1", "e3")


def test_dedupe_entity_ids_empty():
    assert dedupe_entity_ids([]) == ()
    assert dedupe_entity_ids(None) == ()


def test_chunk_entity_ids_below_limit():
    assert chunk_entity_ids(["e1", "e2"], 999) == (("e1", "e2"),)


def test_chunk_entity_ids_splits_at_limit():
    ids = [f"e{i}" for i in range(5)]
    chunks = chunk_entity_ids(ids, 2)
    assert chunks == (("e0", "e1"), ("e2", "e3"), ("e4",))


def test_chunk_entity_ids_empty():
    assert chunk_entity_ids([], 999) == ()


def test_chunk_entity_ids_limit_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        chunk_entity_ids(["e1"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_facts_evidence.py -k "dedupe or chunk" -v`
Expected: FAIL with `ModuleNotFoundError: dext_recommend.facts._ids`

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/facts/_ids.py`:

```python
"""Pure entity-id helpers for R4 fact reads: stable dedup + SQLite-safe chunking."""
from __future__ import annotations

from collections.abc import Iterable


def dedupe_entity_ids(ids: Iterable[str] | None) -> tuple[str, ...]:
    """Order-preserving dedup; None -> ()."""
    if ids is None:
        return ()
    return tuple(dict.fromkeys(str(i) for i in ids))


def chunk_entity_ids(
    ids: Iterable[str], limit: int,
) -> tuple[tuple[str, ...], ...]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    ordered = dedupe_entity_ids(ids)
    return tuple(ordered[i:i + limit] for i in range(0, len(ordered), limit))


__all__ = ["chunk_entity_ids", "dedupe_entity_ids"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_facts_evidence.py -k "dedupe or chunk" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/facts/_ids.py tests/dext_recommend/test_recommend_facts_evidence.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 facts/_ids pure dedup + chunk helpers"
```

---

## Task 8: Pure helpers — `facts/source_urls.py` (canonicalize + dedupe)

**Files:**
- Create: `src/dext_recommend/facts/source_urls.py`
- Test: `tests/dext_recommend/test_recommend_facts_source_urls.py`

**Interfaces:**
- Produces: `canonicalize_source_url(url) -> str | None` (strip tracking params `utm_*`, `fbclid`, `gclid`, `ref`, `from`; drop fragment; lowercase scheme/host; keep path/query order minus stripped params). `None`/empty -> `None`. `dedupe_source_urls(urls) -> tuple[str, ...]` (canonical, stable-order, dedup, drop None).

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_facts_source_urls.py`:

```python
from dext_recommend.facts.source_urls import canonicalize_source_url, dedupe_source_urls


def test_canonicalize_strips_utm_and_fragment():
    raw = "https://x.edu/p?utm_source=foo&id=1#frag"
    assert canonicalize_source_url(raw) == "https://x.edu/p?id=1"


def test_canonicalize_lowercases_scheme_host():
    assert canonicalize_source_url("HTTPS://X.edu/P") == "https://x.edu/P"


def test_canonicalize_none_or_empty():
    assert canonicalize_source_url(None) is None
    assert canonicalize_source_url("") is None


def test_canonicalize_strips_tracking_params_only():
    raw = "https://x.edu/p?fbclid=abc&keep=1&utm_medium=email"
    got = canonicalize_source_url(raw)
    assert "fbclid" not in got and "utm_medium" not in got
    assert "keep=1" in got


def test_dedupe_source_urls_stable_and_canonical():
    urls = [
        "https://x.edu/p?utm_source=foo",
        "https://x.edu/p",
        None,
        "https://y.edu/q",
        "https://x.edu/p#frag",
    ]
    assert dedupe_source_urls(urls) == ("https://x.edu/p", "https://y.edu/q")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_facts_source_urls.py -v`
Expected: FAIL with `ModuleNotFoundError: dext_recommend.facts.source_urls`

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/facts/source_urls.py`:

```python
"""Pure source-URL canonicalization for R4 detail provenance (R4b §2, §4).

Strip tracking params + fragment, lowercase scheme/host, dedupe stably. The
canonical URL is the value surfaced in ProfessorDetail.source_urls; the
original raw URL is never used as a dedup key.
"""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "from",
})


def canonicalize_source_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(str(url))
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, parts.path, query, ""))  # drop fragment


def dedupe_source_urls(urls: Iterable[str | None]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        canon = canonicalize_source_url(raw)
        if canon is None or canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return tuple(out)


__all__ = ["canonicalize_source_url", "dedupe_source_urls"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_facts_source_urls.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/facts/source_urls.py tests/dext_recommend/test_recommend_facts_source_urls.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 facts/source_urls canonical URL normalization"
```

---

## Task 9: Pure helpers — `facts/evidence.py` (snippets + FactItems + weak_explanation)

**Files:**
- Create: `src/dext_recommend/facts/evidence.py`
- Test: `tests/dext_recommend/test_recommend_facts_evidence.py`

**Interfaces:**
- Produces:
  - `select_statement_snippets(statements, *, max_chars=600, max_count=5) -> tuple[str, ...]` — take `normalized_text`, truncate total chars, cap count, in id order.
  - `select_publication_snippets(mentions, *, max_chars=400, max_count=5) -> tuple[str, ...]` — only `needs_review == 0`; id/observation order.
  - `build_fact_items(*, identity, eligibility, research_statements, approved_topics, publications, source_refs_by_field) -> tuple[FactItem, ...]` — assemble `FactItem`s with `ContentClass.FACT` when a `SourceRef` exists, `ContentClass.UNCERTAIN` when missing.
  - `WEAK_EXPLANATION_TEXT` constant — the `weak_explanation` string R4 attaches when semantic score dominates but no citable evidence.
- Consumes: `FactItem`, `ContentClass` from `dext_grounded`; `SourceRef` from `dext_grounded`.

- [ ] **Step 1: Write the failing test**

Append to `tests/dext_recommend/test_recommend_facts_evidence.py`:

```python
from dext_grounded import FactItem, SourceRef
from dext_grounded.content import ContentClass

from dext_recommend.facts.evidence import (
    WEAK_EXPLANATION_TEXT, build_fact_items, select_publication_snippets,
    select_statement_snippets,
)


def _sref(key: str) -> SourceRef:
    return SourceRef(
        doc_path="catalog", heading_path=key, chunk_hash="", quote_or_summary=key,
    )


def test_select_statement_snippets_truncates_and_caps():
    statements = [
        {"id": f"s{i}", "normalized_text": f"statement {i}."} for i in range(10)
    ]
    out = select_statement_snippets(statements, max_chars=20, max_count=3)
    assert len(out) <= 3
    assert sum(len(s) for s in out) <= 20 + 3  # allowance for per-item joins


def test_select_statement_snippets_empty():
    assert select_statement_snippets([]) == ()


def test_select_publication_snippets_excludes_needs_review():
    mentions = [
        {"id": "m1", "observation_id": "o1", "normalized_text": "ok", "needs_review": 0},
        {"id": "m2", "observation_id": "o1", "normalized_text": "review", "needs_review": 1},
    ]
    out = select_publication_snippets(mentions)
    assert out == ("ok",)


def test_build_fact_items_fact_with_source_ref():
    sref = _sref("catalog:entity:e1:build:b1")
    items = build_fact_items(
        identity={"display_name": "A", "entity_id": "e1"},
        eligibility={"master_eligibility": "confirmed", "phd_eligibility": "unknown"},
        research_statements=("works on NLP",),
        approved_topics=("NLP",),
        publications=("Paper A",),
        source_refs_by_field={"display_name": (sref,)},
    )
    by_field = {i.field: i for i in items}
    assert by_field["display_name"].content_class is ContentClass.FACT
    assert by_field["display_name"].source_refs == (sref,)


def test_build_fact_items_uncertain_without_source_ref():
    items = build_fact_items(
        identity={"display_name": "A", "entity_id": "e1"},
        eligibility={"master_eligibility": "confirmed", "phd_eligibility": "unknown"},
        research_statements=("works on NLP",),
        approved_topics=("NLP",),
        publications=("Paper A",),
        source_refs_by_field={},
    )
    for item in items:
        # no source ref => must be uncertain (enforced by FactItem.__post_init__)
        assert item.content_class is ContentClass.UNCERTAIN


def test_weak_explanation_text_is_nonempty():
    assert isinstance(WEAK_EXPLANATION_TEXT, str) and WEAK_EXPLANATION_TEXT.strip()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_facts_evidence.py -k "select_statement or select_publication or build_fact or weak_explanation" -v`
Expected: FAIL with `ModuleNotFoundError: dext_recommend.facts.evidence`

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/facts/evidence.py`:

```python
"""Pure evidence/snippet/FactItem assembly for R4 (R4b §4, grounded §3).

No I/O. Snippets are truncated + capped; FactItems are FACT when a SourceRef
exists and UNCERTAIN otherwise (FactItem.__post_init__ enforces the invariant).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from dext_grounded import FactItem, SourceRef
from dext_grounded.content import ContentClass

WEAK_EXPLANATION_TEXT = (
    "候选排序主要由语义相似度贡献，但当前缺少可回溯的详情证据。"
)


def select_statement_snippets(
    statements: Sequence[Mapping], *, max_chars: int = 600, max_count: int = 5,
) -> tuple[str, ...]:
    out: list[str] = []
    total = 0
    for row in statements:
        if len(out) >= max_count:
            break
        text = str(row.get("normalized_text") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars and out:
            break
        out.append(text)
        total += len(text)
    return tuple(out)


def select_publication_snippets(
    mentions: Sequence[Mapping], *, max_chars: int = 400, max_count: int = 5,
) -> tuple[str, ...]:
    out: list[str] = []
    total = 0
    for row in mentions:
        if len(out) >= max_count:
            break
        if int(row.get("needs_review") or 0) != 0:
            continue
        text = str(row.get("normalized_text") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars and out:
            break
        out.append(text)
        total += len(text)
    return tuple(out)


def build_fact_items(
    *,
    identity: Mapping[str, str],
    eligibility: Mapping[str, str],
    research_statements: Sequence[str],
    approved_topics: Sequence[str],
    publications: Sequence[str],
    source_refs_by_field: Mapping[str, Sequence[SourceRef]],
) -> tuple[FactItem, ...]:
    refs = source_refs_by_field or {}
    items: list[FactItem] = []

    def _add(field: str, value: str) -> None:
        sr = tuple(refs.get(field, ()))
        items.append(FactItem(
            field=field, value=value,
            content_class=ContentClass.FACT if sr else ContentClass.UNCERTAIN,
            source_refs=sr,
        ))

    _add("display_name", str(identity.get("display_name") or ""))
    _add("master_eligibility", str(eligibility.get("master_eligibility") or ""))
    _add("phd_eligibility", str(eligibility.get("phd_eligibility") or ""))
    if research_statements:
        _add("research_statement", "; ".join(research_statements))
    if approved_topics:
        _add("approved_topics", "; ".join(approved_topics))
    if publications:
        _add("publications", "; ".join(publications))
    return tuple(items)


__all__ = [
    "WEAK_EXPLANATION_TEXT",
    "build_fact_items",
    "select_publication_snippets",
    "select_statement_snippets",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_facts_evidence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/facts/evidence.py tests/dext_recommend/test_recommend_facts_evidence.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 facts/evidence pure snippet + FactItem assembly"
```

---

## Task 10: Port updates — `ProfessorFactNotFound` + `ProfessorDetail.fact_bundle`

**Files:**
- Modify: `src/dext_recommend/ports/professor_facts.py`
- Modify: `src/dext_recommend/ports/__init__.py`
- Test: `tests/dext_recommend/test_recommend_models.py` (add), `tests/dext_recommend/test_recommend_fake_ports.py` (verify fake stays compatible)

**Interfaces:**
- Produces: `ProfessorFactNotFound(LookupError)`; `ProfessorDetail` gains `fact_bundle: FactBundle` (required, no default). Re-exports `FactBundle`, `FactItem`, `ContentClass` from `ports/professor_facts.py`; re-exports `ProfessorFactNotFound` from `ports/__init__.py`; `__all__` updated in both files.
- Consumes: `FactBundle`, `FactItem` from `dext_grounded.fact_bundle`; `ContentClass` from `dext_grounded.content`; `SourceRef` from `dext_grounded`.

- [ ] **Step 1: Write the failing test — ProfessorDetail requires fact_bundle; ProfessorFactNotFound is a LookupError**

Add to `tests/dext_recommend/test_recommend_models.py`:

```python
def test_professor_fact_not_found_is_lookup_error():
    from dext_recommend.ports.professor_facts import ProfessorFactNotFound
    assert issubclass(ProfessorFactNotFound, LookupError)


def test_professor_detail_requires_fact_bundle():
    from dext_grounded import FactBundle, FactItem
    from dext_grounded.content import ContentClass
    from dext_recommend.ports.professor_facts import ProfessorDetail

    bundle = FactBundle(
        build_id="b1", subject_id="e1",
        facts=(FactItem(field="display_name", value="A",
                        content_class=ContentClass.UNCERTAIN),),
        source_refs=(),
    )
    d = ProfessorDetail(
        build_id="b1", profile_hash=None, entity_id="e1", display_name="A",
        university="U", org_units=(), title="Prof.", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="unknown",
        role_status="included", profile_url=None,
        research_statements=(), approved_topics=(),
        selected_publication_mentions=(), bio_snippets=(),
        source_urls=(), provenance_refs=(),
        quality_findings=(), risk_flags=(),
        fact_bundle=bundle,
    )
    assert d.fact_bundle is bundle
    assert d.fact_bundle.build_id == "b1"
    assert d.fact_bundle.subject_id == "e1"


def test_professor_detail_fact_bundle_is_required():
    from dext_recommend.ports.professor_facts import ProfessorDetail
    import pytest
    with pytest.raises(TypeError):
        ProfessorDetail(
            build_id="b1", profile_hash=None, entity_id="e1", display_name="A",
            university="U", org_units=(), title="Prof.", title_family="professor",
            master_eligibility="confirmed", phd_eligibility="unknown",
            role_status="included", profile_url=None,
            research_statements=(), approved_topics=(),
            selected_publication_mentions=(), bio_snippets=(),
            source_urls=(), provenance_refs=(),
            quality_findings=(), risk_flags=(),
            # fact_bundle omitted
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_models.py::test_professor_fact_not_found_is_lookup_error tests/dext_recommend/test_recommend_models.py::test_professor_detail_requires_fact_bundle tests/dext_recommend/test_recommend_models.py::test_professor_detail_fact_bundle_is_required -v`
Expected: FAIL with `AttributeError: ProfessorDetail has no attribute 'fact_bundle'` / `ImportError` for `ProfessorFactNotFound`.

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/ports/professor_facts.py`, add imports + the exception + the field. The final file becomes:

```python
"""ProfessorFactPort — assemble ProfessorDetail / hydrate facts (R4)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dext_grounded import SourceRef
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem

from dext_recommend._immutable import freeze_mapping
from dext_recommend.readiness import ActiveBuildSnapshot


class ProfessorFactNotFound(LookupError):
    """Raised by get_detail when the entity is missing/inactive/excluded."""
    def __init__(self, entity_id: str, build_id: str) -> None:
        self.entity_id = entity_id
        self.build_id = build_id
        super().__init__(f"professor fact not found: entity={entity_id} build={build_id}")


@dataclass(frozen=True, slots=True)
class ViewerPermissions:
    include_contacts: bool = False
    can_view_review: bool = False
    diagnostics: bool = False


@dataclass(frozen=True, slots=True)
class ProfessorFact:
    entity_id: str
    display_name: str
    university: str
    org_units: tuple[str, ...]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    profile_hash: str | None
    research_summary: str | None
    # authority fields for hard filters (R3); display fields above are for cards only
    university_id: str | None = None
    city_name: str | None = None
    org_unit_ids: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "org_units", tuple(self.org_units or ()))
        object.__setattr__(self, "org_unit_ids", tuple(self.org_unit_ids or ()))
        object.__setattr__(self, "topic_ids", tuple(self.topic_ids or ()))


@dataclass(frozen=True, slots=True)
class ProfessorDetail:
    build_id: str
    profile_hash: str | None
    entity_id: str
    display_name: str
    university: str
    org_units: tuple[str, ...]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_statements: tuple[str, ...]
    approved_topics: tuple[str, ...]
    selected_publication_mentions: tuple[str, ...]
    bio_snippets: tuple[str, ...]
    source_urls: tuple[str, ...]
    provenance_refs: tuple[SourceRef, ...]
    quality_findings: tuple[str, ...]
    risk_flags: tuple[str, ...]
    fact_bundle: FactBundle                          # R6 sole fact input, required
    contacts: Mapping[str, str] = field(default_factory=dict)   # gated by viewer perms

    def __post_init__(self) -> None:
        for name in (
            "org_units",
            "research_statements",
            "approved_topics",
            "selected_publication_mentions",
            "bio_snippets",
            "source_urls",
            "provenance_refs",
            "quality_findings",
            "risk_flags",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        object.__setattr__(self, "contacts", freeze_mapping(self.contacts))


@runtime_checkable
class ProfessorFactPort(Protocol):
    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail: ...

    async def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]: ...


__all__ = [
    "ContentClass", "FactBundle", "FactItem", "ProfessorDetail",
    "ProfessorFact", "ProfessorFactNotFound", "ProfessorFactPort",
    "ViewerPermissions",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_models.py::test_professor_fact_not_found_is_lookup_error tests/dext_recommend/test_recommend_models.py::test_professor_detail_requires_fact_bundle tests/dext_recommend/test_recommend_models.py::test_professor_detail_fact_bundle_is_required -v`
Expected: PASS

- [ ] **Step 5: Re-export `ProfessorFactNotFound` from ports package**

In `src/dext_recommend/ports/__init__.py`, add `ProfessorFactNotFound` to the `from dext_recommend.ports.professor_facts import (...)` block and to `__all__`. Keep existing exports intact.

- [ ] **Step 6: Fix existing ProfessorDetail constructions to pass fact_bundle**

`ProfessorDetail.fact_bundle` is now required. Update these 7 existing construction sites to pass a minimal `FactBundle` (they live in the fake/test layer and don't need real provenance):

- `tests/dext_recommend/_recfixtures.py:167` — `professor_details_case._detail`
- `tests/dext_recommend/test_recommend_core.py:284, 558, 651`
- `tests/dext_recommend/test_recommend_fake_ports.py:71`
- `tests/dext_recommend/test_recommend_rerank.py:201, 211`

For each, add a helper near the top of the file (or reuse if one exists):

```python
from dext_grounded import FactBundle, FactItem
from dext_grounded.content import ContentClass

def _empty_fact_bundle(*, build_id: str, entity_id: str) -> FactBundle:
    return FactBundle(
        build_id=build_id, subject_id=entity_id,
        facts=(FactItem(field="display_name", value=entity_id,
                        content_class=ContentClass.UNCERTAIN),),
        source_refs=(),
    )
```

Then pass `fact_bundle=_empty_fact_bundle(build_id=<the build_id used>, entity_id=<the entity_id used>)` to each `ProfessorDetail(...)`. For `_recfixtures.py:167` that's `build_id="b-1", entity_id=eid`; match the `build_id`/`entity_id` literals at each call site.

Run: `uv run pytest tests/dext_recommend/test_recommend_fake_ports.py tests/dext_recommend/test_recommend_core.py tests/dext_recommend/test_recommend_rerank.py -q`
Expected: PASS (no `TypeError: missing required argument: 'fact_bundle'`).

- [ ] **Step 7: Commit**

```bash
git add src/dext_recommend/ports/professor_facts.py src/dext_recommend/ports/__init__.py tests/dext_recommend/test_recommend_models.py tests/dext_recommend/test_recommend_fake_ports.py tests/dext_recommend/test_recommend_core.py tests/dext_recommend/test_recommend_rerank.py tests/dext_recommend/_recfixtures.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 ProfessorFactNotFound + ProfessorDetail.fact_bundle required field"
```

---

## Task 11: Adapter — `hydrate` (batch ProfessorFact from reader rows)

**Files:**
- Create: `src/dext_recommend/adapters/catalog_professor_facts.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_adapter.py`

**Interfaces:**
- Produces: `CatalogProfessorFactAdapter(reader: CatalogProfessorFactReader, *, settings: RecommendSettings)` implementing `ProfessorFactPort`. `async hydrate(snapshot, entity_ids) -> dict[str, ProfessorFact]` — dedups IDs, chunks, calls `reader.read_fact_rows(snapshot.build_id, chunk)`, maps rows to `ProfessorFact` (display fields + authority fields), returns `{entity_id: ProfessorFact}` for rows present (missing/inactive/excluded silently absent). Build pin: every call uses `snapshot.build_id`. Errors from the reader propagate as `ReadinessSourceError`.
- Consumes: `CatalogProfessorFactReader.read_fact_rows`; `dedupe_entity_ids`, `chunk_entity_ids`; `RecommendSettings.fact_chunk_size`, `RecommendSettings.fact_read_timeout`.

- [ ] **Step 1: Write the failing test — hydrate maps authority fields + silently drops missing**

Create `tests/dext_recommend/test_recommend_catalog_fact_adapter.py`:

```python
import asyncio

from dext_recommend.adapters._catalog_fact_reader import CatalogSqliteFactReader
from dext_recommend.adapters.catalog_professor_facts import CatalogProfessorFactAdapter
from dext_recommend.config import RecommendSettings
from dext_recommend.readiness import ActiveBuildSnapshot
from datetime import datetime, timezone

from tests.dext_recommend._factfixtures import build_catalog_db, dumps


def _active_build():
    return ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}")


def _snapshot(build_id="b1"):
    return ActiveBuildSnapshot(
        build_id=build_id, catalog_schema_version=6, neo4j_active_build_id="b1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=6,
        embedding_provider="openai", embedding_model="m", embedding_dimension=1536,
        embedding_fingerprint="fp", taxonomy_version="tax-v1",
        ranking_profile_version="ranking-v1", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _profile(entity_id, **kw):
    base = {"university_id": "u1", "org_unit_ids": ["ou_cs"], "city": None,
            "topic_ids": [], "profile_hash": f"h_{entity_id}"}
    base.update(kw)
    return dumps(base)


def test_hydrate_returns_facts_with_authority_fields(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e1", None, 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10,
         _profile("e1", university_id="u_tsinghua", org_unit_ids=["ou_cs", "ou_ai"],
                  city="Beijing", topic_ids=["t1", "t2"]),
         "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot(), ["e1", "e_missing"]))
    assert set(out) == {"e1"}
    f = out["e1"]
    assert f.entity_id == "e1"
    assert f.display_name == "A"
    assert f.university_id == "u_tsinghua"
    assert f.city_name == "Beijing"
    assert f.org_unit_ids == ("ou_cs", "ou_ai")
    assert f.topic_ids == ("t1", "t2")
    assert f.master_eligibility == "confirmed"
    assert f.role_status == "included"
    assert f.profile_hash == "h1"


def test_hydrate_silently_drops_excluded_and_inactive(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ("e2", "b1", "B", "Prof.", "professor", "excluded", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ("e3", "b1", "C", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 0, 0.5),
    ]
    profiles = [
        ("b1", eid, f"h_{eid}", "tv", "ti", "np", 10, _profile(eid),
         "2026-01-01T00:00:00+00:00")
        for eid in ("e1", "e2", "e3")
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot(), ["e1", "e2", "e3", "e_missing"]))
    assert set(out) == {"e1"}


def test_hydrate_dedups_ids(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile("e1"), "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot(), ["e1", "e1", "e1"]))
    assert set(out) == {"e1"}


def test_hydrate_empty_ids_returns_empty(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    assert asyncio.run(adapter.hydrate(_snapshot(), [])) == {}


def test_hydrate_pins_caller_build_id(tmp_path):
    # build b2 with a different professor; hydrate(b1) must not see b2's rows
    profs_b2 = [
        ("e1", "b2", "A2", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    profiles_b2 = [
        ("b2", "e1", "h_b2", "tv", "ti", "np", 10, _profile("e1"), "2026-01-01T00:00:00+00:00"),
    ]
    builds = [
        ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}"),
        ("b2", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=builds,
        canonical_professors=profs_b2, professor_profiles=profiles_b2,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot("b1"), ["e1"]))
    assert out == {}  # e1 only exists under b2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_adapter.py -k hydrate -v`
Expected: FAIL with `ModuleNotFoundError: dext_recommend.adapters.catalog_professor_facts`

- [ ] **Step 3: Write minimal implementation**

Create `src/dext_recommend/adapters/catalog_professor_facts.py`:

```python
"""CatalogProfessorFactAdapter: implements ProfessorFactPort over the
published catalog SQLite (R4). Two methods: hydrate (batch authority facts)
and get_detail (single-entity full ProfessorDetail + FactBundle).

Never imports dext_graph; reads only the pinned build_id. Contacts are
double-gated and never enter the FactBundle. No cache (R7 decorator if ever).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dext_grounded import SourceRef
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.content import ContentClass

from dext_recommend.adapters._catalog_fact_reader import (
    CatalogProfessorFactReader, CatalogProfessorDetailRows,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids
from dext_recommend.facts.evidence import (
    build_fact_items, select_publication_snippets, select_statement_snippets,
)
from dext_recommend.facts.source_urls import dedupe_source_urls
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactNotFound, ViewerPermissions,
)
from dext_recommend.readiness import ActiveBuildSnapshot


def _fact_from_row(row: dict[str, Any]) -> ProfessorFact:
    return ProfessorFact(
        entity_id=str(row["entity_id"]),
        display_name=str(row["display_name"] or ""),
        university=str(row.get("university_id") or ""),
        org_units=tuple(row.get("org_unit_ids") or ()),
        title=str(row.get("title_family") or ""),
        title_family=str(row.get("title_family") or ""),
        master_eligibility=str(row.get("master_eligibility") or "unknown"),
        phd_eligibility=str(row.get("phd_eligibility") or "unknown"),
        role_status=str(row.get("role_status") or "included"),
        profile_url=row.get("profile_url"),
        profile_hash=row.get("profile_hash"),
        research_summary=None,
        university_id=row.get("university_id"),
        city_name=row.get("city_name"),
        org_unit_ids=tuple(row.get("org_unit_ids") or ()),
        topic_ids=tuple(row.get("topic_ids") or ()),
    )


class CatalogProfessorFactAdapter:
    def __init__(
        self, reader: CatalogProfessorFactReader, *, settings: RecommendSettings,
    ) -> None:
        self._reader = reader
        self._settings = settings

    async def hydrate(
        self, snapshot: ActiveBuildSnapshot, entity_ids: list[str],
    ) -> dict[str, ProfessorFact]:
        if not entity_ids:
            return {}
        ordered = dedupe_entity_ids(entity_ids)
        chunks = chunk_entity_ids(ordered, self._settings.fact_chunk_size)
        out: dict[str, ProfessorFact] = {}
        for chunk in chunks:
            rows = await self._reader.read_fact_rows(snapshot.build_id, list(chunk))
            for row in rows:
                fact = _fact_from_row(dict(row))
                out[fact.entity_id] = fact
        return out

    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        raise NotImplementedError  # filled by Task 12


__all__ = ["CatalogProfessorFactAdapter"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_adapter.py -k hydrate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/catalog_professor_facts.py tests/dext_recommend/test_recommend_catalog_fact_adapter.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 CatalogProfessorFactAdapter.hydrate batch authority facts"
```

---

## Task 12: Adapter — `get_detail` (ProfessorDetail + FactBundle invariants)

**Files:**
- Modify: `src/dext_recommend/adapters/catalog_professor_facts.py`
- Test: `tests/dext_recommend/test_recommend_catalog_fact_adapter.py`

**Interfaces:**
- Produces: `async get_detail(snapshot, entity_id, include_contacts, viewer_permissions) -> ProfessorDetail`. Reader returns `None` -> raise `ProfessorFactNotFound`. Review entity + `viewer_permissions.can_view_review=False` -> raise `ProfessorFactNotFound`. Contacts: `include_contacts AND viewer_permissions.include_contacts` -> fill `contacts` from canonical email/phone, else `{}`. `profile_hash=None` -> append `profile_hash_missing` to `risk_flags`. FactBundle: `build_id=snapshot.build_id`, `subject_id=entity_id`, `facts` from `build_fact_items`, `source_refs` == `detail.provenance_refs`. Every `FactItem` has a matching `SourceRef` or is `ContentClass.UNCERTAIN`.

- [ ] **Step 1: Write the failing test — get_detail assembles full detail + FactBundle invariants**

Append to `tests/dext_recommend/test_recommend_catalog_fact_adapter.py`:

```python
import pytest

from dext_grounded import FactBundle, SourceRef

from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFactNotFound, ViewerPermissions,
)

from tests.dext_recommend._factfixtures import dumps


def _full_entity_db(tmp_path, *, role_status="included", profile_hash="h1",
                    email="e@x", phone="123"):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", role_status, "[]",
         "confirmed", "unknown", "areas", "bio1", email, phone,
         "https://x/p", "https://x/e", 1, 0.9),
    ]
    payload = dumps({
        "university_id": "u1", "org_unit_ids": ["ou_cs"], "city": "Beijing",
        "topic_ids": [], "profile_hash": profile_hash,
        "provenance_ref": "catalog:entity:e1:build:b1",
    })
    profiles = [
        ("b1", "e1", profile_hash or "", "tv", "ti", "np", 10, payload,
         "2026-01-01T00:00:00+00:00"),
    ]
    observations = [
        ("obs1", "u1", "snap1", "https://x/p?utm_source=foo", "single_profile",
         dumps({"affiliations": [{"org_unit_name": "Dept CS"}]}),
         "rh1", "direct", "b1", "b1", 1),
    ]
    entity_observations = [("e1", "obs1", "strong", "b1")]
    statements = [
        ("s1", "b1", "e1", "obs1", "raw", "works on NLP", "en", "sh1"),
    ]
    mentions = [
        ("m1", "b1", "e1", "obs1", "raw", "Paper A", 2024, 0.9, 0),
    ]
    topics = [("tax-v1", "t1", "NLP", "nlp", "method", "active", "llm")]
    topic_links = [
        ("b1", "s1", "tax-v1", "t1", "PRIMARY_TOPIC", "span", "llm", 0.95,
         "approved", "catalog:research-statement:b1:s1"),
    ]
    findings = [
        ("f1", "b1", "warning", "incomplete_profile", "e1", None,
         dumps({"note": "x"}), 0),
    ]
    source_docs = [
        ("doc1", "u1", "https://x/p", "ch1", "2026-01-01T00:00:00+00:00", "b1", "b1"),
    ]
    source_tasks = [
        ("b1", "u1", "Tsinghua", "tsinghua", "/src", 0, "COMPLETED",
         "2026-01-01T00:00:00+00:00"),
    ]
    return build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
        professor_observations=observations, entity_observations=entity_observations,
        research_statements=statements, publication_mentions=mentions,
        topics=topics, statement_topic_links=topic_links,
        quality_findings=findings, source_documents=source_docs,
        build_source_tasks=source_tasks,
        entities=[("e1", "professor", "active", None,
                   "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")],
    )


def test_get_detail_assembles_full_professor_detail(tmp_path):
    path = _full_entity_db(tmp_path)
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    detail = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(),
    ))
    assert detail.build_id == "b1"
    assert detail.entity_id == "e1"
    assert detail.display_name == "A"
    assert detail.university == "Tsinghua"
    assert detail.org_units == ("Dept CS",)  # from observation affiliations
    assert detail.research_statements == ("works on NLP",)
    assert detail.approved_topics == ("NLP",)
    assert detail.selected_publication_mentions == ("Paper A",)
    # source URL canonicalized (utm stripped)
    assert detail.source_urls == ("https://x/p",)
    assert detail.quality_findings == ("incomplete_profile",)
    # contacts gated off
    assert dict(detail.contacts) == {}


def test_get_detail_fact_bundle_invariants(tmp_path):
    path = _full_entity_db(tmp_path)
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    detail = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(),
    ))
    assert isinstance(detail.fact_bundle, FactBundle)
    assert detail.fact_bundle.build_id == detail.build_id == "b1"
    assert detail.fact_bundle.subject_id == detail.entity_id == "e1"
    assert tuple(detail.fact_bundle.source_refs) == tuple(detail.provenance_refs)
    # every FactItem has a source_ref or is UNCERTAIN
    from dext_grounded.content import ContentClass
    for item in detail.fact_bundle.facts:
        if not item.source_refs:
            assert item.content_class is ContentClass.UNCERTAIN


def test_get_detail_contacts_double_gated(tmp_path):
    path = _full_entity_db(tmp_path, email="prof@x", phone="555")
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    # both flags True -> contacts present
    d1 = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=True,
        viewer_permissions=ViewerPermissions(include_contacts=True),
    ))
    assert d1.contacts["email"] == "prof@x"
    assert d1.contacts["phone"] == "555"
    # request asks but viewer lacks permission -> empty
    d2 = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=True,
        viewer_permissions=ViewerPermissions(include_contacts=False),
    ))
    assert dict(d2.contacts) == {}
    # viewer permits but request doesn't ask -> empty
    d3 = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(include_contacts=True),
    ))
    assert dict(d3.contacts) == {}
    # contacts never in fact_bundle
    for d in (d1, d2, d3):
        for item in d.fact_bundle.facts:
            assert item.field not in ("email", "phone")
        for ref in d.fact_bundle.source_refs:
            assert "@" not in ref.quote_or_summary


def test_get_detail_missing_raises_professor_fact_not_found(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    with pytest.raises(ProfessorFactNotFound):
        asyncio.run(adapter.get_detail(
            _snapshot(), "nope", include_contacts=False,
            viewer_permissions=ViewerPermissions(),
        ))


def test_get_detail_excluded_raises_not_found(tmp_path):
    path = _full_entity_db(tmp_path, role_status="excluded")
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    with pytest.raises(ProfessorFactNotFound):
        asyncio.run(adapter.get_detail(
            _snapshot(), "e1", include_contacts=False,
            viewer_permissions=ViewerPermissions(),
        ))


def test_get_detail_review_gated_by_viewer_permissions(tmp_path):
    path = _full_entity_db(tmp_path, role_status="review")
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    # viewer cannot see review -> not found
    with pytest.raises(ProfessorFactNotFound):
        asyncio.run(adapter.get_detail(
            _snapshot(), "e1", include_contacts=False,
            viewer_permissions=ViewerPermissions(can_view_review=False),
        ))
    # viewer can see review -> returned
    d = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(can_view_review=True),
    ))
    assert d.role_status == "review"


def test_get_detail_profile_hash_missing_adds_risk(tmp_path):
    path = _full_entity_db(tmp_path, profile_hash=None)
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    d = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(),
    ))
    assert d.profile_hash is None
    assert "profile_hash_missing" in d.risk_flags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_adapter.py -k get_detail -v`
Expected: FAIL with `NotImplementedError` (get_detail stub).

- [ ] **Step 3: Write minimal implementation**

Replace the `get_detail` stub in `src/dext_recommend/adapters/catalog_professor_facts.py`:

```python
import json


def _org_unit_names(observations: tuple[Mapping, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for obs in observations:
        try:
            payload = json.loads(obs.get("observation_payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
        affiliations = payload.get("affiliations") if isinstance(payload, dict) else None
        if isinstance(affiliations, list):
            for item in affiliations:
                if isinstance(item, dict) and item.get("org_unit_name"):
                    name = str(item["org_unit_name"])
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
        elif isinstance(payload, dict) and payload.get("org_unit_name"):
            name = str(payload["org_unit_name"])
            if name not in seen:
                seen.add(name)
                out.append(name)
    return tuple(out)


def _approved_topic_names(topic_links: tuple[Mapping, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for row in topic_links:
        if str(row.get("review_status")) != "approved":
            continue
        name = str(row.get("canonical_name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _finding_codes(findings: tuple[Mapping, ...]) -> tuple[str, ...]:
    return tuple(str(r.get("code")) for r in findings if r.get("code"))


def _source_ref(kind: str, build_id: str, key: str, *, summary: str = "") -> SourceRef:
    return SourceRef(
        doc_path="catalog",
        heading_path=f"catalog:{kind}:{build_id}:{key}",
        chunk_hash=f"{build_id}:{key}",
        quote_or_summary=summary or f"catalog:{kind}:{build_id}:{key}",
    )


class CatalogProfessorFactAdapter:
    # ... __init__ and hydrate unchanged ...

    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        rows = await self._reader.read_detail_rows(snapshot.build_id, entity_id)
        if rows is None:
            raise ProfessorFactNotFound(entity_id, snapshot.build_id)
        canon = dict(rows.canonical)
        role_status = str(canon.get("role_status") or "included")
        if role_status == "review" and not viewer_permissions.can_view_review:
            raise ProfessorFactNotFound(entity_id, snapshot.build_id)

        build_id = snapshot.build_id
        org_units = _org_unit_names(rows.observations)
        statements = select_statement_snippets(rows.statements)
        mentions = select_publication_snippets(rows.mentions)
        topics = _approved_topic_names(rows.topic_links)
        findings = _finding_codes(rows.findings)
        source_urls = dedupe_source_urls(
            str(r.get("source_url")) for r in rows.source_urls
        )
        profile_hash = rows.profile_hash

        # provenance refs (R3-compatible projection)
        provenance_refs: list[SourceRef] = [
            _source_ref("entity", build_id, entity_id,
                        summary=str(rows.profile_payload.get("provenance_ref") or "")),
        ]
        for s in rows.statements:
            provenance_refs.append(
                _source_ref("research-statement", build_id, str(s["id"]),
                            summary=str(s.get("normalized_text") or ""))
            )
        for m in rows.mentions:
            provenance_refs.append(
                _source_ref("publication-mention", build_id, str(m["id"]),
                            summary=str(m.get("normalized_text") or ""))
            )
        for tl in rows.topic_links:
            if str(tl.get("review_status")) == "approved":
                provenance_refs.append(
                    SourceRef(
                        doc_path="catalog",
                        heading_path=str(tl.get("provenance_ref") or ""),
                        chunk_hash=f"{build_id}:{tl['statement_id']}:{tl['topic_id']}",
                        quote_or_summary=str(tl.get("evidence_span") or ""),
                    )
                )
        provenance_refs_tuple = tuple(provenance_refs)

        # FactItems: identity/eligibility/statement/topics/publications, each
        # tied to its SourceRef when one exists, else UNCERTAIN.
        refs_by_field = {
            "display_name": (provenance_refs_tuple[0],) if provenance_refs_tuple else (),
            "master_eligibility": (provenance_refs_tuple[0],) if provenance_refs_tuple else (),
            "phd_eligibility": (provenance_refs_tuple[0],) if provenance_refs_tuple else (),
            "research_statement": tuple(
                r for r in provenance_refs_tuple
                if r.heading_path.startswith("catalog:research-statement:")
            ),
            "approved_topics": tuple(
                r for r in provenance_refs_tuple
                if "research-statement" in r.heading_path
            ),
            "publications": tuple(
                r for r in provenance_refs_tuple
                if r.heading_path.startswith("catalog:publication-mention:")
            ),
        }
        fact_items = build_fact_items(
            identity={"display_name": str(canon.get("display_name") or ""),
                      "entity_id": entity_id},
            eligibility={"master_eligibility": str(canon.get("master_eligibility") or ""),
                         "phd_eligibility": str(canon.get("phd_eligibility") or "")},
            research_statements=statements,
            approved_topics=topics,
            publications=mentions,
            source_refs_by_field=refs_by_field,
        )
        fact_bundle = FactBundle(
            build_id=build_id,
            subject_id=entity_id,
            facts=fact_items,
            source_refs=provenance_refs_tuple,
        )

        # contacts: double gate
        contacts: dict[str, str] = {}
        if include_contacts and viewer_permissions.include_contacts:
            email = canon.get("email")
            phone = canon.get("phone")
            if email:
                contacts["email"] = str(email)
            if phone:
                contacts["phone"] = str(phone)

        risk_flags: list[str] = []
        if profile_hash is None:
            risk_flags.append("profile_hash_missing")

        return ProfessorDetail(
            build_id=build_id,
            profile_hash=profile_hash,
            entity_id=entity_id,
            display_name=str(canon.get("display_name") or ""),
            university=str(rows.university_name or rows.profile_payload.get("university_id") or ""),
            org_units=org_units,
            title=str(canon.get("title_raw") or canon.get("title_family") or ""),
            title_family=str(canon.get("title_family") or ""),
            master_eligibility=str(canon.get("master_eligibility") or "unknown"),
            phd_eligibility=str(canon.get("phd_eligibility") or "unknown"),
            role_status=role_status,
            profile_url=canon.get("profile_url"),
            research_statements=statements,
            approved_topics=topics,
            selected_publication_mentions=mentions,
            bio_snippets=(str(canon.get("bio") or ""),) if canon.get("bio") else (),
            source_urls=source_urls,
            provenance_refs=provenance_refs_tuple,
            quality_findings=findings,
            risk_flags=tuple(risk_flags),
            fact_bundle=fact_bundle,
            contacts=contacts,
        )
```

Add `from collections.abc import Mapping` to the imports at the top.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_adapter.py -k get_detail -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/catalog_professor_facts.py tests/dext_recommend/test_recommend_catalog_fact_adapter.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 CatalogProfessorFactAdapter.get_detail + FactBundle invariants + double-gated contacts"
```

---

## Task 13: Snapshot-switch concurrency — one adapter never mixes builds

**Files:**
- Test: `tests/dext_recommend/test_recommend_catalog_fact_adapter.py`

**Interfaces:**
- Produces: proof that concurrent `hydrate` calls under different snapshots return rows only for their own `build_id`. No new production code (verify Tasks 11–12 pin `snapshot.build_id`).

- [ ] **Step 1: Write the failing test — concurrent hydrate under two builds never cross-contaminates**

Append to `tests/dext_recommend/test_recommend_catalog_fact_adapter.py`:

```python
async def test_concurrent_hydrate_across_builds_does_not_mix(tmp_path):
    # e1 under b1, e2 under b2 (different entity ids to isolate)
    builds = [
        ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}"),
        ("b2", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}"),
    ]
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ("e2", "b2", "B", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile("e1"), "2026-01-01T00:00:00+00:00"),
        ("b2", "e2", "h2", "tv", "ti", "np", 10, _profile("e2"), "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=builds,
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    s1, s2 = _snapshot("b1"), _snapshot("b2")
    out1, out2 = await asyncio.gather(
        adapter.hydrate(s1, ["e1", "e2"]),
        adapter.hydrate(s2, ["e1", "e2"]),
    )
    assert set(out1) == {"e1"}  # b1 only has e1
    assert set(out2) == {"e2"}  # b2 only has e2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_fact_adapter.py::test_concurrent_hydrate_across_builds_does_not_mix -v`
Expected: PASS (the adapter forwards `snapshot.build_id` to every reader call; no shared mutable state).

- [ ] **Step 3: Commit**

```bash
git add tests/dext_recommend/test_recommend_catalog_fact_adapter.py
git -c commit.gpgsign=false commit -m "test(rec): R4 snapshot-switch concurrency — adapter never mixes builds"
```

---

## Task 14: Re-exports, config knobs, import-boundary test

**Files:**
- Modify: `src/dext_recommend/adapters/__init__.py`
- Modify: `src/dext_recommend/__init__.py`
- Modify: `src/dext_recommend/config.py`
- Modify: `src/dext_recommend/facts/__init__.py`
- Modify: `tests/dext_recommend/test_recommend_import_boundary.py`

**Interfaces:**
- Produces: public re-exports `CatalogProfessorFactAdapter`, `CatalogSqliteFactReader`, `CatalogProfessorFactReader`, `ProfessorFactNotFound` at package root; `fact_read_timeout`/`fact_chunk_size` on `RecommendSettings`; import-boundary test covers all new R4 submodules.

- [ ] **Step 1: Write the failing test — new submodules importable and dext_graph-free; config knobs present**

Add to `tests/dext_recommend/test_recommend_import_boundary.py`:

```python
def test_dext_recommend_fact_submodules_importable_without_dext_family():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        for sub in (
            "dext_recommend.facts._ids",
            "dext_recommend.facts.evidence",
            "dext_recommend.facts.source_urls",
            "dext_recommend.adapters._catalog_fact_schema",
            "dext_recommend.adapters._catalog_fact_reader",
            "dext_recommend.adapters.catalog_professor_facts",
        ):
            importlib.import_module(sub)
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_catalog_fact_adapter_reexported_at_root():
    import dext_recommend as dr
    assert hasattr(dr, "CatalogProfessorFactAdapter")
    assert hasattr(dr, "CatalogSqliteFactReader")
    assert hasattr(dr, "ProfessorFactNotFound")


def test_recommend_settings_has_fact_knobs():
    from dext_recommend.config import RecommendSettings
    s = RecommendSettings()
    assert s.fact_read_timeout > 0
    assert s.fact_chunk_size >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_import_boundary.py::test_dext_recommend_fact_submodules_importable_without_dext_family tests/dext_recommend/test_recommend_import_boundary.py::test_catalog_fact_adapter_reexported_at_root tests/dext_recommend/test_recommend_import_boundary.py::test_recommend_settings_has_fact_knobs -v`
Expected: FAIL on missing re-exports / config knobs.

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/config.py`, add inside `RecommendSettings` (after `readiness_*` fields):

```python
    # R4 professor-fact reads
    fact_read_timeout: float = Field(default=5.0, gt=0.0)
    fact_chunk_size: int = Field(default=900, ge=1)
```

In `src/dext_recommend/facts/__init__.py`:

```python
"""Professor fact-bundle assembly (R4)."""
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids
from dext_recommend.facts.evidence import (
    WEAK_EXPLANATION_TEXT, build_fact_items, select_publication_snippets,
    select_statement_snippets,
)
from dext_recommend.facts.source_urls import (
    canonicalize_source_url, dedupe_source_urls,
)

__all__ = [
    "WEAK_EXPLANATION_TEXT", "build_fact_items", "canonicalize_source_url",
    "chunk_entity_ids", "dedupe_entity_ids", "dedupe_source_urls",
    "select_publication_snippets", "select_statement_snippets",
]
```

In `src/dext_recommend/adapters/__init__.py`, add (merge with existing re-exports):

```python
from dext_recommend.adapters._catalog_fact_reader import (
    CatalogProfessorDetailRows, CatalogProfessorFactReader, CatalogSqliteFactReader,
)
from dext_recommend.adapters.catalog_professor_facts import (
    CatalogProfessorFactAdapter,
)
```

(Add those names to the existing `__all__` in that file.)

In `src/dext_recommend/__init__.py`, import `CatalogProfessorFactAdapter`, `CatalogSqliteFactReader`, and `CatalogProfessorFactReader` from `dext_recommend.adapters` (create a new adapters import block if the root currently imports no adapter symbols). Add `ProfessorFactNotFound` to the existing `from dext_recommend.ports import (...)` block. Add all four names to `__all__`.

Before this root re-export works, Task 10 must already have re-exported `ProfessorFactNotFound` from `src/dext_recommend/ports/__init__.py`. If `from dext_recommend.ports import ProfessorFactNotFound` fails here, fix `ports/__init__.py` rather than importing directly from `ports.professor_facts` at the root.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_import_boundary.py::test_dext_recommend_fact_submodules_importable_without_dext_family tests/dext_recommend/test_recommend_import_boundary.py::test_catalog_fact_adapter_reexported_at_root tests/dext_recommend/test_recommend_import_boundary.py::test_recommend_settings_has_fact_knobs -v`
Expected: PASS

- [ ] **Step 5: Run the full import-boundary + adapter-no-dext_graph suite, then commit**

Run: `uv run pytest tests/dext_recommend/test_recommend_import_boundary.py -q`
Expected: PASS

```bash
git add src/dext_recommend/config.py src/dext_recommend/facts/__init__.py src/dext_recommend/adapters/__init__.py src/dext_recommend/__init__.py tests/dext_recommend/test_recommend_import_boundary.py
git -c commit.gpgsign=false commit -m "feat(rec): R4 re-exports + config knobs + import-boundary coverage"
```

---

## Task 15: Full-module green + docs marker

**Files:**
- Modify: `docs/superpowers/specs/2026-07-02-dext-recommend-04b-professor-facts-impl-design.md` (add an "Implemented" status line at top, matching the R3c convention)

**Interfaces:**
- Produces: all R4 tests green; spec status updated to implemented.

- [ ] **Step 1: Run the full dext_recommend suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS (all R4 tests plus the existing suite). If any pre-existing test broke because `ProfessorDetail` now requires `fact_bundle`, fix the construction in that test to pass a minimal `FactBundle` (e.g. `tests/dext_recommend/test_recommend_fake_ports.py`, `test_recommend_detail_fetch.py`, or `_recfixtures.py` detail builders).

- [ ] **Step 2: Update spec status**

At the top of `docs/superpowers/specs/2026-07-02-dext-recommend-04b-professor-facts-impl-design.md`, change the status line:

```markdown
> 状态：已实现；R4 adapter + reader + FactBundle 组装落地，单测全绿（tests/dext_recommend/ 全模块绿）；live ACTIVE-build 验收留待 R7 production acceptance
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-02-dext-recommend-04b-professor-facts-impl-design.md
git -c commit.gpgsign=false commit -m "docs(rec): mark R4b professor facts implemented"
```

---

## Self-Review Checklist (run before declaring done)

- **R4b §2 authority matrix** — each row maps to a reader SQL or mapping: identity/canonical → `_CANONICAL_SQL`; profile payload → `_profile_payload` parse; university display name → `_UNIVERSITY_NAME_SQL`; org-unit display → `_org_unit_names` from observation payload; statements → `_STATEMENTS_SQL`; mentions → `_MENTIONS_SQL` (needs_review filter in `select_publication_snippets`); approved topics → `_TOPIC_LINKS_SQL` + `review_status='approved'`; source URLs → `_SOURCE_URLS_SQL` + `dedupe_source_urls`; findings → `_FINDINGS_SQL`; contacts → canonical email/phone double-gated. ✓
- **R4b §3 two-layer** — `CatalogProfessorFactReader` Protocol + `CatalogSqliteFactReader` (raw rows) + `CatalogProfessorFactAdapter` (domain mapping, no SQL in domain). ✓
- **R4b §4 FactBundle composition** — `ProfessorDetail.fact_bundle: FactBundle` required; `fact_bundle.build_id == detail.build_id == snapshot.build_id`; `fact_bundle.subject_id == detail.entity_id`; `detail.provenance_refs == fact_bundle.source_refs`; contacts never in FactItem/source_refs. ✓
- **R4b §5 visibility/failure** — `hydrate` silently drops missing/inactive/excluded; `get_detail` raises `ProfessorFactNotFound` for missing/inactive/excluded; review gated by `can_view_review`; contacts double-gated; `profile_hash=null` → `profile_hash_missing` risk. ✓
- **R4b §6 acceptance** — temp SQLite fixture with real table/column names + JSON payloads (Task 2); hydrate dedup/chunk/build-pin/excluded (Tasks 4, 11, 13); detail statements/publications/topics/source-refs/findings/FactBundle invariants/contacts double-permission (Task 12); snapshot-switch concurrency (Task 13); event-loop heartbeat thread-offload (Task 6); schema-capability missing / JSON invalid / timeout safe errors (Tasks 3, 4, 6). Live ACTIVE-build acceptance is explicitly out of unit-scope (R7). ✓
- **R4b §7 non-targets** — no Neo4j detail query, no detail cache, no HTTP DTO, no LLM. ✓
- **Placeholder scan** — no TBD/TODO/“implement later”; every code step shows the code. ✓
- **Type consistency** — `CatalogProfessorFactReader` Protocol method `read_fact_rows` / `read_detail_rows` / `check_capability` match the `CatalogSqliteFactReader` signatures; `ProfessorFact`/`ProfessorDetail` field names match `core/filters.py` and `core/recall.py` consumers (`university_id`, `city_name`, `org_unit_ids`, `topic_ids`, `master_eligibility`, `phd_eligibility`, `role_status`). ✓
