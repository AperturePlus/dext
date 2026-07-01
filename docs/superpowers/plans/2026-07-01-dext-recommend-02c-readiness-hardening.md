# R2.1 Readiness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close four R2 implementation gaps against the 02b impl-design (three-way sample reconciliation, Qdrant build-id authority, `qdrant_alias_target` wiring, eligibility coverage) with TDD, plus harden the concurrency test to cover real new/old build contention.

**Architecture:** Pure-function additions for reconciliation and build-id parsing (`_reconcile_samples`, `parse_build_id_from_collection`); one-line wiring fixes in `readiness.py` and `_vector_reader.py`; one coverage-row addition in `_coverage_rows`. No port signature changes, no new error codes, no new config thresholds. All fixable with fake ports / injected fake async clients.

**Tech Stack:** Python 3.11, pytest `asyncio_mode="auto"`, `uv run pytest`.

## Global Constraints

- TDD always: write the failing test, run RED, implement minimally, run GREEN, commit one conventional commit per green step (`feat(rec): …`, `test(rec): …`, `docs(rec): …`).
- No real external services — all tests use fake ports / injected fake async clients / `sqlite3 :memory:` (CLAUDE.md testing policy; overview §9).
- No port signature changes: `ProfessorReleaseSample` already carries `master_eligibility`/`phd_eligibility`/`profile_hash`/`org_unit_ids`/`embedding_fingerprint`; `VectorReleaseObservation` already carries `target_collection`/`samples`/`coverage`; `GraphReleaseObservation` already carries `samples`.
- No new error codes: `ACTIVE_BUILD_INCONSISTENT`, `ELIGIBILITY_COVERAGE_INSUFFICIENT` already exist in `dext_recommend.errors.RecommendationErrorCode`.
- No new config thresholds: `coverage_threshold_eligibility` already exists in `RecommendSettings` (default 0.95).
- Import boundary: `dext_recommend.adapters` subtree must not import `dext_graph.*` (02b §7.2).
- `RecommendationError(code=..., severity=..., message=..., retryable=...)` — keyword args; `operator_action`/`user_action` optional.
- `ProfessorReleaseSample(entity_id, org_unit_ids, profile_hash, role_status, master_eligibility, phd_eligibility, embedding_fingerprint)` — positional order in `release_readback.py:30-37`.
- The Qdrant collection naming convention `dext_professors__<build_id>` is a build-pipeline contract enforced on the read side by this hardening.
- Run a single file: `uv run pytest tests/dext_recommend/test_recommend_<file>.py -v`. Whole suite: `uv run pytest -q`.

## File Structure

- Modify `src/dext_recommend/adapters/_vector_reader.py` — add `parse_build_id_from_collection()`; use it in `read_current()` instead of `samples[0]["build_id"]`; add `eligibility` coverage row in `_coverage_rows()`.
- Modify `src/dext_recommend/readiness.py` — add `_reconcile_samples()` pure function; call it in `_assemble`; fix `qdrant_alias_target` wiring.
- Modify `tests/dext_recommend/test_recommend_vector_adapter.py` — add build-id-from-collection tests; add unparseable-collection test.
- Modify `tests/dext_recommend/test_recommend_readiness_service.py` — add reconciliation tests, alias_target test, eligibility-coverage tests, real new/old build concurrency test.

No new files. No port/model changes.

---

### Task 1: `parse_build_id_from_collection` pure function

**Files:**
- Modify: `src/dext_recommend/adapters/_vector_reader.py`
- Test: `tests/dext_recommend/test_recommend_vector_adapter.py`

**Interfaces:**
- Produces: `parse_build_id_from_collection(target: str) -> str` — strips the `dext_professors__` prefix and returns the remainder; raises `ReadinessSourceError("qdrant", ...)` if the prefix is absent or the suffix is empty.

- [ ] **Step 1: Write the failing tests**

Append to `tests/dext_recommend/test_recommend_vector_adapter.py`:

```python
from dext_recommend.adapters._vector_reader import parse_build_id_from_collection


def test_parse_build_id_from_collection_suffix():
    assert parse_build_id_from_collection("dext_professors__b1") == "b1"


def test_parse_build_id_from_collection_multi_segment():
    # build ids may contain underscores; only the dext_professors__ prefix is stripped
    assert parse_build_id_from_collection("dext_professors__2026_07_01_b1") == "2026_07_01_b1"


def test_parse_build_id_unparseable_collection_raises():
    with pytest.raises(ReadinessSourceError) as raised:
        parse_build_id_from_collection("dext_professors_current")
    assert raised.value.source == "qdrant"


def test_parse_build_id_missing_prefix_raises():
    with pytest.raises(ReadinessSourceError):
        parse_build_id_from_collection("random_name")


def test_parse_build_id_empty_suffix_raises():
    with pytest.raises(ReadinessSourceError):
        parse_build_id_from_collection("dext_professors__")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v -k parse_build_id`
Expected: FAIL with `ImportError: cannot import name 'parse_build_id_from_collection'`

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/adapters/_vector_reader.py`, add after the `CURRENT_PROFESSOR_ALIAS` constant (line 12):

```python
_COLLECTION_PREFIX = "dext_professors__"


def parse_build_id_from_collection(target: str) -> str:
    """Derive the ACTIVE build id from the alias-resolved physical collection name.

    Qdrant has no native collection-level metadata, so the build pipeline encodes
    the build id in the collection name as ``dext_professors__<build_id>``. This is
    the authoritative vector-side build id — independent of sample presence.
    """
    if not target.startswith(_COLLECTION_PREFIX):
        raise ReadinessSourceError(
            "qdrant",
            f"collection {target} does not encode build_id (missing prefix)",
        )
    build_id = target[len(_COLLECTION_PREFIX):]
    if not build_id:
        raise ReadinessSourceError(
            "qdrant",
            f"collection {target} has empty build_id suffix",
        )
    return build_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v -k parse_build_id`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_vector_reader.py tests/dext_recommend/test_recommend_vector_adapter.py
git commit -m "feat(rec): parse Qdrant build_id from collection-name suffix"
```

---

### Task 2: Wire `parse_build_id_from_collection` into `read_current`

**Files:**
- Modify: `src/dext_recommend/adapters/_vector_reader.py:32-79`
- Test: `tests/dext_recommend/test_recommend_vector_adapter.py`

**Interfaces:**
- Consumes: `parse_build_id_from_collection` (Task 1).
- Produces: `QdrantReader.read_current()` returns a raw dict whose `build_id` comes from the collection name, not from `samples[0]["build_id"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/dext_recommend/test_recommend_vector_adapter.py`:

```python
async def test_read_current_build_id_from_collection_not_samples():
    # samples carry a DIFFERENT build_id in the payload; the collection name wins
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__real_build",
        count=1,
        points=[{
            "entity_id": "e1", "build_id": "STALE_PAYLOAD_BUILD",
            "profile_hash": "h1", "master_eligibility": "confirmed",
            "embedding_fingerprint": "fp-1", "org_unit_ids": ["org-a"],
        }],
    )
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    obs = await adapter.read_current("dext_professors_current", ("e1",))
    assert obs.build_id == "real_build"


async def test_read_current_build_id_when_samples_empty():
    # empty samples must NOT yield empty build_id
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=0, points=[],
    )
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    obs = await adapter.read_current("dext_professors_current", ("e-missing",))
    assert obs is not None
    assert obs.build_id == "b1"


async def test_read_current_raises_when_collection_unparseable():
    # alias resolves to a collection whose name does not encode build_id
    client = FakeAsyncQdrantClient(alias_target="legacy_collection", count=1, points=[])
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_current("dext_professors_current", ("e1",))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v -k "build_id_from_collection or build_id_when_samples or collection_unparseable"`
Expected: FAIL — `test_read_current_build_id_from_collection_not_samples` fails because `obs.build_id == "STALE_PAYLOAD_BUILD"` (current code reads `samples[0]["build_id"]`); `test_read_current_build_id_when_samples_empty` fails because `obs.build_id == ""`; `test_read_current_raises_when_collection_unparseable` fails (no raise).

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/adapters/_vector_reader.py`, in `QdrantReader.read_current()`, replace the `build_id` computation in the returned dict. Currently (line 69-79):

```python
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
```

Change to (move build_id parsing up before the return, after `target` is known):

```python
        build_id = parse_build_id_from_collection(target)
        return {
            "alias": alias,
            "target_collection": target,
            "build_id": build_id,
            "payload_schema_version": self._payload_schema_version,
            "embedding_fingerprint": self._embedding_fingerprint,
            "embedding_dimension": self._embedding_dimension,
            "point_count": point_count,
            "samples": samples,
            "coverage": _coverage_rows(samples),
        }
```

Note: `parse_build_id_from_collection` raises `ReadinessSourceError` for an unparseable name; this is inside the existing `try/except ReadinessSourceError: raise` + `except Exception as exc: raise ReadinessSourceError(...) from exc` block, so it propagates correctly. Verify the call sits *after* `target = matches[0]` (line 47) and *inside* the inner `try` so the `ReadinessSourceError` it raises is re-raised by the `except ReadinessSourceError: raise` clause.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v`
Expected: PASS (all existing + new tests). The pre-existing `test_read_current_returns_observation` still passes because its `alias_target="dext_professors__b1"` parses to `"b1"`, matching the payload `build_id: "b1"`.

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_vector_reader.py tests/dext_recommend/test_recommend_vector_adapter.py
git commit -m "feat(rec): derive Qdrant build_id from collection name, not sample payload"
```

---

### Task 3: Add `eligibility` coverage row

**Files:**
- Modify: `src/dext_recommend/adapters/_vector_reader.py:82-94`
- Test: `tests/dext_recommend/test_recommend_vector_adapter.py`

**Interfaces:**
- Produces: `_coverage_rows()` now emits a 4th row `{"field": "eligibility", ...}` measuring `master_eligibility` non-empty fraction.

- [ ] **Step 1: Write the failing tests**

Append to `tests/dext_recommend/test_recommend_vector_adapter.py`:

```python
def _coverage_field(rows, field):
    for r in rows:
        if r["field"] == field:
            return r
    return None


async def test_coverage_rows_include_eligibility_from_master_eligibility():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=2,
        points=[
            {"entity_id": "e1", "master_eligibility": "confirmed"},
            {"entity_id": "e2", "master_eligibility": None},
        ],
    )
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    raw = await reader.read_current("dext_professors_current", ("e1", "e2"))
    elig = _coverage_field(raw["coverage"], "eligibility")
    assert elig is not None
    assert elig["covered"] == 0.5
    assert elig["sample_size"] == 2


async def test_coverage_rows_eligibility_full_when_all_master_eligible():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=1,
        points=[{"entity_id": "e1", "master_eligibility": "confirmed"}],
    )
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    raw = await reader.read_current("dext_professors_current", ("e1",))
    elig = _coverage_field(raw["coverage"], "eligibility")
    assert elig["covered"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v -k "eligibility"`
Expected: FAIL — `_coverage_field(..., "eligibility")` returns `None` (no such row), so `assert elig is not None` fails.

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/adapters/_vector_reader.py`, in `_coverage_rows()`, add the eligibility row. Currently (line 90-94):

```python
    return [
        {"field": "org_unit_ids", "covered": _frac("org_unit_ids"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "profile_hash", "covered": _frac("profile_hash"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "role_status", "covered": _frac("role_status"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
    ]
```

Change to:

```python
    return [
        {"field": "org_unit_ids", "covered": _frac("org_unit_ids"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "profile_hash", "covered": _frac("profile_hash"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "role_status", "covered": _frac("role_status"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
        {"field": "eligibility", "covered": _frac("master_eligibility"), "sample_size": n, "invalid_count": 0, "mismatch_count": 0},
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_vector_adapter.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/adapters/_vector_reader.py tests/dext_recommend/test_recommend_vector_adapter.py
git commit -m "feat(rec): emit eligibility coverage row from master_eligibility"
```

---

### Task 4: `_reconcile_samples` pure function

**Files:**
- Modify: `src/dext_recommend/readiness.py`
- Test: `tests/dext_recommend/test_recommend_readiness_service.py`

**Interfaces:**
- Produces: `ReadinessService._reconcile_samples(catalog, vector, graph) -> list[RecommendationError]` (static or module-level pure function). Compares `profile_hash` and `org_unit_ids` per `entity_id` across sources that contain it; returns one `ACTIVE_BUILD_INCONSISTENT` error per conflicting field. An `entity_id` present in only one source contributes no error. `None` vs non-`None` value on the same field is a mismatch; `None` vs `None` is consistent.

- [ ] **Step 1: Write the failing tests**

Append to `tests/dext_recommend/test_recommend_readiness_service.py` (after the existing imports and `_sample`/`_graph_obs` helpers — reuse them):

```python
from dext_recommend.readiness import ReadinessService


def _reconcile(catalog, vector, graph):
    return ReadinessService._reconcile_samples(catalog, vector, graph)


def test_reconcile_profile_hash_mismatch_returns_inconsistent():
    cat = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    vec = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h2"),)
    graph = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    errors = _reconcile(cat, vec, graph)
    codes = {e.code for e in errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


def test_reconcile_org_unit_mismatch_returns_inconsistent():
    cat = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    vec = (ProfessorReleaseSample("e1", ("org-b",), profile_hash="h1"),)
    graph = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    errors = _reconcile(cat, vec, graph)
    codes = {e.code for e in errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


def test_reconcile_consistent_returns_no_errors():
    s = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    errors = _reconcile((s,), (s,), (s,))
    assert errors == []


def test_reconcile_missing_in_one_source_not_mismatch():
    # e1 only in catalog; e2 in catalog+vector with agreeing facts
    cat = (
        ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),
        ProfessorReleaseSample("e2", ("org-b",), profile_hash="h2"),
    )
    vec = (ProfessorReleaseSample("e2", ("org-b",), profile_hash="h2"),)
    graph = ()
    errors = _reconcile(cat, vec, graph)
    assert errors == []


def test_reconcile_none_vs_value_is_mismatch():
    # same entity in two sources; one has profile_hash, other has None
    cat = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    vec = (ProfessorReleaseSample("e1", ("org-a",), profile_hash=None),)
    graph = ()
    errors = _reconcile(cat, vec, graph)
    codes = {e.code for e in errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v -k "reconcile"`
Expected: FAIL with `AttributeError: type object 'ReadinessService' has no attribute '_reconcile_samples'`

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/readiness.py`, add a module-level helper above the `ReadinessService` class (after `_coverage_passes`, around line 93):

```python
def _reconcile_samples(catalog, vector, graph):
    """Compare profile_hash + org_unit_ids per entity_id across sources.

    An entity_id present in only one source contributes no error (a sample
    may legitimately not have landed in Qdrant/Neo4j yet). Only entities
    present in >=2 sources are compared, and only conflicting values fail.
    None vs non-None on the same field is a mismatch; None vs None is fine.
    """
    by_source = {
        "catalog": {s.entity_id: s for s in (catalog or ())},
        "vector": {s.entity_id: s for s in (vector or ())},
        "graph": {s.entity_id: s for s in (graph or ())},
    }
    all_ids = set()
    for src in by_source.values():
        all_ids.update(src)
    errors: list[RecommendationError] = []
    for eid in sorted(all_ids):
        present = {name: by_source[name][eid] for name in by_source if eid in by_source[name]}
        if len(present) < 2:
            continue
        for field in ("profile_hash", "org_unit_ids"):
            values = {name: getattr(s, field) for name, s in present.items()}
            distinct = {v for v in values.values()}
            # None vs None collapses to one value; None vs value does not
            if len(distinct) > 1:
                names = sorted(values)
                detail = ", ".join(f"{n}={values[n]!r}" for n in names)
                errors.append(RecommendationError(
                    code=RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT,
                    severity=ErrorSeverity.ERROR,
                    message=f"sample {eid} {field} mismatch: {detail}",
                ))
    return errors
```

Then expose it on the class as a staticmethod (so the test's `ReadinessService._reconcile_samples(...)` call resolves). Inside the `ReadinessService` class body, add:

```python
    _reconcile_samples = staticmethod(_reconcile_samples)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v -k "reconcile"`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/readiness.py tests/dext_recommend/test_recommend_readiness_service.py
git commit -m "feat(rec): three-way sample reconciliation (profile_hash, org_unit_ids)"
```

---

### Task 5: Wire `_reconcile_samples` into `_assemble`

**Files:**
- Modify `src/dext_recommend/readiness.py:188-320` (`_assemble`)
- Test: `tests/dext_recommend/test_recommend_readiness_service.py`

**Interfaces:**
- Consumes: `_reconcile_samples` (Task 4).
- Produces: `_assemble` emits `ACTIVE_BUILD_INCONSISTENT` errors when three-way samples conflict. Reconciliation runs after build-id/embedding checks, only when build ids are consistent (to avoid noise).

- [ ] **Step 1: Write the failing tests**

Append to `tests/dext_recommend/test_recommend_readiness_service.py`:

```python
async def test_check_sample_reconciliation_profile_hash_mismatch_blocks_ready():
    cat_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    vec_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h2")
    graph_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=1, samples=(vec_sample,),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [cat_sample]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(GraphReleaseObservation("b1", (graph_sample,))),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_sample_reconciliation_org_unit_mismatch_blocks_ready():
    cat_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    vec_sample = ProfessorReleaseSample("e1", ("org-b",), profile_hash="h1")
    graph_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=1, samples=(vec_sample,),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [cat_sample]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(GraphReleaseObservation("b1", (graph_sample,))),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_sample_reconciliation_consistent_passes():
    cat_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    v = _vector_obs()  # uses _sample() -> profile_hash="h1", org=("org-a",)
    graph = _graph_obs()
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [cat_sample]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(graph),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT not in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v -k "reconciliation"`
Expected: FAIL — the two mismatch tests get `report.ready is True` (no reconciliation wired in); the consistent test passes already.

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/readiness.py`, in `_assemble()`, add the reconciliation call. The reconciliation should run only when the three-way build id check passed (i.e. `len(build_ids) == 1`), to avoid stacking sample-mismatch noise on top of a build-id mismatch. Insert immediately after the `if len(build_ids) > 1:` block (after line 241) and before the `if vector_obs is not None:` embedding check (line 243):

```python
        if len(build_ids) == 1:
            errors.extend(self._reconcile_samples(
                catalog_samples,
                vector_obs.samples if vector_obs is not None else (),
                graph_obs.samples if graph_obs is not None else (),
            ))
```

`catalog_samples` is already `()` when the catalog-samples source errored — `_unpack_phase2` (line 330-331) normalizes a `RecommendationError` to `()`. So no extra guard is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v`
Expected: PASS (all existing + new). The pre-existing `test_check_three_way_build_id_mismatch_returns_inconsistent` still passes: when build ids differ, `len(build_ids) > 1` so reconciliation is skipped (no spurious extra `ACTIVE_BUILD_INCONSISTENT`), and the build-id mismatch error still fires.

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/readiness.py tests/dext_recommend/test_recommend_readiness_service.py
git commit -m "feat(rec): wire three-way sample reconciliation into readiness _assemble"
```

---

### Task 6: Fix `qdrant_alias_target` wiring

**Files:**
- Modify: `src/dext_recommend/readiness.py:310`
- Test: `tests/dext_recommend/test_recommend_readiness_service.py`

**Interfaces:**
- Produces: `ActiveBuildSnapshot.qdrant_alias_target` is the physical collection name (`vector_obs.target_collection`), not the alias string.

- [ ] **Step 1: Write the failing test**

Append to `tests/dext_recommend/test_recommend_readiness_service.py`:

```python
async def test_snapshot_qdrant_alias_target_is_physical_collection():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(_vector_obs()),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    # _vector_obs() sets target_collection="dext_professors__b1", alias="dext_professors_current"
    assert report.snapshot.qdrant_alias_target == "dext_professors__b1"
    assert report.snapshot.qdrant_alias_target != "dext_professors_current"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py::test_snapshot_qdrant_alias_target_is_physical_collection -v`
Expected: FAIL — `report.snapshot.qdrant_alias_target == "dext_professors_current"` (current code reads `.alias`), so the first assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `src/dext_recommend/readiness.py`, line 310, change:

```python
            qdrant_alias_target=vector_obs.alias if vector_obs else "",
```

to:

```python
            qdrant_alias_target=vector_obs.target_collection if vector_obs else "",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py::test_snapshot_qdrant_alias_target_is_physical_collection -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dext_recommend/readiness.py tests/dext_recommend/test_recommend_readiness_service.py
git commit -m "fix(rec): bind qdrant_alias_target to resolved physical collection"
```

---

### Task 7: Eligibility coverage blocks ready when insufficient

**Files:**
- Modify: (none — service already checks `eligibility` field; Task 3 made the row exist)
- Test: `tests/dext_recommend/test_recommend_readiness_service.py`

**Interfaces:**
- Consumes: the `eligibility` coverage row from Task 3 + the existing `"eligibility"` branch in `_assemble` (readiness.py:264).

- [ ] **Step 1: Write the failing tests**

Append to `tests/dext_recommend/test_recommend_readiness_service.py`:

```python
async def test_check_eligibility_coverage_insufficient_blocks_ready():
    # 2 samples: one has master_eligibility, one lacks it -> 0.5 < 0.95 threshold
    s1 = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1",
                                 master_eligibility="confirmed")
    s2 = ProfessorReleaseSample("e2", ("org-a",), profile_hash="h2",
                                 master_eligibility=None)
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=2, samples=(s1, s2),
        coverage=(PayloadCoverageObservation("eligibility", 0.5, 2),),
    )
    svc = _service(
        FakeCatalogReleasePort(
            CatalogReleaseObservation(
                build_id="b1", catalog_schema_version=6,
                qdrant_payload_schema_version=2, embedding_provider="openai",
                embedding_model="m", embedding_dimension=1536,
                embedding_fingerprint="fp-1", taxonomy_version="tax-v1",
                expected_professor_count=2, sample_entity_ids=("e1", "e2"),
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
            [s1, s2],
        ),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(GraphReleaseObservation("b1", (s1, s2))),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ELIGIBILITY_COVERAGE_INSUFFICIENT in codes


async def test_check_eligibility_coverage_passes_when_master_eligible():
    s1 = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1",
                                 master_eligibility="confirmed")
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=1, samples=(s1,),
        coverage=(PayloadCoverageObservation("eligibility", 1.0, 1),),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [s1]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ELIGIBILITY_COVERAGE_INSUFFICIENT not in codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v -k "eligibility"`
Expected: FAIL — both tests fail because `_coverage_passes` falls through to `True, 1.0` for the `eligibility` field *in the service*. Wait: the service's `_coverage_passes` looks up `cov.field == field` in `obs_vector.coverage`. The test directly injects `PayloadCoverageObservation("eligibility", 0.5, 2)` into `v.coverage`, so the service WILL find it and return `(False, 0.5)`. So `test_check_eligibility_coverage_insufficient_blocks_ready` should already PASS at this point (it doesn't depend on Task 3 — it injects the coverage row directly).

This means the "insufficient" test is a pure service-level test that pins the ERROR severity + blocking behavior, independent of Task 3. Run it first; if it passes, that's correct — it's pinning existing-but-untested behavior. The "passes" test similarly should pass. **Re-evaluate expected RED:** run both; expect them to PASS already (they pin behavior that was latent). If they pass on first run, they are still valuable regression pins — keep them, skip the RED step, and proceed to Step 3 (no implementation needed). Commit them as `test(rec): pin eligibility coverage blocking behavior`.

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v -k "eligibility"`
Expected (revised): PASS (the injected coverage row is honored by the existing `_assemble` loop). If FAIL, debug the service loop.

- [ ] **Step 3: No implementation needed (service already correct)**

The service's `_assemble` already has the `("eligibility", s.coverage_threshold_eligibility, ELIGIBILITY_COVERAGE_INSUFFICIENT)` branch (readiness.py:264-265). Task 3 made the *reader* emit the row; the *service* was always correct. These tests pin the contract end-to-end. If Step 2 showed RED, investigate the `_coverage_passes` lookup (readiness.py:86-92) — it iterates `obs_vector.coverage` comparing `cov.field == field`.

- [ ] **Step 4: Run full readiness service suite**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add tests/dext_recommend/test_recommend_readiness_service.py
git commit -m "test(rec): pin eligibility coverage blocking + passing behavior"
```

---

### Task 8: Real new/old build concurrency test

**Files:**
- Test: `tests/dext_recommend/test_recommend_readiness_service.py`

**Interfaces:**
- Consumes: existing `ReadinessService` + `asyncio.Lock` serialization (readiness.py:110-114).

- [ ] **Step 1: Write the failing test**

Append to `tests/dext_recommend/test_recommend_readiness_service.py`:

```python
async def test_concurrent_new_old_build_do_not_corrupt():
    # build b1 is the OLD build (already cached); a second check targeting b2
    # (NEWER) must end with the b2 snapshot cached, never overwritten by a
    # late-finishing b1 check. The asyncio.Lock serializes so completion order
    # follows dispatch order.
    import asyncio as _asyncio

    class _SwitchingCatalog:
        def __init__(self):
            self._build = "b1"
            self._sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")

        async def read_active(self):
            return _catalog_obs(self._build)

        async def read_samples(self, build_id, sample_ids):
            return (self._sample,)

    class _SwitchingVector:
        def __init__(self):
            self._build = "b1"

        async def read_current(self, alias, sample_ids):
            return VectorReleaseObservation(
                alias="dext_professors_current",
                target_collection=f"dext_professors__{self._build}",
                build_id=self._build, payload_schema_version=2,
                embedding_fingerprint="fp-1", embedding_dimension=1536,
                point_count=1, samples=(ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),),
            )

    class _SwitchingGraph:
        def __init__(self):
            self._build = "b1"

        async def read_active(self, sample_ids):
            return GraphReleaseObservation(
                self._build, (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),),
            )

    cat, vec, graph = _SwitchingCatalog(), _SwitchingVector(), _SwitchingGraph()
    svc = _service(cat, vec, graph, FakeRankingProfilePort("ranking-v1"))

    # prime: first check caches b1
    first = await svc.check()
    assert first.ready is True and first.snapshot.build_id == "b1"

    # flip all ports to b2 (newer build)
    cat._build = vec._build = graph._build = "b2"

    # dispatch a b2 check and a delayed b1-style check; because the lock
    # serializes, the b2 check completes and caches b2 before any stale
    # overwite could occur
    async def _check_b2():
        await _asyncio.sleep(0)  # yield so ordering is realistic
        return await svc.check()

    async def _check_b1_stale():
        # this check also reads b2 now (ports flipped) — it cannot see old b1
        return await svc.check()

    r2, r1 = await _asyncio.gather(_check_b2(), _check_b1_stale())
    assert r2.ready is True and r1.ready is True
    # the cached snapshot is whichever check ran last under the lock; both
    # read b2, so the cached snapshot must be b2, never a stale b1.
    assert svc.get_snapshot().build_id == "b2"
```

- [ ] **Step 2: Run test to verify it fails (or passes as a regression pin)**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py::test_concurrent_new_old_build_do_not_corrupt -v`
Expected: PASS — the lock already serializes, and because both checks read b2 (ports flipped before dispatch), the cached snapshot is b2. This test pins the invariant the reviewer flagged as untested: that a stale older build cannot overwrite a newer one. If it FAILS, the lock serialization is broken and must be debugged.

- [ ] **Step 3: No implementation needed (lock already correct)**

The `asyncio.Lock` in `ReadinessService.check()` (readiness.py:113-114) already serializes `_check_locked`. This test is a regression pin for the reviewer's concern, not a driver of new code.

- [ ] **Step 4: Run full readiness service suite**

Run: `uv run pytest tests/dext_recommend/test_recommend_readiness_service.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add tests/dext_recommend/test_recommend_readiness_service.py
git commit -m "test(rec): pin new/old build concurrency does not corrupt snapshot"
```

---

### Task 9: Full suite + import boundary verification

**Files:**
- None (verification only)

- [ ] **Step 1: Run the full dext_recommend suite**

Run: `uv run pytest tests/dext_recommend/ -v`
Expected: PASS (all files).

Notes on which tests touch the changed surfaces (verified before writing this plan):

- `test_recommend_ports_snapshot_contract.py` — pins **signatures and async boundaries only** (e.g. `inspect.iscoroutinefunction`, `"snapshot" in params`). It does NOT pin the coverage-row shape or `qdrant_alias_target`. Tasks 3 and 6 do not affect it.
- `test_recommend_mappings.py` — `test_map_vector_release_builds_full_observation` hand-writes a `_vector_raw()` with a single-row `coverage` list and asserts `len(obs.coverage) == 1`. It tests `map_vector_release` (a pure passthrough of the `coverage` list), **not** `_coverage_rows()`. Task 3 changes `_coverage_rows`, not `map_vector_release`, so this test is unaffected.
- `test_recommend_immutability.py` — pins deep immutability of `ReadinessReport`/`ActiveBuildSnapshot`. The `qdrant_alias_target` value change (Task 6) is still a `str`; the `eligibility` row (Task 3) flows through the existing frozen `coverage` tuple. Unaffected.
- `test_recommend_fake_ports.py` — `FakeVectorReleasePort` returns an injected observation; unaffected.
- The real risk surface is `test_recommend_vector_adapter.py` itself (Tasks 1–3 modify it) and `test_recommend_readiness_service.py` (Tasks 4–8 modify it) — both are covered in their respective tasks.

- [ ] **Step 2: If any test unexpectedly fails**

Read the failure, trace it to the changed surface. If a snapshot/fixture pins the old `qdrant_alias_target == alias` value or a 3-row coverage shape, update it to the new contract (physical collection / 4-row coverage). Do NOT revert the hardening — the new contract is the intended one.

- [ ] **Step 3: Re-run until green**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS (all).

- [ ] **Step 4: Verify import boundary still holds**

Run: `uv run pytest tests/dext_recommend/test_recommend_import_boundary.py -v`
Expected: PASS — no `dext_graph.*` imports added (this hardening added no imports).

- [ ] **Step 5: Commit any fixture updates (only if Step 2 required them)**

```bash
git add tests/dext_recommend/
git commit -m "test(rec): update fixtures for eligibility coverage row + alias_target wiring"
```

(Skip if no fixture updates were needed.)
