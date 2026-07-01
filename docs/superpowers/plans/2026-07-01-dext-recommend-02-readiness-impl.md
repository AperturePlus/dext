# dext_recommend R2 Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `ReadinessService.check()` + three real read-only adapters (catalog SQLite / Qdrant / Neo4j) + ranking profile reader, so an `ActiveBuildSnapshot` can be built, validated, and cached from published ACTIVE build artifacts — fully unit-tested with fake ports / `:memory:` SQLite / injected fake async clients, no dependency on a real ACTIVE build.

**Architecture:** Two-phase dependency-graph orchestration in `check()` (phase 1: catalog+ranking concurrent; phase 2: catalog-samples+vector+graph concurrent, using `sample_ids` derived from catalog). Adapters split into a dialect seam (reader Protocol + concrete reader) and a dialect-agnostic mapping layer (pure functions), so a future PG reader reuses mappings. `gather_safe` wraps each call in `asyncio.wait_for` and normalizes timeout/exception/`ReadinessSourceError` into structured `RecommendationError` via per-call `failure_code`. An `asyncio.Lock` serializes concurrent `check()` so a late-finishing older check can't overwrite a newer snapshot.

**Tech Stack:** Python 3.11, asyncio, `sqlite3` (stdlib, read-only URI mode), `qdrant_client.AsyncQdrantClient`, `neo4j.AsyncGraphDatabase`, pydantic v2 (`RecommendSettings`), pytest `asyncio_mode="auto"`.

## Global Constraints

- **Test runner:** `uv run pytest` (auto-syncs deps from `pyproject.toml`). Python 3.11.
- **Module-green bar:** a phase is done when `uv run pytest tests/dext_recommend/ -q` is green; full-project `uv run pytest -q` timeout is a known constraint, not a blocker.
- **TDD always:** write the failing test, run it RED, implement minimally, run GREEN, commit one conventional commit per green step (`feat(rec): …`, `test(rec): …`, `docs(rec): …`).
- **Import boundary:** `dext_recommend` (including `adapters/` subtree) must NOT import `dext`, `dext_graph`, `dext_monitor`, `dext_competition`. It MAY import `dext_grounded` (shared contract). Catalog adapter writes its own read-only SQLite connection — never imports `dext_graph.catalog.db`.
- **Read-only catalog:** catalog adapter uses `mode=ro` URI + `PRAGMA query_only=ON`; never runs migrations or writes.
- **Async boundary (frozen in R1):** all port I/O methods are `async def`; `ActiveSnapshotProvider.get_snapshot()` and `ReadinessService.get_snapshot()` stay synchronous (in-memory cached read). Contract tests use `inspect.iscoroutinefunction` to pin this.
- **No real Qdrant/Neo4j in tests:** R2 unit tests use fake async clients / `:memory:` SQLite only. Real end-to-end integration is deferred to R7 (ACTIVE build not ready as of 2026-07-01).
- **Secrets:** API keys use `pydantic.SecretStr`; never appear in `model_dump()` / logs / manifest.
- **Platform:** Windows + Git Bash. `LF will be replaced by CRLF` git warnings are benign.

**Spec:** [2026-07-01-dext-recommend-02b-readiness-impl-design.md](../specs/2026-07-01-dext-recommend-02b-readiness-impl-design.md) (implementation design) + [2026-06-30-dext-recommendation-02-readiness-design.md](../specs/2026-06-30-dext-recommendation-02-readiness-design.md) (contract spec, authoritative on failure modes/acceptance).

---

## File Structure

**Create:**
- `src/dext_recommend/adapters/_sampling.py` — pure `deterministic_sample_ids(entity_ids, k)`
- `src/dext_recommend/adapters/_mappings.py` — pure `map_catalog_release` / `map_professor_sample` / `map_vector_release` / `map_graph_release`
- `src/dext_recommend/adapters/_catalog_reader.py` — `CatalogReleaseReader` Protocol + `CatalogSqliteReader`
- `src/dext_recommend/adapters/_vector_reader.py` — `VectorReleaseReader` Protocol + `QdrantReader`
- `src/dext_recommend/adapters/_graph_reader.py` — `GraphReleaseReader` Protocol + `Neo4jReader`
- `src/dext_recommend/adapters/catalog_release.py` — `CatalogReleaseAdapter` (implements `CatalogReleasePort`)
- `src/dext_recommend/adapters/vector_release.py` — `VectorReleaseAdapter` (implements `VectorReleasePort`)
- `src/dext_recommend/adapters/graph_release.py` — `GraphReleaseAdapter` (implements `GraphReleasePort`)
- `src/dext_recommend/adapters/ranking_profile.py` — `RankingProfileAdapter` (implements `RankingProfilePort`)
- `src/dext_recommend/adapters/_readback_call.py` — `ReadbackCall` dataclass + `gather_safe`
- `tests/dext_recommend/test_recommend_sampling.py`
- `tests/dext_recommend/test_recommend_mappings.py`
- `tests/dext_recommend/test_recommend_catalog_adapter.py`
- `tests/dext_recommend/test_recommend_vector_adapter.py`
- `tests/dext_recommend/test_recommend_graph_adapter.py`
- `tests/dext_recommend/test_recommend_ranking_profile_adapter.py`
- `tests/dext_recommend/test_recommend_gather_safe.py`
- `tests/dext_recommend/test_recommend_readiness_service.py`

**Modify:**
- `src/dext_recommend/errors.py` — +6 error codes
- `src/dext_recommend/config.py` — +6 threshold/timeout fields with validators
- `src/dext_recommend/readiness.py` — implement `ReadinessService.check()` + `_assemble`; add `ReadinessDeps`
- `src/dext_recommend/adapters/__init__.py` — re-export adapter classes
- `tests/dext_recommend/test_recommend_import_boundary.py` — add adapters-subtree assertion

---

## Task 1: Extend error codes + config thresholds

**Files:**
- Modify: `src/dext_recommend/errors.py`
- Modify: `src/dext_recommend/config.py`
- Test: `tests/dext_recommend/test_recommend_errors.py`, `tests/dext_recommend/test_recommend_config.py`

**Interfaces:**
- Produces: `RecommendationErrorCode.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT`, `.PROFILE_HASH_COVERAGE_INSUFFICIENT`, `.ROLE_STATUS_COVERAGE_INSUFFICIENT`, `.ELIGIBILITY_COVERAGE_INSUFFICIENT`, `.ORG_UNIT_FILTER_UNAVAILABLE`, `.RANKING_PROFILE_UNAVAILABLE`; `RecommendSettings` fields `readiness_readback_timeout`, `readiness_sample_size`, `coverage_threshold_org_unit_ids`, `coverage_threshold_profile_hash`, `coverage_threshold_role_status`, `coverage_threshold_eligibility`.

- [ ] **Step 1: Write the failing test for new error codes**

Add to `tests/dext_recommend/test_recommend_errors.py`:

```python
def test_new_readiness_error_codes_registered():
    from dext_recommend.errors import RecommendationErrorCode as C
    assert C.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT.value == "org_unit_ids_coverage_insufficient"
    assert C.PROFILE_HASH_COVERAGE_INSUFFICIENT.value == "profile_hash_coverage_insufficient"
    assert C.ROLE_STATUS_COVERAGE_INSUFFICIENT.value == "role_status_coverage_insufficient"
    assert C.ELIGIBILITY_COVERAGE_INSUFFICIENT.value == "eligibility_coverage_insufficient"
    assert C.ORG_UNIT_FILTER_UNAVAILABLE.value == "org_unit_filter_unavailable"
    assert C.RANKING_PROFILE_UNAVAILABLE.value == "ranking_profile_unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_errors.py::test_new_readiness_error_codes_registered -v`
Expected: FAIL with `AttributeError: ORG_UNIT_IDS_COVERAGE_INSUFFICIENT`

- [ ] **Step 3: Add the six codes to the enum**

In `src/dext_recommend/errors.py`, extend `RecommendationErrorCode` (after `GENERATION_UNAVAILABLE`):

```python
    ORG_UNIT_IDS_COVERAGE_INSUFFICIENT = "org_unit_ids_coverage_insufficient"
    PROFILE_HASH_COVERAGE_INSUFFICIENT = "profile_hash_coverage_insufficient"
    ROLE_STATUS_COVERAGE_INSUFFICIENT = "role_status_coverage_insufficient"
    ELIGIBILITY_COVERAGE_INSUFFICIENT = "eligibility_coverage_insufficient"
    ORG_UNIT_FILTER_UNAVAILABLE = "org_unit_filter_unavailable"
    RANKING_PROFILE_UNAVAILABLE = "ranking_profile_unavailable"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_errors.py::test_new_readiness_error_codes_registered -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for config thresholds + validators**

Add to `tests/dext_recommend/test_recommend_config.py`:

```python
import pytest
from pydantic import ValidationError

from dext_recommend.config import RecommendSettings


def test_readiness_thresholds_have_defaults():
    s = RecommendSettings()
    assert s.readiness_readback_timeout == 5.0
    assert s.readiness_sample_size == 50
    assert s.coverage_threshold_org_unit_ids == 0.95
    assert s.coverage_threshold_profile_hash == 0.99
    assert s.coverage_threshold_role_status == 0.95
    assert s.coverage_threshold_eligibility == 0.95


def test_readiness_thresholds_reject_out_of_range():
    with pytest.raises(ValidationError):
        RecommendSettings(readiness_readback_timeout=0)
    with pytest.raises(ValidationError):
        RecommendSettings(readiness_readback_timeout=-1.0)
    with pytest.raises(ValidationError):
        RecommendSettings(coverage_threshold_org_unit_ids=0.0)
    with pytest.raises(ValidationError):
        RecommendSettings(coverage_threshold_org_unit_ids=1.5)
    with pytest.raises(ValidationError):
        RecommendSettings(readiness_sample_size=-1)


def test_readiness_thresholds_accept_upper_bound():
    s = RecommendSettings(coverage_threshold_profile_hash=1.0)
    assert s.coverage_threshold_profile_hash == 1.0
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_config.py -v`
Expected: FAIL with `AttributeError: readiness_readback_timeout`

- [ ] **Step 7: Add the six config fields with validators**

In `src/dext_recommend/config.py`, add to `RecommendSettings` (after `oversample_max`):

```python
    # Readiness (R2)
    readiness_readback_timeout: float = Field(default=5.0, gt=0.0)
    readiness_sample_size: int = Field(default=50, ge=0)
    coverage_threshold_org_unit_ids: float = Field(default=0.95, gt=0.0, le=1.0)
    coverage_threshold_profile_hash: float = Field(default=0.99, gt=0.0, le=1.0)
    coverage_threshold_role_status: float = Field(default=0.95, gt=0.0, le=1.0)
    coverage_threshold_eligibility: float = Field(default=0.95, gt=0.0, le=1.0)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_config.py -v`
Expected: PASS

- [ ] **Step 9: Run full rec suite + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

```bash
git add src/dext_recommend/errors.py src/dext_recommend/config.py tests/dext_recommend/test_recommend_errors.py tests/dext_recommend/test_recommend_config.py
git commit -m "feat(rec): add R2 readiness error codes + config thresholds"
```

---

## Task 2: Deterministic sampling pure function

**Files:**
- Create: `src/dext_recommend/adapters/_sampling.py`
- Test: `tests/dext_recommend/test_recommend_sampling.py`

**Interfaces:**
- Produces: `deterministic_sample_ids(entity_ids: Sequence[str], k: int) -> tuple[str, ...]` — stable across calls and across SQLite/PG; returns `()` for `k <= 0` or empty input.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_sampling.py`:

```python
from dext_recommend.adapters._sampling import deterministic_sample_ids


def test_empty_input_returns_empty():
    assert deterministic_sample_ids((), 50) == ()
    assert deterministic_sample_ids(("e1",), 0) == ()


def test_deterministic_and_stable():
    ids = [f"e{i}" for i in range(200)]
    a = deterministic_sample_ids(ids, 50)
    b = deterministic_sample_ids(ids, 50)
    assert a == b
    assert len(a) == 50
    # subset of input, no fabrication
    assert set(a).issubset(ids)


def test_order_independent_of_input_order():
    ids = ["e3", "e1", "e2"]
    assert deterministic_sample_ids(ids, 3) == deterministic_sample_ids(sorted(ids), 3)


def test_k_larger_than_input_returns_all_sorted_by_hash():
    ids = ["e1", "e2", "e3"]
    out = deterministic_sample_ids(ids, 50)
    assert len(out) == 3
    assert set(out) == {"e1", "e2", "e3"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_sampling.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters._sampling'`

- [ ] **Step 3: Write the implementation**

Create `src/dext_recommend/adapters/_sampling.py`:

```python
"""Deterministic sample-ID derivation shared across catalog/vector/graph readback.

Stable across calls and across SQLite/PG readers; does not depend on DB sort
order. Used so catalog, Qdrant and Neo4j reconcile the same sample set.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence


def deterministic_sample_ids(
    entity_ids: Sequence[str], k: int,
) -> tuple[str, ...]:
    if k <= 0 or not entity_ids:
        return ()
    keyed = sorted(
        entity_ids,
        key=lambda eid: (hashlib.sha256(eid.encode("utf-8")).hexdigest(), eid),
    )
    return tuple(keyed[:k])


__all__ = ["deterministic_sample_ids"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_sampling.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_sampling.py tests/dext_recommend/test_recommend_sampling.py
git commit -m "feat(rec): deterministic sample-id derivation for readiness readback"
```

---

## Task 3: Mapping layer pure functions

**Files:**
- Create: `src/dext_recommend/adapters/_mappings.py`
- Test: `tests/dext_recommend/test_recommend_mappings.py`

**Interfaces:**
- Consumes: `CatalogReleaseObservation`, `ProfessorReleaseSample`, `VectorReleaseObservation`, `GraphReleaseObservation` (from `dext_recommend.ports.release_readback`).
- Produces: `map_catalog_release(row, sample_ids)`, `map_professor_sample(row)`, `map_vector_release(raw)`, `map_graph_release(raw)`.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_mappings.py`:

```python
from dext_recommend.adapters._mappings import (
    map_catalog_release, map_graph_release, map_professor_sample,
    map_vector_release,
)


def _catalog_row():
    return {
        "build_id": "b1",
        "catalog_schema_version": 6,
        "qdrant_payload_schema_version": 2,
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 1536,
        "embedding_fingerprint": "fp-1",
        "taxonomy_version": "tax-v1",
        "expected_professor_count": 11801,
        "created_at": "2026-07-01T00:00:00+00:00",
    }


def test_map_catalog_release_passes_sample_ids_through():
    obs = map_catalog_release(_catalog_row(), ("e1", "e2"))
    assert obs.build_id == "b1"
    assert obs.catalog_schema_version == 6
    assert obs.embedding_fingerprint == "fp-1"
    assert obs.expected_professor_count == 11801
    assert obs.sample_entity_ids == ("e1", "e2")


def test_map_professor_sample_normalizes_org_unit_ids():
    sample = map_professor_sample({
        "entity_id": "e1",
        "org_unit_ids": ["org-a", "org-b"],
        "profile_hash": "h1",
        "role_status": "included",
        "master_eligibility": "confirmed",
        "phd_eligibility": "unknown",
        "embedding_fingerprint": "fp-1",
    })
    assert sample.entity_id == "e1"
    assert sample.org_unit_ids == ("org-a", "org-b")
    assert sample.profile_hash == "h1"


def _vector_raw():
    return {
        "alias": "dext_professors_current",
        "target_collection": "dext_professors__b1",
        "build_id": "b1",
        "payload_schema_version": 2,
        "embedding_fingerprint": "fp-1",
        "embedding_dimension": 1536,
        "point_count": 11801,
        "samples": [{
            "entity_id": "e1",
            "org_unit_ids": ["org-a"],
            "profile_hash": "h1",
            "role_status": "included",
            "master_eligibility": "confirmed",
            "phd_eligibility": "unknown",
            "embedding_fingerprint": "fp-1",
        }],
        "coverage": [
            {"field": "org_unit_ids", "covered": 1.0, "sample_size": 50,
             "invalid_count": 0, "mismatch_count": 0},
        ],
    }


def test_map_vector_release_builds_full_observation():
    obs = map_vector_release(_vector_raw())
    assert obs.alias == "dext_professors_current"
    assert obs.target_collection == "dext_professors__b1"
    assert obs.build_id == "b1"
    assert obs.payload_schema_version == 2
    assert obs.embedding_fingerprint == "fp-1"
    assert obs.embedding_dimension == 1536
    assert obs.point_count == 11801
    assert len(obs.samples) == 1
    assert obs.samples[0].entity_id == "e1"
    assert len(obs.coverage) == 1
    assert obs.coverage[0].field == "org_unit_ids"


def test_map_graph_release():
    raw = {
        "build_id": "b1",
        "samples": [{
            "entity_id": "e1",
            "org_unit_ids": ["org-a"],
            "profile_hash": "h1",
            "role_status": "included",
            "master_eligibility": "confirmed",
            "phd_eligibility": "unknown",
            "embedding_fingerprint": "fp-1",
        }],
    }
    obs = map_graph_release(raw)
    assert obs.build_id == "b1"
    assert obs.samples[0].entity_id == "e1"


def test_map_catalog_release_taxonomy_none_passthrough():
    row = _catalog_row()
    row["taxonomy_version"] = None
    obs = map_catalog_release(row, ())
    assert obs.taxonomy_version is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_mappings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters._mappings'`

- [ ] **Step 3: Write the implementation**

Create `src/dext_recommend/adapters/_mappings.py`:

```python
"""Dialect-agnostic mapping from raw rows/dicts to release observations.

These pure functions consume Mapping / dict (never sqlite3.Row, Qdrant point
or Neo4j record), so a future CatalogPgReader reuses them unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation, GraphReleaseObservation, PayloadCoverageObservation,
    ProfessorReleaseSample, VectorReleaseObservation,
)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def map_catalog_release(
    row: Mapping[str, object], sample_ids: tuple[str, ...],
) -> CatalogReleaseObservation:
    return CatalogReleaseObservation(
        build_id=str(row["build_id"]),
        catalog_schema_version=int(row["catalog_schema_version"]),
        qdrant_payload_schema_version=int(row["qdrant_payload_schema_version"]),
        embedding_provider=str(row["embedding_provider"]),
        embedding_model=str(row["embedding_model"]),
        embedding_dimension=int(row["embedding_dimension"]),
        embedding_fingerprint=str(row["embedding_fingerprint"]),
        taxonomy_version=row["taxonomy_version"] if row["taxonomy_version"] is None else str(row["taxonomy_version"]),
        expected_professor_count=int(row["expected_professor_count"]),
        sample_entity_ids=sample_ids,
        created_at=_parse_dt(str(row["created_at"])),
    )


def map_professor_sample(row: Mapping[str, object]) -> ProfessorReleaseSample:
    org_units = row.get("org_unit_ids") or ()
    return ProfessorReleaseSample(
        entity_id=str(row["entity_id"]),
        org_unit_ids=tuple(org_units),
        profile_hash=row.get("profile_hash") if row.get("profile_hash") is None else str(row["profile_hash"]),
        role_status=row.get("role_status") if row.get("role_status") is None else str(row["role_status"]),
        master_eligibility=row.get("master_eligibility") if row.get("master_eligibility") is None else str(row["master_eligibility"]),
        phd_eligibility=row.get("phd_eligibility") if row.get("phd_eligibility") is None else str(row["phd_eligibility"]),
        embedding_fingerprint=row.get("embedding_fingerprint") if row.get("embedding_fingerprint") is None else str(row["embedding_fingerprint"]),
    )


def map_vector_release(raw: Mapping[str, object]) -> VectorReleaseObservation:
    samples = tuple(map_professor_sample(s) for s in (raw.get("samples") or ()))
    coverage = tuple(
        PayloadCoverageObservation(
            field=str(c["field"]),
            covered=float(c["covered"]),
            sample_size=int(c["sample_size"]),
            invalid_count=int(c.get("invalid_count", 0)),
            mismatch_count=int(c.get("mismatch_count", 0)),
        )
        for c in (raw.get("coverage") or ())
    )
    return VectorReleaseObservation(
        alias=str(raw["alias"]),
        target_collection=str(raw["target_collection"]),
        build_id=str(raw["build_id"]),
        payload_schema_version=int(raw["payload_schema_version"]),
        embedding_fingerprint=str(raw["embedding_fingerprint"]),
        embedding_dimension=int(raw["embedding_dimension"]),
        point_count=int(raw["point_count"]),
        samples=samples,
        coverage=coverage,
    )


def map_graph_release(raw: Mapping[str, object]) -> GraphReleaseObservation:
    samples = tuple(map_professor_sample(s) for s in (raw.get("samples") or ()))
    return GraphReleaseObservation(
        build_id=str(raw["build_id"]),
        samples=samples,
    )


__all__ = [
    "map_catalog_release", "map_graph_release", "map_professor_sample",
    "map_vector_release",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_mappings.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_mappings.py tests/dext_recommend/test_recommend_mappings.py
git commit -m "feat(rec): dialect-agnostic release observation mappings"
```

---

## Task 4: Catalog SQLite adapter

**Files:**
- Create: `src/dext_recommend/adapters/_catalog_reader.py`
- Create: `src/dext_recommend/adapters/catalog_release.py`
- Test: `tests/dext_recommend/test_recommend_catalog_adapter.py`

**Interfaces:**
- Consumes: `CatalogReleasePort` (from `dext_recommend.ports.release_readback`), `deterministic_sample_ids`, `map_catalog_release`, `map_professor_sample`, `RecommendSettings`.
- Produces: `CatalogReleaseReader` Protocol, `CatalogSqliteReader`, `CatalogReleaseAdapter` (implements `CatalogReleasePort`).

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_catalog_adapter.py`:

```python
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

import pytest

from dext_recommend.adapters._catalog_reader import CatalogSqliteReader
from dext_recommend.adapters.catalog_release import CatalogReleaseAdapter


SCHEMA = """
CREATE TABLE graph_builds (
    id TEXT PRIMARY KEY, status TEXT NOT NULL,
    curation_version TEXT NOT NULL, taxonomy_version TEXT,
    graph_schema_version INTEGER NOT NULL, vector_schema_version INTEGER NOT NULL,
    embedding_provider TEXT, embedding_base_url TEXT, embedding_model TEXT,
    embedding_fingerprint TEXT, embedding_dimension INTEGER,
    settings_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT, finished_at TEXT, last_error TEXT
);
CREATE TABLE canonical_professors (
    entity_id TEXT, build_id TEXT, name TEXT, title_raw TEXT,
    title_family TEXT, role_status TEXT, role_reason_codes TEXT,
    master_eligibility TEXT, phd_eligibility TEXT, research_areas_text TEXT,
    bio TEXT, email TEXT, phone TEXT, profile_url TEXT, external_url TEXT,
    active INTEGER, completeness REAL, PRIMARY KEY (entity_id, build_id)
);
CREATE TABLE professor_profiles (
    build_id TEXT, entity_id TEXT, profile_hash TEXT, template_version TEXT,
    tokenizer_identity TEXT, normalized_profile TEXT, token_count INTEGER,
    payload_json TEXT, created_at TEXT, PRIMARY KEY (build_id, entity_id)
);
"""


def _build_db(tmp_path, *, active_rows, professors, profiles):
    path = tmp_path / "catalog.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,"
            "graph_schema_version,vector_schema_version,embedding_provider,"
            "embedding_model,embedding_fingerprint,embedding_dimension,settings_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            active_rows,
        )
        conn.executemany(
            "INSERT INTO canonical_professors(entity_id,build_id,name,title_family,"
            "role_status,role_reason_codes,master_eligibility,phd_eligibility,active,"
            "completeness) VALUES(?,?,?,?,?,?,?,?,?,1,0.5)",
            professors,
        )
        conn.executemany(
            "INSERT INTO professor_profiles(build_id,entity_id,profile_hash,"
            "template_version,tokenizer_identity,normalized_profile,token_count,"
            "payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            profiles,
        )
    return path


def _active_row(build_id="b1"):
    return (build_id, "ACTIVE", "cur-v1", "tax-v1", 6, 6, "openai",
            "text-embedding-3-small", "fp-1", 1536, "{}")


def test_read_active_returns_observation_with_sample_ids(tmp_path):
    profs = [(f"e{i}", "b1", f"name{i}", "professor", "included", "[]",
              "confirmed", "unknown") for i in range(60)]
    profiles = [("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10, "{}", "2026-01-01T00:00:00+00:00") for i in range(60)]
    path = _build_db(tmp_path, active_rows=[_active_row()], professors=profs, profiles=profiles)
    adapter = CatalogReleaseAdapter(CatalogSqliteReader(path), sample_size=10)
    import asyncio
    obs = asyncio.run(adapter.read_active())
    assert obs is not None
    assert obs.build_id == "b1"
    assert obs.embedding_fingerprint == "fp-1"
    assert len(obs.sample_entity_ids) == 10


async def test_read_active_returns_none_when_no_active(tmp_path):
    path = _build_db(tmp_path, active_rows=[], professors=[], profiles=[])
    adapter = CatalogReleaseAdapter(CatalogSqliteReader(path), sample_size=10)
    assert await adapter.read_active() is None


async def test_read_samples_returns_professor_samples(tmp_path):
    profs = [("e1", "b1", "n1", "professor", "included", "[]", "confirmed", "unknown")]
    profiles = [("b1", "e1", "h1", "tv", "ti", "np", 10, "{}", "2026-01-01T00:00:00+00:00")]
    path = _build_db(tmp_path, active_rows=[_active_row()], professors=profs, profiles=profiles)
    adapter = CatalogReleaseAdapter(CatalogSqliteReader(path), sample_size=10)
    samples = await adapter.read_samples("b1", ("e1",))
    assert len(samples) == 1
    assert samples[0].entity_id == "e1"
    assert samples[0].profile_hash == "h1"


def test_connect_ro_rejects_missing_file(tmp_path):
    from dext_recommend.adapters._catalog_reader import CatalogSqliteReader
    from dext_recommend.ports.release_readback import ReadinessSourceError
    reader = CatalogSqliteReader(tmp_path / "nonexistent.db")
    with pytest.raises(ReadinessSourceError):
        import asyncio
        asyncio.run(reader.read_active_row())


def test_connect_ro_query_only_blocks_writes(tmp_path):
    path = _build_db(tmp_path, active_rows=[_active_row()],
                     professors=[("e1","b1","n","professor","included","[]","confirmed","unknown")],
                     profiles=[("b1","e1","h","tv","ti","np",10,"{}","2026-01-01T00:00:00+00:00")])
    reader = CatalogSqliteReader(path)
    import asyncio
    async def _try_write():
        conn = reader._connect_ro(path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,graph_schema_version,vector_schema_version,settings_json) VALUES('x','ACTIVE','c','t',1,1,'{}')")
        finally:
            conn.close()
    asyncio.run(_try_write())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters._catalog_reader'`

- [ ] **Step 3: Write the reader**

Create `src/dext_recommend/adapters/_catalog_reader.py`:

```python
"""Catalog SQLite dialect seam: read raw active-build rows without importing dext_graph.

The reader returns plain sqlite3.Row mappings; domain mapping lives in _mappings.py.
Read-only URI mode + query_only pragma guarantees no writes.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

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


_ACTIVE_SQL = """
SELECT id, status, curation_version, taxonomy_version,
       graph_schema_version, vector_schema_version, embedding_provider,
       embedding_model, embedding_fingerprint, embedding_dimension, finished_at
FROM graph_builds WHERE status='ACTIVE'
"""

_ACTIVE_ENTITY_IDS_SQL = """
SELECT entity_id FROM canonical_professors
WHERE build_id=? AND active=1 ORDER BY entity_id
"""

_SAMPLE_SQL = """
SELECT cp.entity_id, cp.role_status, cp.master_eligibility, cp.phd_eligibility,
       pp.profile_hash
FROM canonical_professors cp
LEFT JOIN professor_profiles pp
  ON pp.build_id=cp.build_id AND pp.entity_id=cp.entity_id
WHERE cp.build_id=? AND cp.entity_id IN (%s)
ORDER BY cp.entity_id
"""


class CatalogReleaseReader(Protocol):
    async def read_active_row(self) -> Mapping[str, Any] | None: ...
    async def read_sample_rows(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]: ...


class CatalogSqliteReader:
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        self._path = Path(path)
        self._timeout = timeout

    def _connect_ro(self, path: Path | None = None) -> sqlite3.Connection:
        return _connect_ro(path or self._path)

    async def read_active_row(self) -> Mapping[str, Any] | None:
        def _read() -> Mapping[str, Any] | None:
            with closing(self._connect_ro()) as conn:
                row = conn.execute(_ACTIVE_SQL).fetchone()
                if row is None:
                    return None
                data = dict(row)
                ids = [r["entity_id"] for r in conn.execute(
                    _ACTIVE_ENTITY_IDS_SQL, (data["id"],)
                )]
                data["active_entity_ids"] = tuple(ids)
                return data
        return await asyncio.wait_for(
            asyncio.to_thread(_read), self._timeout,
        )

    async def read_sample_rows(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        if not sample_ids:
            return ()
        placeholders = ",".join("?" for _ in sample_ids)
        sql = _SAMPLE_SQL % placeholders

        def _read() -> tuple[Mapping[str, Any], ...]:
            with closing(self._connect_ro()) as conn:
                return tuple(
                    dict(r) for r in conn.execute(sql, (build_id, *sample_ids))
                )
        return await asyncio.wait_for(
            asyncio.to_thread(_read), self._timeout,
        )


__all__ = ["CatalogReleaseReader", "CatalogSqliteReader"]
```

- [ ] **Step 4: Write the adapter**

Create `src/dext_recommend/adapters/catalog_release.py`:

```python
"""CatalogReleaseAdapter: composes reader + mapping + sampling, implements CatalogReleasePort."""
from __future__ import annotations

from dext_recommend.adapters._catalog_reader import CatalogReleaseReader
from dext_recommend.adapters._mappings import map_catalog_release, map_professor_sample
from dext_recommend.adapters._sampling import deterministic_sample_ids
from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation, ProfessorReleaseSample,
)


class CatalogReleaseAdapter:
    def __init__(self, reader: CatalogReleaseReader, *, sample_size: int) -> None:
        self._reader = reader
        self._sample_size = sample_size

    async def read_active(self) -> CatalogReleaseObservation | None:
        row = await self._reader.read_active_row()
        if row is None:
            return None
        sample_ids = deterministic_sample_ids(row["active_entity_ids"], self._sample_size)
        return map_catalog_release(row, sample_ids)

    async def read_samples(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[ProfessorReleaseSample, ...]:
        rows = await self._reader.read_sample_rows(build_id, sample_ids)
        return tuple(map_professor_sample(r) for r in rows)


__all__ = ["CatalogReleaseAdapter"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_catalog_adapter.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/adapters/_catalog_reader.py src/dext_recommend/adapters/catalog_release.py tests/dext_recommend/test_recommend_catalog_adapter.py
git commit -m "feat(rec): catalog SQLite read-only adapter + sampling"
```

---

## Task 5: Qdrant vector adapter

**Files:**
- Create: `src/dext_recommend/adapters/_vector_reader.py`
- Create: `src/dext_recommend/adapters/vector_release.py`
- Test: `tests/dext_recommend/test_recommend_vector_adapter.py`

**Interfaces:**
- Consumes: `VectorReleasePort`, `map_vector_release`, an injected async client (Protocol with `get_aliases`/`count`/`scroll`).
- Produces: `VectorReleaseReader` Protocol, `QdrantReader`, `VectorReleaseAdapter` (implements `VectorReleasePort`).

- [ ] **Step 1: Write the failing test with a fake async client**

Create `tests/dext_recommend/test_recommend_vector_adapter.py`:

```python
import pytest

from dext_recommend.adapters._vector_reader import QdrantReader
from dext_recommend.adapters.vector_release import VectorReleaseAdapter
from dext_recommend.ports.release_readback import ReadinessSourceError


class FakeAlias:
    def __init__(self, collection_name): self.collection_name = collection_name


class FakeCountResult:
    def __init__(self, count): self.count = count


class FakePoint:
    def __init__(self, pid, payload): self.id = pid; self.payload = payload


class FakeScrollResponse:
    def __init__(self, points, next_offset): self.points = points; self.next_offset = next_offset


class FakeAsyncQdrantClient:
    def __init__(self, *, alias_target=None, count=0, points=None, raise_on_aliases=False):
        self._alias_target = alias_target
        self._count = count
        self._points = points or []
        self._raise_on_aliases = raise_on_aliases

    async def get_aliases(self):
        if self._raise_on_aliases:
            raise RuntimeError("connection refused")
        if self._alias_target is None:
            return type("R", (), {"aliases": []})()
        return type("R", (), {"aliases": [FakeAlias(self._alias_target)]})()

    async def count(self, collection_name, *, exact=True):
        return FakeCountResult(self._count)

    async def scroll(self, collection_name, *, limit, offset=None, with_payload=True, with_vectors=False):
        pts = self._points[:limit]
        return FakeScrollResponse([FakePoint(p["entity_id"], p) for p in pts], None)


async def test_read_current_returns_observation():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=11801,
        points=[{
            "entity_id": "e1", "build_id": "b1", "profile_hash": "h1",
            "role_status": "included", "master_eligibility": "confirmed",
            "phd_eligibility": "unknown", "embedding_fingerprint": "fp-1",
            "org_unit_ids": ["org-a"],
        }],
    )
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    obs = await adapter.read_current("dext_professors_current", ("e1",))
    assert obs is not None
    assert obs.alias == "dext_professors_current"
    assert obs.target_collection == "dext_professors__b1"
    assert obs.build_id == "b1"
    assert obs.point_count == 11801
    assert len(obs.samples) == 1


async def test_read_current_returns_none_when_alias_missing():
    client = FakeAsyncQdrantClient(alias_target=None)
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    assert await adapter.read_current("dext_professors_current", ("e1",)) is None


async def test_read_current_raises_readiness_source_error_on_connection_failure():
    client = FakeAsyncQdrantClient(raise_on_aliases=True)
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_current("dext_professors_current", ("e1",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters._vector_reader'`

- [ ] **Step 3: Write the reader**

Create `src/dext_recommend/adapters/_vector_reader.py`:

```python
"""Qdrant dialect seam: resolve current alias, count, scroll sample payloads.

Returns a raw mapping consumed by map_vector_release. Never imports dext_graph's
ProfessorQdrant wrapper — only the qdrant_client async API.
"""
from __future__ import annotations

from typing import Any, Protocol

from dext_recommend.ports.release_readback import ReadinessSourceError

CURRENT_PROFESSOR_ALIAS = "dext_professors_current"


class VectorReleaseReader(Protocol):
    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None: ...


class QdrantReader:
    def __init__(
        self, client: Any, *,
        embedding_dimension: int, embedding_fingerprint: str,
        payload_schema_version: int = 2,
    ) -> None:
        self._client = client
        self._embedding_dimension = embedding_dimension
        self._embedding_fingerprint = embedding_fingerprint
        self._payload_schema_version = payload_schema_version

    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        try:
            aliases = await self._client.get_aliases()
        except Exception as exc:
            raise ReadinessSourceError("qdrant", "alias readback failed", retryable=True) from exc
        matches = [
            str(a.collection_name) for a in aliases.aliases
            if str(getattr(a, "alias_name", "")) == alias
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ReadinessSourceError("qdrant", f"alias {alias} is ambiguous")
        target = matches[0]
        try:
            count_result = await self._client.count(target, exact=True)
            point_count = int(count_result.count)
            points = []
            offset = None
            while True:
                resp, offset = await self._client.scroll(
                    collection_name=target, limit=256, offset=offset,
                    with_payload=True, with_vectors=False,
                )
                for p in resp:
                    points.append(dict(p.payload or {}))
                    points[-1]["entity_id"] = str(p.id)
                if offset is None:
                    break
            wanted = set(sample_ids)
            samples = [p for p in points if p.get("entity_id") in wanted]
        except ReadinessSourceError:
            raise
        except Exception as exc:
            raise ReadinessSourceError("qdrant", "readback failed", retryable=True) from exc
        return {
            "alias": alias,
            "target_collection": target,
            "build_id": samples[0]["build_id"] if samples else "",
            "payload_schema_version": self._payload_schema_version,
            "embedding_fingerprint": self._embedding_fingerprint,
            "embedding_dimension": self._embedding_dimension,
            "point_count": point_count,
            "samples": samples,
            "coverage": _coverage_rows(samples),
        }


def _coverage_rows(samples: list[dict]) -> list[dict]:
    if not samples:
        return []
    n = len(samples)

    def _frac(field):
        ok = sum(1 for s in samples if s.get(field) not in (None, "", []))
        return ok / n
    return [
        {"field": "org_unit_ids", "covered": _frac("org_unit_ids"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "profile_hash", "covered": _frac("profile_hash"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "role_status", "covered": _frac("role_status"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
    ]


__all__ = ["CURRENT_PROFESSOR_ALIAS", "QdrantReader", "VectorReleaseReader"]
```

- [ ] **Step 4: Write the adapter**

Create `src/dext_recommend/adapters/vector_release.py`:

```python
"""VectorReleaseAdapter: composes QdrantReader + mapping, implements VectorReleasePort."""
from __future__ import annotations

from dext_recommend.adapters._mappings import map_vector_release
from dext_recommend.adapters._vector_reader import VectorReleaseReader
from dext_recommend.ports.release_readback import VectorReleaseObservation


class VectorReleaseAdapter:
    def __init__(self, reader: VectorReleaseReader) -> None:
        self._reader = reader

    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> VectorReleaseObservation | None:
        raw = await self._reader.read_current(alias, sample_ids)
        if raw is None:
            return None
        return map_vector_release(raw)


__all__ = ["VectorReleaseAdapter"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/adapters/_vector_reader.py src/dext_recommend/adapters/vector_release.py tests/dext_recommend/test_recommend_vector_adapter.py
git commit -m "feat(rec): Qdrant vector release adapter with fake-async-client tests"
```

---

## Task 6: Neo4j graph adapter

**Files:**
- Create: `src/dext_recommend/adapters/_graph_reader.py`
- Create: `src/dext_recommend/adapters/graph_release.py`
- Test: `tests/dext_recommend/test_recommend_graph_adapter.py`

**Interfaces:**
- Consumes: `GraphReleasePort`, `map_graph_release`, an injected async driver/session (fake).
- Produces: `GraphReleaseReader` Protocol, `Neo4jReader`, `GraphReleaseAdapter` (implements `GraphReleasePort`).

- [ ] **Step 1: Write the failing test with a fake async driver**

Create `tests/dext_recommend/test_recommend_graph_adapter.py`:

```python
import pytest

from dext_recommend.adapters._graph_reader import Neo4jReader
from dext_recommend.adapters.graph_release import GraphReleaseAdapter
from dext_recommend.ports.release_readback import ReadinessSourceError


class FakeRecord:
    def __init__(self, data): self._data = data
    def __getitem__(self, key): return self._data[key]


class FakeResult:
    def __init__(self, records): self._records = records
    async def single(self, *, strict=False): return self._records[0] if self._records else None
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._records): raise StopAsyncIteration
        r = self._records[self._i]; self._i += 1; return r


class FakeSession:
    def __init__(self, *, pointer=None, samples=None, raise_on_run=False):
        self._pointer = pointer
        self._samples = samples or []
        self._raise = raise_on_run

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def run(self, query, **params):
        if self._raise:
            raise RuntimeError("neo4j unreachable")
        if "GraphState" in query:
            return FakeResult([FakeRecord({"build_id": self._pointer})] if self._pointer else [])
        return FakeResult([FakeRecord(s) for s in self._samples])


class FakeDriver:
    def __init__(self, session): self._session = session
    async def verify_connectivity(self): pass
    def session(self, **kw): return self._session
    async def close(self): pass


async def test_read_active_returns_observation():
    session = FakeSession(pointer="b1", samples=[{
        "entity_id": "e1", "build_id": "b1", "profile_hash": "h1",
        "role_status": "included", "master_eligibility": "confirmed",
        "phd_eligibility": "unknown", "embedding_fingerprint": "fp-1",
        "org_unit_ids": ["org-a"],
    }])
    reader = Neo4jReader(FakeDriver(session))
    adapter = GraphReleaseAdapter(reader)
    obs = await adapter.read_active(("e1",))
    assert obs is not None
    assert obs.build_id == "b1"
    assert len(obs.samples) == 1


async def test_read_active_returns_none_when_pointer_missing():
    session = FakeSession(pointer=None)
    reader = Neo4jReader(FakeDriver(session))
    adapter = GraphReleaseAdapter(reader)
    assert await adapter.read_active(("e1",)) is None


async def test_read_active_raises_on_connection_failure():
    session = FakeSession(raise_on_run=True)
    reader = Neo4jReader(FakeDriver(session))
    adapter = GraphReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_active(("e1",))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_graph_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters._graph_reader'`

- [ ] **Step 3: Write the reader**

Create `src/dext_recommend/adapters/_graph_reader.py`:

```python
"""Neo4j dialect seam: read active build pointer + sample professor nodes.

Never imports dext_graph's neo4j_sink; only the neo4j async driver API.
"""
from __future__ import annotations

from typing import Any, Protocol

from dext_recommend.ports.release_readback import ReadinessSourceError

_POINTER_QUERY = (
    "MATCH (:GraphState {name:'active'})-[:POINTS_TO]->(build:Build) "
    "RETURN build.id AS build_id ORDER BY build.id"
)
_SAMPLE_QUERY = (
    "MATCH (n:Professor {build_id: $build_id}) "
    "WHERE n.id IN $ids "
    "RETURN n.id AS entity_id, n.profile_hash AS profile_hash, "
    "n.role_status AS role_status, n.master_eligibility AS master_eligibility, "
    "n.phd_eligibility AS phd_eligibility, n.embedding_fingerprint AS embedding_fingerprint, "
    "n.org_unit_ids AS org_unit_ids"
)


class GraphReleaseReader(Protocol):
    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None: ...


class Neo4jReader:
    def __init__(self, driver: Any) -> None:
        self._driver = driver

    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> dict[str, Any] | None:
        try:
            await self._driver.verify_connectivity()
            async with self._driver.session() as session:
                result = await session.run(_POINTER_QUERY)
                values = [str(r["build_id"]) async for r in result]
        except Exception as exc:
            raise ReadinessSourceError("neo4j", "pointer readback failed", retryable=True) from exc
        if not values:
            return None
        if len(values) > 1:
            raise ReadinessSourceError("neo4j", "active pointer targets multiple builds")
        build_id = values[0]
        try:
            async with self._driver.session() as session:
                result = await session.run(_SAMPLE_QUERY, build_id=build_id, ids=list(sample_ids))
                samples = [dict(r) async for r in result]
        except Exception as exc:
            raise ReadinessSourceError("neo4j", "sample readback failed", retryable=True) from exc
        return {"build_id": build_id, "samples": samples}


__all__ = ["GraphReleaseReader", "Neo4jReader"]
```

- [ ] **Step 4: Write the adapter**

Create `src/dext_recommend/adapters/graph_release.py`:

```python
"""GraphReleaseAdapter: composes Neo4jReader + mapping, implements GraphReleasePort."""
from __future__ import annotations

from dext_recommend.adapters._graph_reader import GraphReleaseReader
from dext_recommend.adapters._mappings import map_graph_release
from dext_recommend.ports.release_readback import GraphReleaseObservation


class GraphReleaseAdapter:
    def __init__(self, reader: GraphReleaseReader) -> None:
        self._reader = reader

    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> GraphReleaseObservation | None:
        raw = await self._reader.read_active(sample_ids)
        if raw is None:
            return None
        return map_graph_release(raw)


__all__ = ["GraphReleaseAdapter"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_graph_adapter.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/dext_recommend/adapters/_graph_reader.py src/dext_recommend/adapters/graph_release.py tests/dext_recommend/test_recommend_graph_adapter.py
git commit -m "feat(rec): Neo4j graph release adapter with fake-async-driver tests"
```

---

## Task 7: Ranking profile adapter

**Files:**
- Create: `src/dext_recommend/adapters/ranking_profile.py`
- Test: `tests/dext_recommend/test_recommend_ranking_profile_adapter.py`

**Interfaces:**
- Consumes: `RankingProfilePort`, `RecommendSettings.ranking_profile_path`.
- Produces: `RankingProfileAdapter` (implements `RankingProfilePort`); `read_version(path) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_ranking_profile_adapter.py`:

```python
import json

import pytest

from dext_recommend.adapters.ranking_profile import RankingProfileAdapter
from dext_recommend.ports.release_readback import ReadinessSourceError


async def test_read_version_returns_version_string(tmp_path):
    profile = tmp_path / "ranking-profile.json"
    profile.write_text(json.dumps({"version": "ranking-v1"}), encoding="utf-8")
    adapter = RankingProfileAdapter()
    assert await adapter.read_version(profile) == "ranking-v1"


async def test_read_version_raises_when_file_missing(tmp_path):
    adapter = RankingProfileAdapter()
    with pytest.raises(ReadinessSourceError):
        await adapter.read_version(tmp_path / "nonexistent.json")


async def test_read_version_raises_when_no_version_key(tmp_path):
    profile = tmp_path / "ranking-profile.json"
    profile.write_text(json.dumps({"weights": {}}), encoding="utf-8")
    adapter = RankingProfileAdapter()
    with pytest.raises(ReadinessSourceError):
        await adapter.read_version(profile)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters.ranking_profile'`

- [ ] **Step 3: Write the implementation**

Create `src/dext_recommend/adapters/ranking_profile.py`:

```python
"""RankingProfileAdapter: reads the version string from a JSON profile file."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dext_recommend.ports.release_readback import ReadinessSourceError


class RankingProfileAdapter:
    async def read_version(self, path: Path) -> str:
        p = Path(path)
        def _read() -> str:
            if not p.is_file():
                raise ReadinessSourceError("ranking", f"profile not found: {p}")
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ReadinessSourceError("ranking", "profile is not valid JSON") from exc
            version = data.get("version")
            if not isinstance(version, str) or not version:
                raise ReadinessSourceError("ranking", "profile missing version string")
            return version
        return await asyncio.to_thread(_read)


__all__ = ["RankingProfileAdapter"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/ranking_profile.py tests/dext_recommend/test_recommend_ranking_profile_adapter.py
git commit -m "feat(rec): ranking profile JSON reader adapter"
```

---

## Task 8: `ReadbackCall` + `gather_safe`

**Files:**
- Create: `src/dext_recommend/adapters/_readback_call.py`
- Test: `tests/dext_recommend/test_recommend_gather_safe.py`

**Interfaces:**
- Consumes: `RecommendationError`, `RecommendationErrorCode`, `ReadinessSourceError`.
- Produces: `ReadbackCall(source, failure_code, coro)`, `gather_safe(*calls, timeout) -> tuple[Any | RecommendationError, ...]`.

- [ ] **Step 1: Write the failing test**

Create `tests/dext_recommend/test_recommend_gather_safe.py`:

```python
import asyncio

import pytest

from dext_recommend.adapters._readback_call import ReadbackCall, gather_safe
from dext_recommend.errors import ErrorSeverity, RecommendationErrorCode
from dext_recommend.ports.release_readback import ReadinessSourceError


async def _ok(value): return value
async def _raise_source(): raise ReadinessSourceError("x", "boom", retryable=True)
async def _raise_runtime(): raise RuntimeError("boom")
async def _slow():
    await asyncio.sleep(10); return "late"


async def test_gather_safe_passes_through_normal_values():
    calls = [
        ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _ok("a")),
        ReadbackCall("ranking", RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE, _ok("v1")),
    ]
    out = await gather_safe(*calls, timeout=5.0)
    assert out == ("a", "v1")


async def test_gather_safe_converts_readiness_source_error():
    calls = [ReadbackCall("vector", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _raise_source())]
    out = await gather_safe(*calls, timeout=5.0)
    assert isinstance(out[0], type(out[0]))  # RecommendationError
    from dext_recommend.errors import RecommendationError
    assert isinstance(out[0], RecommendationError)
    assert out[0].code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    assert out[0].retryable is True


async def test_gather_safe_converts_timeout():
    calls = [ReadbackCall("graph", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _slow())]
    out = await gather_safe(*calls, timeout=0.05)
    from dext_recommend.errors import RecommendationError
    assert isinstance(out[0], RecommendationError)
    assert out[0].code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    assert out[0].retryable is True
    assert "timed out" in out[0].message


async def test_gather_safe_converts_other_exceptions():
    calls = [ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _raise_runtime())]
    out = await gather_safe(*calls, timeout=5.0)
    from dext_recommend.errors import RecommendationError
    assert isinstance(out[0], RecommendationError)
    assert out[0].code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    assert out[0].retryable is False


async def test_gather_safe_passes_none_through():
    calls = [ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, _ok(None))]
    out = await gather_safe(*calls, timeout=5.0)
    assert out == (None,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_gather_safe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dext_recommend.adapters._readback_call'`

- [ ] **Step 3: Write the implementation**

Create `src/dext_recommend/adapters/_readback_call.py`:

```python
"""ReadbackCall metadata + gather_safe: normalize timeout/exception/ReadinessSourceError
into structured RecommendationError using per-call failure_code.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from dext_recommend.errors import ErrorSeverity, RecommendationError, RecommendationErrorCode
from dext_recommend.ports.release_readback import ReadinessSourceError


@dataclass(frozen=True, slots=True)
class ReadbackCall:
    source: str
    failure_code: RecommendationErrorCode
    coro: Awaitable[Any]


async def gather_safe(
    *calls: ReadbackCall, timeout: float,
) -> tuple[Any | RecommendationError, ...]:
    async def _run(coro: Awaitable[Any]) -> Any:
        return await asyncio.wait_for(coro, timeout)

    results = await asyncio.gather(
        *(_run(c.coro) for c in calls), return_exceptions=True,
    )
    out: list[Any | RecommendationError] = []
    for call, result in zip(calls, results, strict=True):
        if isinstance(result, RecommendationError):
            out.append(result)
        elif isinstance(result, ReadinessSourceError):
            out.append(RecommendationError(
                code=call.failure_code,
                severity=ErrorSeverity.ERROR,
                message=result.reason,
                retryable=result.retryable,
            ))
        elif isinstance(result, (asyncio.TimeoutError, TimeoutError)):
            out.append(RecommendationError(
                code=call.failure_code,
                severity=ErrorSeverity.ERROR,
                message=f"{call.source} readback timed out",
                retryable=True,
            ))
        elif isinstance(result, BaseException):
            out.append(RecommendationError(
                code=call.failure_code,
                severity=ErrorSeverity.ERROR,
                message=f"{call.source} readback failed",
                retryable=False,
                operator_action="check adapter logs",
            ))
        else:
            out.append(result)
    return tuple(out)


__all__ = ["ReadbackCall", "gather_safe"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_gather_safe.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_readback_call.py tests/dext_recommend/test_recommend_gather_safe.py
git commit -m "feat(rec): ReadbackCall + gather_safe error normalization"
```

---

## Task 9: `ReadinessService.check()` orchestration + `_assemble`

**Files:**
- Modify: `src/dext_recommend/readiness.py`
- Test: `tests/dext_recommend/test_recommend_readiness_service.py`

**Interfaces:**
- Consumes: `CatalogReleasePort`, `VectorReleasePort`, `GraphReleasePort`, `RankingProfilePort`, `RecommendSettings`, `gather_safe`, `ReadbackCall`, `RecommendationError`, `ErrorSeverity`, `RecommendationErrorCode`, `CoverageStat`, `ActiveBuildSnapshot`, `ReadinessReport`.
- Produces: `ReadinessDeps` (dataclass of 4 ports), `ReadinessService.__init__(deps, settings)`, `ReadinessService.check()` (async), `ReadinessService.get_snapshot()` (sync), `ActiveSnapshotProvider` implementation.

- [ ] **Step 1: Write the failing test for happy path + 5 failure modes + concurrency + warning-only**

Create `tests/dext_recommend/test_recommend_readiness_service.py`:

```python
import asyncio

import pytest

from dext_recommend.errors import ErrorSeverity, RecommendationErrorCode
from dext_recommend.ports._fakes import (
    FakeCatalogReleasePort, FakeGraphReleasePort, FakeRankingProfilePort,
    FakeVectorReleasePort,
)
from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation, GraphReleaseObservation, PayloadCoverageObservation,
    ProfessorReleaseSample, VectorReleaseObservation,
)
from dext_recommend.readiness import ReadinessDeps, ReadinessService
from dext_recommend.config import RecommendSettings
from datetime import datetime, timezone


def _catalog_obs(build_id="b1", sample_ids=("e1",)):
    return CatalogReleaseObservation(
        build_id=build_id, catalog_schema_version=6,
        qdrant_payload_schema_version=2, embedding_provider="openai",
        embedding_model="m", embedding_dimension=1536,
        embedding_fingerprint="fp-1", taxonomy_version="tax-v1",
        expected_professor_count=1, sample_entity_ids=sample_ids,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def _sample(eid="e1"):
    return ProfessorReleaseSample(eid, ("org-a",), profile_hash="h1",
                                  role_status="included", master_eligibility="confirmed",
                                  phd_eligibility="unknown", embedding_fingerprint="fp-1")


def _vector_obs(build_id="b1"):
    return VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id=build_id, payload_schema_version=2,
        embedding_fingerprint="fp-1", embedding_dimension=1536, point_count=1,
        samples=(_sample(),), coverage=(PayloadCoverageObservation("org_unit_ids", 1.0, 1),),
    )


def _graph_obs(build_id="b1"):
    return GraphReleaseObservation(build_id=build_id, samples=(_sample(),))


def _service(catalog, vector, graph, ranking, **settings):
    s = RecommendSettings(**settings)
    return ReadinessService(ReadinessDeps(catalog, vector, graph, ranking), s)


async def test_check_happy_path_returns_ready_snapshot():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(_vector_obs()),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    assert report.snapshot is not None
    assert report.snapshot.build_id == "b1"
    assert report.snapshot.ranking_profile_version == "ranking-v1"
    assert report.errors == ()
    assert svc.get_snapshot() is report.snapshot


async def test_check_no_active_build_returns_unavailable():
    svc = _service(
        FakeCatalogReleasePort(None), FakeVectorReleasePort(),
        FakeGraphReleasePort(), FakeRankingProfilePort(),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE in codes


async def test_check_three_way_build_id_mismatch_returns_inconsistent():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs("b1"), [_sample()]),
        FakeVectorReleasePort(_vector_obs("b2")),
        FakeGraphReleasePort(_graph_obs("b3")),
        FakeRankingProfilePort(),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_embedding_fingerprint_mismatch():
    v = VectorReleaseObservation(
        alias="a", target_collection="c", build_id="b1", payload_schema_version=2,
        embedding_fingerprint="fp-OTHER", embedding_dimension=1536, point_count=1,
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(v), FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort(),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH in codes


async def test_check_org_unit_coverage_insufficient_emits_warning_not_blocking():
    v = VectorReleaseObservation(
        alias="a", target_collection="c", build_id="b1", payload_schema_version=2,
        embedding_fingerprint="fp-1", embedding_dimension=1536, point_count=1,
        coverage=(PayloadCoverageObservation("org_unit_ids", 0.3, 1),),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(v), FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort(),
    )
    report = await svc.check()
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT in codes
    assert RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE in codes
    # warning-only does NOT block ready
    assert report.ready is True


async def test_check_failure_keeps_old_snapshot():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(_vector_obs()),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    ok = await svc.check()
    assert ok.ready is True
    # now break vector
    svc._deps = ReadinessDeps(
        svc._deps.catalog_port, FakeVectorReleasePort(None),
        svc._deps.graph_port, svc._deps.ranking_port,
    )
    report = await svc.check()
    assert report.ready is False
    assert svc.get_snapshot() is ok.snapshot  # old snapshot preserved


async def test_concurrent_check_serialized_does_not_overwrite_newer():
    # first check succeeds and caches snapshot b1
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs("b1"), [_sample()]),
        FakeVectorReleasePort(_vector_obs("b1")),
        FakeGraphReleasePort(_graph_obs("b1")),
        FakeRankingProfilePort("ranking-v1"),
    )
    await svc.check()
    first = svc.get_snapshot()
    assert first.build_id == "b1"
    # a second concurrent check (newer b2) starts; an older-style late finish
    # cannot overwrite because the Lock serializes
    calls = []

    async def _check_b2():
        calls.append(("b2", "start"))
        await asyncio.sleep(0)  # yield
        report = await svc.check()
        calls.append(("b2", "done"))

    async def _check_b1_again():
        calls.append(("b1", "start"))
        report = await svc.check()
        calls.append(("b1", "done"))

    # both target the same ports (b1); serialization means no interleaving corruption
    await asyncio.gather(_check_b2(), _check_b1_again())
    # both completed
    assert ("b2", "done") in calls and ("b1", "done") in calls
    assert svc.get_snapshot() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'ReadinessDeps'` (or `NotImplementedError`)

- [ ] **Step 3: Implement `ReadinessService` + `ReadinessDeps`**

Replace the `ReadinessService` class and add `ReadinessDeps` in `src/dext_recommend/readiness.py`. Add these imports at the top (after existing ones):

```python
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from dext_recommend.config import RecommendSettings
from dext_recommend.errors import ErrorSeverity, RecommendationError, RecommendationErrorCode
from dext_recommend.ports.release_readback import (
    CatalogReleasePort, GraphReleasePort, RankingProfilePort, VectorReleasePort,
)
from dext_recommend.adapters._readback_call import ReadbackCall, gather_safe
```

Add `ReadinessDeps` and replace the placeholder `ReadinessService`:

```python
@dataclass(frozen=True, slots=True)
class ReadinessDeps:
    catalog_port: CatalogReleasePort
    vector_port: VectorReleasePort
    graph_port: GraphReleasePort
    ranking_port: RankingProfilePort


def _coverage_passes(obs_vector, field, threshold):
    if obs_vector is None or not obs_vector.coverage:
        return True, 1.0
    for cov in obs_vector.coverage:
        if cov.field == field:
            return cov.covered >= threshold, cov.covered
    return True, 1.0


class ReadinessService:
    def __init__(self, deps: ReadinessDeps, settings: RecommendSettings) -> None:
        self._deps = deps
        self._settings = settings
        self._snapshot: ActiveBuildSnapshot | None = None
        self._lock = asyncio.Lock()

    async def check(self) -> ReadinessReport:
        async with self._lock:
            return await self._check_locked()

    async def _check_locked(self) -> ReadinessReport:
        s = self._settings
        phase1 = await gather_safe(
            ReadbackCall("catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                         self._deps.catalog_port.read_active()),
            ReadbackCall("ranking", RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE,
                         self._deps.ranking_port.read_version(s.ranking_profile_path)),
            timeout=s.readiness_readback_timeout,
        )
        catalog_obs, ranking_version = phase1
        errors: list[RecommendationError] = [
            e for e in phase1 if isinstance(e, RecommendationError)
        ]
        catalog_ok = (
            not isinstance(catalog_obs, RecommendationError)
            and catalog_obs is not None
        )
        phase2_results: tuple = ()
        if catalog_ok:
            sample_ids = catalog_obs.sample_entity_ids
            phase2_results = await gather_safe(
                ReadbackCall(
                    "catalog-samples", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                    self._deps.catalog_port.read_samples(catalog_obs.build_id, sample_ids),
                ),
                ReadbackCall(
                    "vector", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                    self._deps.vector_port.read_current(s.qdrant_alias, sample_ids),
                ),
                ReadbackCall(
                    "graph", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                    self._deps.graph_port.read_active(sample_ids),
                ),
                timeout=s.readiness_readback_timeout,
            )
            errors.extend(
                e for e in phase2_results if isinstance(e, RecommendationError)
            )

        ranking_ver = ranking_version if not isinstance(ranking_version, RecommendationError) else None
        new_snapshot, validation_errors, coverage = self._assemble(
            catalog_obs if catalog_ok else None,
            ranking_ver,
            phase2_results,
        )
        errors.extend(validation_errors)

        has_error = any(e.severity is ErrorSeverity.ERROR for e in errors)
        ready = not has_error and new_snapshot is not None
        if ready:
            self._snapshot = new_snapshot
        return ReadinessReport(
            ready=ready,
            snapshot=self._snapshot,
            errors=tuple(errors),
            payload_coverage=coverage,
        )

    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        return self._snapshot

    def _assemble(
        self,
        catalog_obs, ranking_version, phase2_results,
    ) -> tuple[ActiveBuildSnapshot | None, list[RecommendationError], Mapping[str, CoverageStat]]:
        errors: list[RecommendationError] = []
        coverage: dict[str, CoverageStat] = {}

        def _err(code, message, *, severity=ErrorSeverity.ERROR, retryable=False):
            errors.append(RecommendationError(
                code=code, severity=severity, message=message, retryable=retryable,
            ))

        if catalog_obs is None:
            _err(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, "no ACTIVE build")
            return None, errors, coverage

        catalog_samples, vector_obs, graph_obs = self._unpack_phase2(phase2_results)

        # normalize: error objects and None both mean "unavailable"
        if isinstance(vector_obs, RecommendationError) or vector_obs is None:
            _err(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, "vector alias/collection unavailable")
            vector_obs = None
        if isinstance(graph_obs, RecommendationError) or graph_obs is None:
            _err(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, "graph active pointer unavailable")
            graph_obs = None

        build_ids = {catalog_obs.build_id}
        if vector_obs is not None:
            build_ids.add(vector_obs.build_id)
        if graph_obs is not None:
            build_ids.add(graph_obs.build_id)
        if len(build_ids) > 1:
            _err(RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT,
                 f"three-way build id mismatch: {sorted(build_ids)}")

        if vector_obs is not None:
            if vector_obs.embedding_dimension != catalog_obs.embedding_dimension:
                _err(RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
                     "embedding dimension mismatch")
            if vector_obs.embedding_fingerprint != catalog_obs.embedding_fingerprint:
                _err(RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
                     "embedding fingerprint mismatch")

        if vector_obs is not None:
            s = self._settings
            for field, threshold, code in (
                ("org_unit_ids", s.coverage_threshold_org_unit_ids,
                 RecommendationErrorCode.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT),
                ("profile_hash", s.coverage_threshold_profile_hash,
                 RecommendationErrorCode.PROFILE_HASH_COVERAGE_INSUFFICIENT),
                ("role_status", s.coverage_threshold_role_status,
                 RecommendationErrorCode.ROLE_STATUS_COVERAGE_INSUFFICIENT),
                ("eligibility", s.coverage_threshold_eligibility,
                 RecommendationErrorCode.ELIGIBILITY_COVERAGE_INSUFFICIENT),
            ):
                passes, covered = _coverage_passes(vector_obs, field, threshold)
                coverage[field] = CoverageStat(field=field, covered=covered,
                                               sample_size=vector_obs.point_count, passes=passes)
                if not passes:
                    _err(code, f"{field} coverage {covered:.2f} < {threshold}")
                    if field == "org_unit_ids":
                        _err(RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE,
                             "org_unit hard filter unavailable",
                             severity=ErrorSeverity.WARNING)

        if ranking_version is None or isinstance(ranking_version, RecommendationError):
            _err(RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE, "ranking profile unavailable")
            ranking_ver = "unknown"
        else:
            ranking_ver = ranking_version

        has_error = any(e.severity is ErrorSeverity.ERROR for e in errors)
        if has_error:
            return None, errors, coverage

        snapshot = ActiveBuildSnapshot(
            build_id=catalog_obs.build_id,
            catalog_schema_version=catalog_obs.catalog_schema_version,
            neo4j_active_build_id=graph_obs.build_id if graph_obs else "",
            qdrant_alias_target=vector_obs.alias if vector_obs else "",
            qdrant_payload_schema_version=catalog_obs.qdrant_payload_schema_version,
            embedding_provider=catalog_obs.embedding_provider,
            embedding_model=catalog_obs.embedding_model,
            embedding_dimension=catalog_obs.embedding_dimension,
            embedding_fingerprint=catalog_obs.embedding_fingerprint,
            taxonomy_version=catalog_obs.taxonomy_version,
            ranking_profile_version=ranking_ver,
            created_at=catalog_obs.created_at,
        )
        return snapshot, errors, coverage

    @staticmethod
    def _unpack_phase2(phase2_results):
        # phase2 order: catalog_samples, vector, graph
        if not phase2_results:
            return (), None, None
        catalog_samples = phase2_results[0]
        vector_obs = phase2_results[1] if len(phase2_results) > 1 else None
        graph_obs = phase2_results[2] if len(phase2_results) > 2 else None
        if isinstance(catalog_samples, RecommendationError):
            catalog_samples = ()
        if isinstance(vector_obs, RecommendationError):
            pass  # keep the error object so _assemble can detect
        if isinstance(graph_obs, RecommendationError):
            pass
        return catalog_samples, vector_obs, graph_obs
```

Update `__all__` in `readiness.py` to include `ReadinessDeps`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v`
Expected: PASS

- [ ] **Step 5: Run full rec suite + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

```bash
git add src/dext_recommend/readiness.py tests/dext_recommend/test_recommend_readiness_service.py
git commit -m "feat(rec): implement ReadinessService.check() two-phase orchestration"
```

---

## Task 10: Import boundary + adapters `__init__` + final green

**Files:**
- Modify: `src/dext_recommend/adapters/__init__.py`
- Modify: `tests/dext_recommend/test_recommend_import_boundary.py`

**Interfaces:**
- Produces: `dext_recommend.adapters` re-exports adapter classes; import-boundary test asserts `dext_recommend.adapters` subtree does not import `dext_graph.*`.

- [ ] **Step 1: Write the failing test for adapters-subtree boundary**

Add to `tests/dext_recommend/test_recommend_import_boundary.py`:

```python
def test_dext_recommend_adapters_do_not_import_dext_graph():
    importlib.import_module("dext_recommend.adapters")
    importlib.import_module("dext_recommend.adapters._catalog_reader")
    importlib.import_module("dext_recommend.adapters._vector_reader")
    importlib.import_module("dext_recommend.adapters._graph_reader")
    importlib.import_module("dext_recommend.adapters.catalog_release")
    importlib.import_module("dext_recommend.adapters.vector_release")
    importlib.import_module("dext_recommend.adapters.graph_release")
    importlib.import_module("dext_recommend.adapters.ranking_profile")
    assert "dext_graph" not in sys.modules, (
        "dext_recommend.adapters must not import dext_graph"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_import_boundary.py::test_dext_recommend_adapters_do_not_import_dext_graph -v`
Expected: may PASS already (modules don't import dext_graph) — if so, this is a green-from-the-start guard test; commit it as a contract pin.

- [ ] **Step 3: Populate adapters `__init__.py`**

Replace `src/dext_recommend/adapters/__init__.py` with:

```python
"""Concrete read-only adapter implementations for published ACTIVE build artifacts.

Each adapter composes a dialect-seam reader (SQLite/Qdrant/Neo4j) with the
dialect-agnostic mapping layer. Adapters never import dext_graph.
"""
from dext_recommend.adapters.catalog_release import CatalogReleaseAdapter
from dext_recommend.adapters.graph_release import GraphReleaseAdapter
from dext_recommend.adapters.ranking_profile import RankingProfileAdapter
from dext_recommend.adapters.vector_release import VectorReleaseAdapter

__all__ = [
    "CatalogReleaseAdapter",
    "GraphReleaseAdapter",
    "RankingProfileAdapter",
    "VectorReleaseAdapter",
]
```

- [ ] **Step 4: Run full rec suite**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/__init__.py tests/dext_recommend/test_recommend_import_boundary.py
git commit -m "feat(rec): wire R2 adapters + pin import boundary"
```

- [ ] **Step 6: Final full-module verification**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: all green, no skips. This is the R2 acceptance bar per [[dext-rec-r0r1-acceptance-bar]] (module-green, not full-project).
