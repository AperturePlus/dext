# dext_recommend R3 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix six+one defect areas in the R3 recommend core (adaptive recall, same_field semantics, org_unit degradation, config wiring, explanation non-empty, request-local resilience, request/permission boundaries) so R3 meets its own acceptance bar without leaving the fake-port boundary.

**Architecture:** TDD deltas against the existing R3 core in `src/dext_recommend/core/`. Seven workstreams land in dependency order W3→W4a→W2→W1→W4b→W5→W7→W6-a→W6-b. No live adapters; `assemble_core`/`build_test_core` are pure injection seams. Each request gets a fresh `RecommendExecutionContext`; `RecommendationCore` stays stateless.

**Tech Stack:** Python 3.11, asyncio, dataclasses (frozen+slots), pydantic-settings, pytest (`asyncio_mode="auto"`), `uv` runner. Source of truth: `docs/superpowers/specs/2026-07-02-dext-recommend-03c-r3-hardening-design.md`.

## Global Constraints

- **Run tests:** `uv run pytest tests/dext_recommend/ -q` (single module green is the bar; do NOT require full-repo green).
- **One test at a time:** `uv run pytest tests/dext_recommend/test_<name>.py::test_name -v`.
- **TDD strict:** write failing test → run RED → implement minimal → run GREEN → commit one conventional commit (`fix(rec): …` / `feat(rec): …` / `test(rec): …` / `docs(rec): …`).
- **No live LLM/Qdrant/Neo4j:** all tests use the existing fake ports in `src/dext_recommend/ports/_fakes.py` and `tests/dext_recommend/_recfixtures.py`. Never downgrade to a mock or skip the key.
- **Import DAG:** `models.py` must NOT import `dext_recommend.core.*`. Diagnostic DTOs live in `models.py`; `core/_resilience.py` only consumes them.
- **Immutability:** all DTOs are `@dataclass(frozen=True, slots=True)`; mappings frozen via `freeze_mapping`; request-local context is the only mutable dataclass.
- **Branch:** work on `next1` (current). Do not create a new branch unless asked.
- **Commit signing off:** use `git -c commit.gpgsign=false commit -m "..."` to avoid gpg prompts.
- **Platform:** Windows + Git Bash. `LF will be replaced by CRLF` warnings are benign.

---

## File Structure

**Modified (src/dext_recommend/):**
- `core/filters.py` — W3: `payload_prefilter` gains `org_unit_degraded` kw; `final_filter` degradation rule.
- `core/rerank.py` — W2: replace `_anchor_boost` with `_same_field_affinity`; add `anchor_topics` param; configurable `tie_break` (W4b); two new score_components keys.
- `core/ranking_profile.py` — W2: add `same_field_boost_per_topic`/`same_field_boost_max` fields + validation; W4b: tighten `tie_break` validation to 4-field complete-permutation.
- `core/recall.py` — W1: rewrite `recall_loop` to per-step filter + `RecallResult` + fact cache; pass `rrf_k`/`sparse_vector` (W4a).
- `core/service.py` — W2: anchor resolution; W1: shrink orchestration; W6: `_guarded` integration, request-local ctx, timeout; W7: request validation, viewer permissions.
- `core/explanation.py` — W5: non-empty `short_reasons` when detail None.
- `core/detail_fetch.py` — W6: per-entity `DETAILS_UNAVAILABLE` degradation, phase timing.
- `core/validation.py` — W6/W7: allow `phase_diagnostics`; validate `INVALID_REQUEST`/`UNAUTHORIZED_REVIEW` paths.
- `ports/vector_search.py` — W4a: `hybrid_recall` signature (required `rrf_k`, optional `sparse_vector`).
- `ports/_fakes.py` — W4a: `FakeVectorSearchPort.hybrid_recall` new signature + call recording.
- `models.py` — W4b: `QueryDiagnostics.steps_used`; W6: `PhaseDiagnostic` + `RecommendResponse.phase_diagnostics`; W7: `RecommendRequest.__post_init__` validation.
- `errors.py` — W6/W7: new codes (`REQUEST_TIMEOUT`, `LLM_UNAVAILABLE`, `EMBEDDING_UNAVAILABLE`, `VECTOR_UNAVAILABLE`, `HYDRATE_UNAVAILABLE`, `DETAILS_UNAVAILABLE`, `INVALID_REQUEST`, `UNAUTHORIZED_REVIEW`).
- `config.py` — W7: `total_timeout` `gt=0`; add `query_max_chars`, `limit_max`.
- `adapters/ranking_profile.py` — W6-a: add `read_profile` with robust error normalization.
- `__init__.py` / `core/__init__.py` / `ports/__init__.py` — re-export new public names.

**Created (src/dext_recommend/):**
- `core/_resilience.py` — W6: `RecommendExecutionContext`, `PhaseDiagnostic` consumer, `_guarded_async`/`_guarded_sync`, `ClassifiedRecommendError`.
- `composition.py` — W6-a: `assemble_core`/`build_test_core` seams.

**Created (data/):**
- `data/recommend/ranking-profile.json` — W6-a: default profile.

**Modified (tests/dext_recommend/):**
- `_recfixtures.py` — add `same_field_boost_*` to `ranking_profile_dict`; add anchor fixtures; add concurrent-isolation helper.
- Various `test_recommend_*.py` — rewrite bug-pin tests, add new acceptance tests (see §6.1 of spec).

---

## Task 1: W3 — org_unit degradation chain

**Files:**
- Modify: `src/dext_recommend/core/filters.py`
- Modify: `src/dext_recommend/core/service.py` (compute `org_unit_degraded` once, pass to both stages; tighten warning)
- Test: `tests/dext_recommend/test_recommend_filters.py`, `tests/dext_recommend/test_recommend_core.py`

**Interfaces:**
- Produces: `payload_prefilter(hits, filters, *, org_unit_degraded: bool = False) -> list[VectorHit]`; `final_filter` unchanged signature but reads new `org_unit_degraded` rule; `FilterDiagnostics.org_unit_degraded` unchanged.

- [ ] **Step 1: Write failing test — payload_prefilter skips org_unit when degraded**

Add to `tests/dext_recommend/test_recommend_filters.py`:

```python
def test_payload_prefilter_skips_org_unit_when_degraded():
    from dext_recommend.core.filters import payload_prefilter
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports.vector_search import VectorHit

    hits = [
        VectorHit(entity_id="e1", score=0.9, payload={"org_unit_ids": ["ou_cs"]}),
        VectorHit(entity_id="e2", score=0.8, payload={"org_unit_ids": ["ou_math"]}),
    ]
    filters = RecommendationFilters(org_unit_ids=("ou_cs",))
    out = payload_prefilter(hits, filters, org_unit_degraded=True)
    assert {h.entity_id for h in out} == {"e1", "e2"}


def test_payload_prefilter_keeps_org_unit_when_not_degraded():
    from dext_recommend.core.filters import payload_prefilter
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports.vector_search import VectorHit

    hits = [
        VectorHit(entity_id="e1", score=0.9, payload={"org_unit_ids": ["ou_cs"]}),
        VectorHit(entity_id="e2", score=0.8, payload={"org_unit_ids": ["ou_math"]}),
    ]
    filters = RecommendationFilters(org_unit_ids=("ou_cs",))
    out = payload_prefilter(hits, filters, org_unit_degraded=False)
    assert {h.entity_id for h in out} == {"e1"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_filters.py::test_payload_prefilter_skips_org_unit_when_degraded tests/dext_recommend/test_recommend_filters.py::test_payload_prefilter_keeps_org_unit_when_not_degraded -v`
Expected: FAIL — `payload_prefilter()` does not accept `org_unit_degraded`.

- [ ] **Step 3: Implement — payload_prefilter gains org_unit_degraded kw**

In `src/dext_recommend/core/filters.py`, replace the `payload_prefilter` function:

```python
def payload_prefilter(
    hits: list[VectorHit] | tuple[VectorHit, ...],
    filters: RecommendationFilters,
    *,
    org_unit_degraded: bool = False,
) -> list[VectorHit]:
    out: list[VectorHit] = []
    for h in hits:
        keep = True
        for key, req in (
            ("university_id", filters.university_ids),
            ("city_name", filters.city_names),
            ("title_family", filters.title_families),
        ):
            m = _payload_match(h.payload, key, tuple(req))
            if m is False:
                keep = False
                break
        if keep and not org_unit_degraded:
            m = _payload_match(h.payload, "org_unit_ids", tuple(filters.org_unit_ids))
            if m is False:
                keep = False
        if filters.master_eligibility == "confirmed":
            m = _payload_match(h.payload, "master_eligibility", ("confirmed",))
            if m is False:
                keep = False
        if filters.phd_eligibility == "confirmed":
            m = _payload_match(h.payload, "phd_eligibility", ("confirmed",))
            if m is False:
                keep = False
        if keep:
            out.append(h)
    return out
```

(Note: `org_unit_ids` moved out of the loop tuple and is gated by `not org_unit_degraded`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_filters.py::test_payload_prefilter_skips_org_unit_when_degraded tests/dext_recommend/test_recommend_filters.py::test_payload_prefilter_keeps_org_unit_when_not_degraded -v`
Expected: PASS.

- [ ] **Step 5: Write failing test — final_filter degraded rule (missing=False=degrade)**

Add to `tests/dext_recommend/test_recommend_filters.py`:

```python
def test_final_filter_org_unit_degraded_when_flag_missing():
    from dext_recommend.core.filters import final_filter, FilterDiagnostics
    from dext_recommend.models import RecommendationFilters, RecommendationWarning
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact

    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    fact = ProfessorFact(
        entity_id="e1", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, org_unit_ids=("ou_math",),
    )
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    # coverage_flags missing the key entirely -> degraded
    survivors, diag = final_filter(
        hits, {"e1": fact}, RecommendationFilters(org_unit_ids=("ou_cs",)),
        route, coverage_flags={}, review_policy="exclude",
    )
    assert diag.org_unit_degraded is True
    assert len(survivors) == 1  # not hard-filtered on org_unit


def test_final_filter_org_unit_not_degraded_when_flag_true():
    from dext_recommend.core.filters import final_filter
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact

    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    fact = ProfessorFact(
        entity_id="e1", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, org_unit_ids=("ou_math",),
    )
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    survivors, diag = final_filter(
        hits, {"e1": fact}, RecommendationFilters(org_unit_ids=("ou_cs",)),
        route, coverage_flags={"org_unit_ids": True}, review_policy="exclude",
    )
    assert diag.org_unit_degraded is False
    assert len(survivors) == 0  # hard-filtered: org_unit mismatch
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_filters.py::test_final_filter_org_unit_degraded_when_flag_missing tests/dext_recommend/test_recommend_filters.py::test_final_filter_org_unit_not_degraded_when_flag_true -v`
Expected: first FAILs (missing flag treated as not-degraded → survivor count 0); second may pass already.

- [ ] **Step 7: Implement — final_filter degraded rule**

In `src/dext_recommend/core/filters.py`, replace line `org_unit_degraded = not bool(coverage_flags.get("org_unit_ids", True))` with:

```python
org_unit_degraded = coverage_flags.get("org_unit_ids") is not True
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_filters.py::test_final_filter_org_unit_degraded_when_flag_missing tests/dext_recommend/test_recommend_filters.py::test_final_filter_org_unit_not_degraded_when_flag_true -v`
Expected: PASS.

- [ ] **Step 9: Write failing test — warning silent when org_unit not requested**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_org_unit_degraded_silent_when_not_requested():
    """org_unit coverage missing but filters.org_unit_ids empty -> no warning."""
    from tests.dext_recommend._recfixtures import (
        snapshot, ranking_profile_dict, recommend_core_case, vector_hits_case,
        professor_facts_case, professor_details_case, fake_llm_for_understanding,
        coverage_flags_case,
    )
    prof = recommend_core_case(
        ranking_port_profile=ranking_profile_dict(),
        coverage_flags=coverage_flags_case("b-1", org_unit_ids=None),
    )
    # request has NO org_unit_ids filter
    from dext_recommend.models import RecommendRequest, RecommendationFilters
    req = RecommendRequest(
        query_text="computer vision", filters=RecommendationFilters(),
        oversample=200, limit=5,
    )
    resp = await prof.recommend(req)
    codes = [w.code for w in resp.warnings]
    assert "org_unit_filter_unavailable" not in codes
```

(If `recommend_core_case` is not the exact factory name in `_recfixtures.py`, substitute the actual one used by existing happy-path tests — read `tests/dext_recommend/test_recommend_core.py` for the real construction pattern and mirror it.)

- [ ] **Step 10: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_org_unit_degraded_silent_when_not_requested -v`
Expected: FAIL — warning still emitted unconditionally.

- [ ] **Step 11: Implement — tighten warning emission in service.py**

In `src/dext_recommend/core/service.py`, find the warning block (around line 215):

```python
if filter_diag.org_unit_degraded:
    warnings.append(_warn(RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE,
                          "org_unit hard filter degraded (coverage unavailable)"))
```

Replace with:

```python
if filter_diag.org_unit_degraded and effective_filters.org_unit_ids:
    warnings.append(_warn(RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE,
                          "org_unit hard filter degraded (coverage unavailable)"))
```

Also: compute `org_unit_degraded` once and pass it to the `payload_prefilter` call site. Search for `payload_prefilter(` in service.py; if it's called directly, pass `org_unit_degraded=`. (At this task stage recall_loop hasn't been rewritten yet — service still calls `payload_prefilter` directly. Pass the flag through.) Compute it near where `coverage_flags` is read:

```python
coverage_flags = self._deps.coverage_flags_by_build_id.get(snapshot.build_id, {})
org_unit_degraded = coverage_flags.get("org_unit_ids") is not True
```

- [ ] **Step 12: Run the new test + full filters/core tests**

Run: `uv run pytest tests/dext_recommend/test_recommend_filters.py tests/dext_recommend/test_recommend_core.py -q`
Expected: PASS (new tests pass; existing tests stay green — the existing `test_recommend_org_unit_degraded` test requests org_unit_ids so it still gets the warning).

- [ ] **Step 13: Add regression test — enforced when coverage ok**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_org_unit_enforced_when_coverage_ok():
    """flag True + org_unit requested -> mismatching candidate filtered (no over-softening)."""
    # mirror the existing org_unit_degraded test construction but with
    # coverage_flags_case("b-1", org_unit_ids=True) and assert the
    # mismatching candidate is absent from results.
    # See existing test_recommend_org_unit_degraded for the fixture pattern.
    ...
```

(Implement the body by mirroring the nearest existing org_unit test in the file; the assertion is that a candidate whose fact `org_unit_ids` doesn't match is NOT in `resp.results`.)

- [ ] **Step 14: Run + commit**

Run: `uv run pytest tests/dext_recommend/test_recommend_filters.py tests/dext_recommend/test_recommend_core.py -q`
Expected: PASS.

```bash
git add src/dext_recommend/core/filters.py src/dext_recommend/core/service.py tests/dext_recommend/test_recommend_filters.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "fix(rec): org_unit degradation — missing flag degrades both filter stages, warning gated on request"
```

---

## Task 2: W4a — hybrid_recall port contract (required rrf_k, optional sparse_vector)

**Files:**
- Modify: `src/dext_recommend/ports/vector_search.py` (protocol)
- Modify: `src/dext_recommend/ports/_fakes.py` (`FakeVectorSearchPort.hybrid_recall`)
- Test: `tests/dext_recommend/test_recommend_fake_ports.py`

**Interfaces:**
- Produces: `VectorSearchPort.hybrid_recall(snapshot, query_vector, filters, oversample, profile_version, *, rrf_k: int, sparse_vector: Mapping | None = None) -> list[VectorHit]`. `rrf_k` is required (intentional breaking change); `sparse_vector` optional with default None.

- [ ] **Step 1: Write failing test — fake records rrf_k and sparse_vector**

Add to `tests/dext_recommend/test_recommend_fake_ports.py`:

```python
async def test_fake_vector_hybrid_recall_records_rrf_k_and_sparse_vector():
    from tests.dext_recommend._recfixtures import snapshot, vector_hits_case
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports._fakes import FakeVectorSearchPort

    port = FakeVectorSearchPort(hits=vector_hits_case("basic"))
    snap = snapshot()
    await port.hybrid_recall(
        snap, [0.1, 0.2], RecommendationFilters(), 200, "r1",
        rrf_k=42, sparse_vector={"indices": [0, 1], "values": [0.5, 0.5]},
    )
    call = port.hybrid_recall_calls[-1]
    assert call["rrf_k"] == 42
    assert call["sparse_vector"] == {"indices": [0, 1], "values": [0.5, 0.5]}
```

(Use the actual `vector_hits_case` factory name from `_recfixtures.py`; if the "basic" case name differs, read `_recfixtures.py` and use the real one.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_fake_ports.py::test_fake_vector_hybrid_recall_records_rrf_k_and_sparse_vector -v`
Expected: FAIL — `hybrid_recall()` does not accept `rrf_k`.

- [ ] **Step 3: Implement — protocol + fake signature**

In `src/dext_recommend/ports/vector_search.py`, replace the `hybrid_recall` protocol method:

```python
@runtime_checkable
class VectorSearchPort(Protocol):
    async def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters,
        oversample: int,
        profile_version: str,
        *,
        rrf_k: int,
        sparse_vector: Mapping | None = None,
    ) -> list[VectorHit]: ...

    async def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback: ...

    async def count_readback(
        self, snapshot: ActiveBuildSnapshot, filter: dict | None = None,
    ) -> int: ...
```

(`Mapping` is already imported from `collections.abc` at the top of the file.)

In `src/dext_recommend/ports/_fakes.py`, replace `FakeVectorSearchPort.hybrid_recall`:

```python
    async def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters | None,
        oversample: int,
        profile_version: str,
        *,
        rrf_k: int,
        sparse_vector: Mapping | None = None,
    ) -> list[VectorHit]:
        self.hybrid_recall_calls.append({
            "oversample": oversample,
            "filters": filters,
            "profile_version": profile_version,
            "snapshot_build_id": snapshot.build_id,
            "rrf_k": rrf_k,
            "sparse_vector": dict(sparse_vector) if sparse_vector else None,
        })
        return list(self._hits)
```

(Add `from collections.abc import Mapping` to `_fakes.py` imports if not present.)

- [ ] **Step 4: Run the new test**

Run: `uv run pytest tests/dext_recommend/test_recommend_fake_ports.py::test_fake_vector_hybrid_recall_records_rrf_k_and_sparse_vector -v`
Expected: PASS.

- [ ] **Step 5: Fix all existing callers of hybrid_recall**

Search for every `hybrid_recall` call in `src/dext_recommend/` and update it to pass `rrf_k=profile.rrf_k` (and `sparse_vector=...` where available). At this stage the main caller is `core/recall.py:56`. Update it:

```python
hits = await vector_port.hybrid_recall(
    snapshot, query_vector, filters, step, profile.version,
    rrf_k=profile.rrf_k,
)
```

(`sparse_vector` plumbing into recall_loop lands in Task 4/W1; for now just satisfy the required kw.)

- [ ] **Step 6: Run full recommend test module**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS (all existing tests green with the new required kw; no test was asserting the old 5-arg signature in a way that breaks).

- [ ] **Step 7: Commit**

```bash
git add src/dext_recommend/ports/vector_search.py src/dext_recommend/ports/_fakes.py src/dext_recommend/core/recall.py tests/dext_recommend/test_recommend_fake_ports.py
git -c commit.gpgsign=false commit -m "feat(rec): hybrid_recall port contract — required rrf_k, optional sparse_vector"
```

---

## Task 3: W2 — same_field semantics

**Files:**
- Modify: `src/dext_recommend/core/ranking_profile.py` (add boost fields + validation)
- Modify: `src/dext_recommend/core/rerank.py` (replace `_anchor_boost`, add `_same_field_affinity`, `anchor_topics` param, two new components)
- Modify: `src/dext_recommend/core/service.py` (anchor resolution via `hydrate`)
- Modify: `tests/dext_recommend/_recfixtures.py` (`ranking_profile_dict` gains boost fields)
- Test: `tests/dext_recommend/test_recommend_rerank.py`, `tests/dext_recommend/test_recommend_ranking_profile.py`, `tests/dext_recommend/test_recommend_core.py`

**Interfaces:**
- Produces: `rerank(window, fact_map, detail_map, semantic_scores, student_context, profile, route, *, query_terms=(), anchor_topics=()) -> list[RerankEntry]`; `RerankEntry.score_components` always contains keys `semantic_score`, `topic_statement_score`, `student_fit_score`, `eligibility_score`, `provenance_score`, `completeness_score`, `same_field_overlap`, `same_field_boost`.

- [ ] **Step 1: Write failing test — RankingProfile validates boost fields**

Add to `tests/dext_recommend/test_recommend_ranking_profile.py`:

```python
def test_ranking_profile_validates_same_field_boost_fields():
    from dext_recommend.core.ranking_profile import RankingProfile

    base = dict(
        version="r1", weights={"semantic_score": 0.50, "topic_statement_score": 0.18,
            "student_fit_score": 0.12, "eligibility_score": 0.08,
            "provenance_score": 0.08, "completeness_score": 0.04},
        rrf_k=60, oversample_steps=(200, 400), detail_rerank_window=50,
        detail_fetch_concurrency=8, detail_rerank_window_max=100,
        match_level_thresholds={"excellent": 0.75, "strong": 0.55, "possible": 0.35},
        tie_break=("score", "semantic_score", "evidence_count", "entity_id"),
        same_field_boost_per_topic=0.05, same_field_boost_max=0.15,
    )
    RankingProfile.from_dict(base)  # ok

    bad = dict(base, same_field_boost_per_topic=0.3, same_field_boost_max=0.15)
    try:
        RankingProfile.from_dict(bad)
        assert False, "per_topic > max should raise"
    except ValueError:
        pass

    bad2 = dict(base, same_field_boost_max=1.5)
    try:
        RankingProfile.from_dict(bad2)
        assert False, "max > 1 should raise"
    except ValueError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile.py::test_ranking_profile_validates_same_field_boost_fields -v`
Expected: FAIL — `from_dict` passes `same_field_boost_*` as unexpected kwargs or the fields don't exist.

- [ ] **Step 3: Implement — RankingProfile boost fields + validation**

In `src/dext_recommend/core/ranking_profile.py`, add two fields to the `RankingProfile` dataclass (after `tie_break`):

```python
    same_field_boost_per_topic: float
    same_field_boost_max: float
```

In `__post_init__`, add (before the `object.__setattr__` calls):

```python
        if not (0.0 <= self.same_field_boost_per_topic <= self.same_field_boost_max <= 1.0):
            raise ValueError(
                "require 0 <= same_field_boost_per_topic <= same_field_boost_max <= 1"
            )
```

In `from_dict`, add:

```python
            same_field_boost_per_topic=d["same_field_boost_per_topic"],
            same_field_boost_max=d["same_field_boost_max"],
```

- [ ] **Step 4: Update _recfixtures.ranking_profile_dict**

In `tests/dext_recommend/_recfixtures.py`, add to the `ranking_profile_dict` function signature and body:

```python
def ranking_profile_dict(
    *, version: str = "r1",
    ...
    tie_break: tuple[str, ...] = ("score", "semantic_score", "evidence_count", "entity_id"),
    same_field_boost_per_topic: float = 0.05,
    same_field_boost_max: float = 0.15,
) -> dict:
    return {
        ...
        "tie_break": tie_break,
        "same_field_boost_per_topic": same_field_boost_per_topic,
        "same_field_boost_max": same_field_boost_max,
    }
```

- [ ] **Step 5: Run ranking profile test + existing tests**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile.py -q`
Expected: PASS.

- [ ] **Step 6: Write failing test — rerank same_field affinity**

Add to `tests/dext_recommend/test_recommend_rerank.py`:

```python
def test_rerank_same_field_boosts_topic_overlap():
    from dext_recommend.core.rerank import rerank
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact
    from tests.dext_recommend._recfixtures import ranking_profile_dict

    profile = RankingProfile.from_dict(ranking_profile_dict())
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    # two candidates, identical semantic; one shares anchor topic
    hits = [
        VectorHit(entity_id="e_share", score=0.9, payload={}),
        VectorHit(entity_id="e_other", score=0.9, payload={}),
    ]
    fact_share = ProfessorFact(
        entity_id="e_share", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, topic_ids=("topic_nlp",),
    )
    fact_other = ProfessorFact(
        entity_id="e_other", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, topic_ids=("topic_cv",),
    )
    fact_map = {"e_share": fact_share, "e_other": fact_other}
    semantic = {"e_share": 1.0, "e_other": 1.0}
    ranked = rerank(
        hits, fact_map, {}, semantic, None, profile, route,
        anchor_topics=("topic_nlp",),
    )
    assert ranked[0].entity_id == "e_share"
    assert ranked[0].score_components["same_field_overlap"] > 0.0
    assert ranked[0].score_components["same_field_boost"] > 0.0
    assert ranked[1].score_components["same_field_overlap"] == 0.0
    assert ranked[1].score_components["same_field_boost"] == 0.0


def test_rerank_same_field_components_always_present():
    """Even with no anchor_topics, both same_field_* keys exist and are 0.0."""
    from dext_recommend.core.rerank import rerank
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from tests.dext_recommend._recfixtures import ranking_profile_dict

    profile = RankingProfile.from_dict(ranking_profile_dict())
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    hits = [VectorHit(entity_id="e1", score=0.5, payload={})]
    ranked = rerank(hits, {}, {}, {"e1": 1.0}, None, profile, route)
    assert "same_field_overlap" in ranked[0].score_components
    assert "same_field_boost" in ranked[0].score_components
    assert ranked[0].score_components["same_field_overlap"] == 0.0
    assert ranked[0].score_components["same_field_boost"] == 0.0
```

- [ ] **Step 7: Run tests to verify they fail**

Run: `uv run pytest tests/dext_recommend/test_recommend_rerank.py::test_rerank_same_field_boosts_topic_overlap tests/dext_recommend/test_recommend_rerank.py::test_rerank_same_field_components_always_present -v`
Expected: FAIL — `rerank()` does not accept `anchor_topics`; `_anchor_boost` still boosts the anchor itself.

- [ ] **Step 8: Implement — _same_field_affinity + rerank changes**

In `src/dext_recommend/core/rerank.py`, delete the `_anchor_boost` function and add:

```python
def _same_field_affinity(
    fact: ProfessorFact | None, anchor_topics: tuple[str, ...],
    base: float, profile: RankingProfile,
) -> tuple[float, float, float]:
    """Return (new_score, overlap_component, boost)."""
    if not anchor_topics or fact is None:
        return base, 0.0, 0.0
    cand = set(fact.topic_ids or ())
    anchor = set(anchor_topics)
    overlap_count = len(cand & anchor)
    overlap_component = overlap_count / max(1, len(anchor))
    boost = min(
        profile.same_field_boost_max,
        profile.same_field_boost_per_topic * overlap_count,
    )
    return min(1.0, base + boost), overlap_component, boost
```

Change the `rerank` signature to add `anchor_topics: tuple[str, ...] = ()` (after `query_terms`). In the loop body, replace the `score = _anchor_boost(...)` line with:

```python
        score, sf_overlap, sf_boost = _same_field_affinity(
            fact, anchor_topics, score, profile,
        )
        score = min(1.0, max(0.0, score))
        components = {
            "semantic_score": sem,
            "topic_statement_score": topic,
            "student_fit_score": fit,
            "eligibility_score": elig,
            "provenance_score": prov,
            "completeness_score": comp,
            "same_field_overlap": sf_overlap,
            "same_field_boost": sf_boost,
        }
```

(Remove the old `components = {...}` dict that lacked the two new keys; the `_match_level` call stays.)

- [ ] **Step 9: Run rerank tests**

Run: `uv run pytest tests/dext_recommend/test_recommend_rerank.py -q`
Expected: PASS (new tests pass; existing rerank tests that assert `score_components` keys need updating — if any assert the exact 6-key set, update them to expect the 8-key set in the same commit).

- [ ] **Step 10: Write failing test — same_field anchor excluded + falls back**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_same_field_anchor_excluded_and_topic_overlap_ranked():
    """Anchor not in results; topic-sharing candidates rank above non-sharing."""
    # Construct a core where e_nlp_anchor has topic_ids=("topic_nlp","topic_ml"),
    # e_nlp_a/e_nlp_b share one/both, e_cv_strong shares none.
    # Request same_field intent with anchor_entity_id="e_nlp_anchor".
    # Assert: e_nlp_anchor not in results; e_nlp_a/e_nlp_b ahead of e_cv_strong
    # when semantic scores are tied.
    # Mirror the existing test_recommend_same_field_anchor_boost construction
    # in the same file (it currently asserts the WRONG behavior — rewrite it).
    ...
```

(Implement by rewriting the existing `test_recommend_same_field_anchor_boost` test in the file — read its current body, replace the assertions per spec §6.1 delta.)

- [ ] **Step 11: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_same_field_anchor_excluded_and_topic_overlap_ranked -v`
Expected: FAIL — service still uses `_anchor_boost` path / doesn't exclude anchor.

- [ ] **Step 12: Implement — service anchor resolution**

In `src/dext_recommend/core/service.py`, add `import dataclasses` at top if not present. After `route = resolve_recommend_route(request)` and before `effective_filters = ...`, add anchor resolution:

```python
        anchor_topics: tuple[str, ...] = ()
        if route.intent == "same_field" and route.anchor_entity_id:
            anchor_map = await self._deps.facts_port.hydrate(
                snapshot, [route.anchor_entity_id],
            )
            anchor_fact = anchor_map.get(route.anchor_entity_id)
            anchor_unavailable = (
                anchor_fact is None
                or anchor_fact.role_status == "excluded"
                or (anchor_fact.role_status == "review"
                    and request.review_policy != "include_downranked")
                or not anchor_fact.topic_ids
            )
            if anchor_unavailable:
                route_warnings.append(_warn(
                    RecommendationErrorCode.MISSING_ANCHOR,
                    "anchor unavailable or lacks approved topics; falling back to new_search",
                ))
                route = dataclasses.replace(
                    route, intent="new_search", anchor_entity_id=None,
                )
            else:
                anchor_topics = tuple(anchor_fact.topic_ids)
                route = dataclasses.replace(
                    route,
                    exclude_entity_ids=tuple(dict.fromkeys(
                        route.exclude_entity_ids + (route.anchor_entity_id,)
                    )),
                )
```

Then pass `anchor_topics=anchor_topics` to the `rerank(...)` call in service.

- [ ] **Step 13: Run same_field tests**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py -k same_field -v`
Expected: PASS.

- [ ] **Step 14: Add fallback tests**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_same_field_anchor_missing_falls_back():
    """Anchor not in ACTIVE build (fact None) -> missing_anchor warning + new_search."""
    ...

async def test_recommend_same_field_anchor_excluded_falls_back():
    """Anchor role_status=excluded -> missing_anchor warning + new_search."""
    ...

async def test_recommend_same_field_anchor_without_topics_falls_back():
    """Anchor exists but topic_ids=() -> missing_anchor warning + new_search."""
    ...

async def test_recommend_same_field_boost_from_profile():
    """profile same_field_boost_per_topic=0 -> no boost (order by tie-break only)."""
    ...
```

(Implement each by mirroring the construction of the anchor test above with the appropriate fixture mutation.)

- [ ] **Step 15: Run full recommend module + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS.

```bash
git add src/dext_recommend/core/ranking_profile.py src/dext_recommend/core/rerank.py src/dext_recommend/core/service.py tests/dext_recommend/_recfixtures.py tests/dext_recommend/test_recommend_ranking_profile.py tests/dext_recommend/test_recommend_rerank.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "fix(rec): same_field — topic-overlap boost, anchor excluded, missing-anchor fallback"
```

---

## Task 4: W1 — adaptive recall loop rewrite

**Files:**
- Modify: `src/dext_recommend/core/recall.py` (rewrite `recall_loop`, add `RecallResult`/`StepDiag`)
- Modify: `src/dext_recommend/core/service.py` (shrink: call new recall_loop, drop post-loop filter calls)
- Test: `tests/dext_recommend/test_recommend_recall.py`, `tests/dext_recommend/test_recommend_core.py`

**Interfaces:**
- Produces: `recall_loop(snapshot, vector_port, query_vector, effective_filters, profile, *, facts_port, route, coverage_flags, review_policy, embedding_sparse_vector, oversample_max, request_oversample, limit) -> RecallResult`; `RecallResult(survivors: tuple[VectorHit,...], fact_map: Mapping[str,ProfessorFact], filter_diagnostics: FilterDiagnostics, steps_used: int, step_diags: tuple[StepDiag,...])`.

- [ ] **Step 1: Write failing test — per-step filter, current-step authoritative**

Add to `tests/dext_recommend/test_recommend_recall.py`:

```python
async def test_recall_loop_filters_per_step_and_dedups_hydrate():
    from dext_recommend.core.recall import recall_loop, RecallResult
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports._fakes import FakeVectorSearchPort, FakeProfessorFactPort
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact
    from tests.dext_recommend._recfixtures import snapshot, ranking_profile_dict

    profile = RankingProfile.from_dict(ranking_profile_dict(
        oversample_steps=(100, 200),
    ))
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    # step 100: e1 (will be filtered out by fact); step 200: e1 + e2
    hits_100 = [VectorHit(entity_id="e1", score=0.9, payload={})]
    hits_200 = [
        VectorHit(entity_id="e1", score=0.95, payload={}),
        VectorHit(entity_id="e2", score=0.8, payload={}),
    ]

    class SteppedVectorPort:
        def __init__(self):
            self.calls = 0
        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.calls += 1
            return list(hits_100) if oversample == 100 else list(hits_200)

    port = SteppedVectorPort()
    fact_e1 = ProfessorFact(
        entity_id="e1", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="any",
        phd_eligibility="any", role_status="excluded", profile_url=None,
        profile_hash=None, research_summary=None,
    )
    fact_e2 = ProfessorFact(
        entity_id="e2", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="any",
        phd_eligibility="any", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None,
    )
    facts_port = FakeProfessorFactPort(facts={"e1": fact_e1, "e2": fact_e2})

    result = await recall_loop(
        snapshot(), port, [0.1], RecommendationFilters(), profile,
        facts_port=facts_port, route=route, coverage_flags={},
        review_policy="exclude",
        embedding_sparse_vector=None,
        oversample_max=200, request_oversample=100, limit=5,
    )
    assert isinstance(result, RecallResult)
    assert result.steps_used == 2  # step 100 had 0 survivors (e1 excluded) -> continued
    assert [h.entity_id for h in result.survivors] == ["e2"]  # current step authoritative
    # e1 hydrated once (cached), not re-hydrated at step 200
    assert sum(len(c["entity_ids"]) for c in facts_port.hydrate_calls) == 2  # e1 then e2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_recall.py::test_recall_loop_filters_per_step_and_dedups_hydrate -v`
Expected: FAIL — `recall_loop` signature doesn't accept the new kwargs; returns tuple not `RecallResult`.

- [ ] **Step 3: Implement — RecallResult/StepDiag + recall_loop rewrite**

Replace the `recall_loop` function and add dataclasses in `src/dext_recommend/core/recall.py`:

```python
from dataclasses import dataclass
from collections.abc import Mapping

from dext_recommend.models import RecommendationFilters
from dext_recommend.ports.vector_search import VectorHit, VectorSearchPort
from dext_recommend.ports.professor_facts import ProfessorFact, ProfessorFactPort
from dext_recommend.readiness import ActiveBuildSnapshot
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.filters import payload_prefilter, final_filter, FilterDiagnostics
from dext_recommend.core.intent import RecommendRoute


@dataclass(frozen=True, slots=True)
class StepDiag:
    step: int
    raw_hits: int
    post_prefilter: int
    post_filter: int


@dataclass(frozen=True, slots=True)
class RecallResult:
    survivors: tuple[VectorHit, ...]
    fact_map: Mapping[str, ProfessorFact]
    filter_diagnostics: FilterDiagnostics
    steps_used: int
    step_diags: tuple[StepDiag, ...]


def compute_oversample_steps(
    request_oversample: int, profile: RankingProfile, *, oversample_max: int,
) -> tuple[int, ...]:
    steps = tuple(s for s in profile.oversample_steps
                  if s >= request_oversample and s <= oversample_max)
    if not steps:
        clamp = min(request_oversample, oversample_max)
        steps = (clamp,)
    return steps


def normalize_rrf(hits: list[VectorHit]) -> dict[str, float]:
    if not hits:
        return {}
    if len(hits) == 1:
        return {hits[0].entity_id: 1.0}
    scores = [h.score for h in hits]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return {h.entity_id: 1.0 for h in hits}
    return {h.entity_id: (h.score - lo) / (hi - lo) for h in hits}


async def recall_loop(
    snapshot: ActiveBuildSnapshot,
    vector_port: VectorSearchPort,
    query_vector: list[float],
    effective_filters: RecommendationFilters,
    profile: RankingProfile,
    *,
    facts_port: ProfessorFactPort,
    route: RecommendRoute,
    coverage_flags: Mapping[str, bool],
    review_policy: str,
    embedding_sparse_vector,
    oversample_max: int,
    request_oversample: int,
    limit: int,
) -> RecallResult:
    steps = compute_oversample_steps(request_oversample, profile, oversample_max=oversample_max)
    org_unit_degraded = coverage_flags.get("org_unit_ids") is not True

    fact_cache: dict[str, ProfessorFact] = {}
    hydrated_ids: set[str] = set()
    current_survivors: list[VectorHit] = []
    current_filter_diag = FilterDiagnostics()
    step_diags: list[StepDiag] = []
    steps_used = 0

    for step in steps:
        steps_used += 1
        hits = await vector_port.hybrid_recall(
            snapshot, query_vector, effective_filters, step, profile.version,
            rrf_k=profile.rrf_k, sparse_vector=embedding_sparse_vector,
        )
        # dedup within this step by entity_id, keep first occurrence
        seen_step: set[str] = set()
        dedup_hits: list[VectorHit] = []
        for h in hits:
            if h.entity_id not in seen_step:
                seen_step.add(h.entity_id)
                dedup_hits.append(h)
        pref = payload_prefilter(dedup_hits, effective_filters, org_unit_degraded=org_unit_degraded)

        new_ids = [h.entity_id for h in pref if h.entity_id not in hydrated_ids]
        if new_ids:
            hydrated_ids.update(new_ids)
            fact_cache.update(await facts_port.hydrate(snapshot, new_ids))

        current_survivors, current_filter_diag = final_filter(
            pref, fact_cache, effective_filters, route, coverage_flags,
            review_policy=review_policy,
        )
        step_diags.append(StepDiag(
            step=step, raw_hits=len(hits),
            post_prefilter=len(pref), post_filter=len(current_survivors),
        ))
        if len(current_survivors) >= limit:
            break

    return RecallResult(
        survivors=tuple(current_survivors),
        fact_map=dict(fact_cache),
        filter_diagnostics=current_filter_diag,
        steps_used=steps_used,
        step_diags=tuple(step_diags),
    )


__all__ = ["StepDiag", "RecallResult", "compute_oversample_steps", "normalize_rrf", "recall_loop"]
```

- [ ] **Step 4: Run the recall test**

Run: `uv run pytest tests/dext_recommend/test_recommend_recall.py::test_recall_loop_filters_per_step_and_dedups_hydrate -v`
Expected: PASS.

- [ ] **Step 5: Shrink service.py — use new recall_loop**

In `src/dext_recommend/core/service.py`, replace the block at lines ~147-163 (the `recall_loop` call + post-loop `payload_prefilter`+`hydrate`+`final_filter`) with:

```python
        coverage_flags = self._deps.coverage_flags_by_build_id.get(snapshot.build_id, {})
        recall = await recall_loop(
            snapshot, self._deps.vector_port, list(embedding.vector),
            effective_filters, profile,
            facts_port=self._deps.facts_port,
            route=route, coverage_flags=coverage_flags,
            review_policy=request.review_policy,
            embedding_sparse_vector=embedding.sparse_vector,
            oversample_max=self._settings.oversample_max,
            request_oversample=request.oversample, limit=request.limit,
        )
        survivors = list(recall.survivors)
        fact_map = dict(recall.fact_map)
        filter_diag = recall.filter_diagnostics
        recall_count = recall.step_diags[-1].raw_hits if recall.step_diags else 0
        steps_used = recall.steps_used
```

Delete the now-dead `payload_prefilter`/`hydrate`/`final_filter` calls that followed. Keep the `if not survivors:` branch (uses `filter_diag`/`recall_count`). Update the `no_candidates` diagnostics `QueryDiagnostics` to include `steps_used=steps_used` once that field exists (Task 5 adds the field — for now pass what you can; if `QueryDiagnostics` doesn't yet accept `steps_used`, omit it here and Task 5 will wire it).

Also update the happy-path `QueryDiagnostics(...)` construction to use `recall_count` and `post_filter_count=len(survivors)` from the new variables.

- [ ] **Step 6: Run full recommend module**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS (existing oversample-progression test may need updating to reflect "current step authoritative" — if `test_recommend_oversample_step_progression` asserts cumulative survivors, update it to assert current-step survivors; this is the spec W1 behavior change).

- [ ] **Step 7: Add authoritative-recall test**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_larger_step_is_authoritative():
    """A candidate that appeared at step 200 with a different score and then
    disappeared at step 400 must NOT persist in results. Step 400 is authoritative."""
    # Build a stepped vector port: step 200 returns [e1, e2], step 400 returns [e3].
    # Assert results contain e3 only, not e1/e2.
    ...


async def test_recommend_empty_new_ids_skips_hydrate():
    """When all pref hits are already hydrated, hydrate is not called again."""
    # step 200 returns same entity_ids as step 100 -> hydrate_calls stays at 1.
    ...
```

- [ ] **Step 8: Run + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS.

```bash
git add src/dext_recommend/core/recall.py src/dext_recommend/core/service.py tests/dext_recommend/test_recommend_recall.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "fix(rec): adaptive recall — per-step filter, current-step authoritative, fact cache dedup"
```

---

## Task 5: W4b — configurable tie_break + QueryDiagnostics.steps_used

**Files:**
- Modify: `src/dext_recommend/core/ranking_profile.py` (tie_break validation: 4 unique fields)
- Modify: `src/dext_recommend/core/rerank.py` (`_tie_break_key`)
- Modify: `src/dext_recommend/models.py` (`QueryDiagnostics.steps_used`)
- Modify: `src/dext_recommend/core/service.py` (write `steps_used`)
- Test: `tests/dext_recommend/test_recommend_rerank.py`, `tests/dext_recommend/test_recommend_ranking_profile.py`, `tests/dext_recommend/test_recommend_models.py`, `tests/dext_recommend/test_recommend_core.py`

**Interfaces:**
- Produces: `QueryDiagnostics(..., steps_used: int = 0)`; `RankingProfile.tie_break` must be a permutation of `{"score","semantic_score","evidence_count","entity_id"}`.

- [ ] **Step 1: Write failing test — tie_break drives sort**

Add to `tests/dext_recommend/test_recommend_rerank.py`:

```python
def test_rerank_tie_break_configurable():
    from dext_recommend.core.rerank import rerank
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from tests.dext_recommend._recfixtures import ranking_profile_dict

    d = ranking_profile_dict(tie_break=("score", "entity_id", "semantic_score", "evidence_count"))
    profile = RankingProfile.from_dict(d)
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    # two candidates with identical score+semantic+evidence -> tie broken by entity_id asc
    hits = [
        VectorHit(entity_id="e_b", score=0.5, payload={}),
        VectorHit(entity_id="e_a", score=0.5, payload={}),
    ]
    ranked = rerank(hits, {}, {}, {"e_a": 1.0, "e_b": 1.0}, None, profile, route)
    assert ranked[0].entity_id == "e_a"
    assert ranked[1].entity_id == "e_b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_rerank.py::test_rerank_tie_break_configurable -v`
Expected: FAIL — rerank still uses hardcoded sort.

- [ ] **Step 3: Implement — _tie_break_key + RankingProfile validation**

In `src/dext_recommend/core/rerank.py`, add:

```python
_TIE_BREAK_FIELDS = {"score", "semantic_score", "evidence_count", "entity_id"}


def _tie_break_key(entry: RerankEntry, spec: tuple[str, ...]) -> tuple:
    keys = []
    for field in spec:
        if field == "score":
            keys.append(-entry.score)
        elif field == "semantic_score":
            keys.append(-entry.score_components["semantic_score"])
        elif field == "evidence_count":
            keys.append(-entry.evidence_count)
        elif field == "entity_id":
            keys.append(entry.entity_id)
        else:
            raise ValueError(f"unknown tie_break field: {field}")
    return tuple(keys)
```

Replace the `entries.sort(...)` line with:

```python
    entries.sort(key=lambda e: _tie_break_key(e, profile.tie_break))
```

In `src/dext_recommend/core/ranking_profile.py`, strengthen the `tie_break` validation in `__post_init__` (replace the existing `if not self.tie_break:` check):

```python
        _allowed_tie_break = {"score", "semantic_score", "evidence_count", "entity_id"}
        if not self.tie_break:
            raise ValueError("RankingProfile.tie_break must be non-empty")
        if len(set(self.tie_break)) != len(self.tie_break):
            raise ValueError("RankingProfile.tie_break must not repeat fields")
        unknown = [f for f in self.tie_break if f not in _allowed_tie_break]
        if unknown:
            raise ValueError(f"RankingProfile.tie_break has unknown fields: {unknown}")
        if set(self.tie_break) != _allowed_tie_break:
            raise ValueError(
                "RankingProfile.tie_break must be a complete permutation of "
                "{score, semantic_score, evidence_count, entity_id}"
            )
```

- [ ] **Step 4: Run rerank + ranking_profile tests**

Run: `uv run pytest tests/dext_recommend/test_recommend_rerank.py tests/dext_recommend/test_recommend_ranking_profile.py -q`
Expected: PASS.

- [ ] **Step 5: Write failing test — QueryDiagnostics.steps_used**

Add to `tests/dext_recommend/test_recommend_models.py`:

```python
def test_query_diagnostics_steps_used_defaults_zero():
    from dext_recommend.models import QueryDiagnostics
    d = QueryDiagnostics(query_length=5, language_summary="en", filter_summary="none")
    assert d.steps_used == 0


def test_query_diagnostics_steps_used_set():
    from dext_recommend.models import QueryDiagnostics
    d = QueryDiagnostics(query_length=5, language_summary="en",
                        filter_summary="none", steps_used=2)
    assert d.steps_used == 2
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_models.py::test_query_diagnostics_steps_used_defaults_zero tests/dext_recommend/test_recommend_models.py::test_query_diagnostics_steps_used_set -v`
Expected: FAIL — `QueryDiagnostics` has no `steps_used`.

- [ ] **Step 7: Implement — add steps_used**

In `src/dext_recommend/models.py`, add to `QueryDiagnostics`:

```python
@dataclass(frozen=True, slots=True)
class QueryDiagnostics:
    query_length: int
    language_summary: str | None
    filter_summary: str | None
    recall_count: int = 0
    post_filter_count: int = 0
    returned_count: int = 0
    steps_used: int = 0
```

In `src/dext_recommend/core/service.py`, pass `steps_used=steps_used` to every `QueryDiagnostics(...)` construction (the no_candidates branch and the happy-path branch). Wire the `steps_used` variable from Task 4's recall result into both.

- [ ] **Step 8: Run full recommend module + add steps_used core test**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS.

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_steps_used_in_diagnostics():
    """Happy path: resp.query.steps_used >= 1."""
    # mirror the existing happy-path test; assert resp.query.steps_used >= 1
    ...
```

- [ ] **Step 9: Commit**

```bash
git add src/dext_recommend/core/rerank.py src/dext_recommend/core/ranking_profile.py src/dext_recommend/models.py src/dext_recommend/core/service.py tests/dext_recommend/test_recommend_rerank.py tests/dext_recommend/test_recommend_ranking_profile.py tests/dext_recommend/test_recommend_models.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "feat(rec): configurable tie_break + QueryDiagnostics.steps_used"
```

---

## Task 6: W5 — explanation non-empty when detail missing

**Files:**
- Modify: `src/dext_recommend/core/explanation.py`
- Test: `tests/dext_recommend/test_recommend_explanation.py`, `tests/dext_recommend/test_recommend_core.py`

**Interfaces:**
- Produces: `build_explanation(...)` always returns `ExplanationResult` with `len(short_reasons) >= 1`.

- [ ] **Step 1: Write failing test — detail None gives non-empty qualified reason**

Add to `tests/dext_recommend/test_recommend_explanation.py`:

```python
def test_explanation_detail_none_has_qualified_reason():
    from dext_recommend.core.explanation import build_explanation
    from dext_recommend.core.rerank import RerankEntry
    entry = RerankEntry(
        entity_id="e1", score=0.42,
        score_components={"semantic_score": 0.5, "topic_statement_score": 0.0,
                          "student_fit_score": 0.0, "eligibility_score": 0.0,
                          "provenance_score": 0.0, "completeness_score": 0.0,
                          "same_field_overlap": 0.0, "same_field_boost": 0.0},
        match_level="possible", evidence_count=0,
    )
    expl = build_explanation(entry, None, None, query_terms=("cv",))
    assert len(expl.short_reasons) >= 1
    assert expl.weak_explanation is True
    assert expl.missing_reason is not None
    # must not present the composite entry.score as a semantic score
    assert "semantic" not in expl.short_reasons[0].lower()
    assert "score=" not in expl.short_reasons[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_explanation.py::test_explanation_detail_none_has_qualified_reason -v`
Expected: FAIL — current code returns `short_reasons=()` when detail None.

- [ ] **Step 3: Implement — qualified reason**

In `src/dext_recommend/core/explanation.py`, replace the `if detail is None:` block:

```python
    if detail is None:
        return ExplanationResult(
            short_reasons=("综合匹配信号较高；当前缺少可回溯详情证据",),
            evidence_refs=(),
            matched_topics=(),
            matched_statements=(),
            matched_publications=(),
            weak_explanation=True,
            missing_reason="ProfessorDetail unavailable in ACTIVE build",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/dext_recommend/test_recommend_explanation.py::test_explanation_detail_none_has_qualified_reason -v`
Expected: PASS.

- [ ] **Step 5: Rewrite the bug-pin test**

In `tests/dext_recommend/test_recommend_explanation.py`, find the test at line ~28 that asserts `expl.short_reasons == ()`. Replace that assertion:

```python
    # Previously pinned the empty-reason bug; spec §8 requires >= 1 item.
    assert len(expl.short_reasons) >= 1
    assert expl.weak_explanation is True
    assert "缺少可回溯详情证据" in expl.short_reasons[0]
```

- [ ] **Step 6: Add every-result-has-reason test**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_explanation_every_result_has_reason():
    """Every result in resp.results has len(short_reasons) >= 1, including
    candidates whose ProfessorDetail is missing."""
    # Build a core where at least one top candidate has no detail.
    # Assert all(c.short_reasons for c in resp.results).
    ...
```

- [ ] **Step 7: Run full recommend module + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS.

```bash
git add src/dext_recommend/core/explanation.py tests/dext_recommend/test_recommend_explanation.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "fix(rec): explanation always non-empty — qualified reason when detail missing"
```

---

## Task 7: W7 — request validation + viewer permission boundary

**Files:**
- Modify: `src/dext_recommend/config.py` (`total_timeout` gt=0; add `query_max_chars`, `limit_max`)
- Modify: `src/dext_recommend/errors.py` (add `INVALID_REQUEST`, `UNAUTHORIZED_REVIEW`)
- Modify: `src/dext_recommend/models.py` (`RecommendRequest.__post_init__` — but only for cheap invariants; full validation in service)
- Modify: `src/dext_recommend/core/service.py` (`recommend(request, *, viewer_permissions=None)`; `validate_request`; gate `include_contacts`/`review_policy` on permissions)
- Test: `tests/dext_recommend/test_recommend_config.py`, `tests/dext_recommend/test_recommend_core.py`

**Interfaces:**
- Produces: `RecommendationCore.recommend(request, *, viewer_permissions: ViewerPermissions | None = None) -> RecommendResponse`. Default `ViewerPermissions()` all-off.

- [ ] **Step 1: Write failing test — config validations**

Add to `tests/dext_recommend/test_recommend_config.py`:

```python
def test_config_total_timeout_must_be_positive():
    from dext_recommend.config import RecommendSettings
    import pytest
    with pytest.raises(Exception):
        RecommendSettings(total_timeout=0.0)


def test_config_has_query_max_chars_and_limit_max():
    from dext_recommend.config import RecommendSettings
    s = RecommendSettings()
    assert s.query_max_chars > 0
    assert s.limit_max > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_config.py::test_config_total_timeout_must_be_positive tests/dext_recommend/test_recommend_config.py::test_config_has_query_max_chars_and_limit_max -v`
Expected: FAIL.

- [ ] **Step 3: Implement — config fields**

In `src/dext_recommend/config.py`, change `total_timeout` and add fields:

```python
    # Timeouts / limits (overview §6.6, §11)
    total_timeout: float = Field(default=30.0, gt=0.0)
    oversample_default: int = 200
    oversample_max: int = 1000
    query_max_chars: int = Field(default=4096, gt=0)
    limit_max: int = Field(default=50, gt=0)
```

In `src/dext_recommend/errors.py`, add to `RecommendationErrorCode`:

```python
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED_REVIEW = "unauthorized_review"
```

- [ ] **Step 4: Run config test**

Run: `uv run pytest tests/dext_recommend/test_recommend_config.py -q`
Expected: PASS.

- [ ] **Step 5: Write failing test — invalid request calls no ports**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_invalid_request_calls_no_ports():
    from dext_recommend.models import RecommendRequest
    # Build a core with recording fakes.
    req = RecommendRequest(query_text="", limit=5)  # empty query_text
    resp = await core.recommend(req)
    codes = [w.code for w in resp.warnings]
    assert "invalid_request" in codes
    assert resp.results == ()
    # no port was called
    assert vector_port.hybrid_recall_calls == []
    assert facts_port.hydrate_calls == []
    assert embedding_port.call_count == 0  # adapt to actual recording attr
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_invalid_request_calls_no_ports -v`
Expected: FAIL — no request validation.

- [ ] **Step 7: Implement — validate_request + viewer_permissions**

In `src/dext_recommend/core/service.py`, add a `validate_request` function:

```python
def validate_request(request: RecommendRequest, settings: RecommendSettings) -> str | None:
    """Return an error message string if invalid, else None."""
    if not isinstance(request.query_text, str) or not request.query_text.strip():
        return "query_text must be a non-empty string"
    if len(request.query_text) > settings.query_max_chars:
        return f"query_text exceeds {settings.query_max_chars} chars"
    if not (1 <= request.limit <= settings.limit_max):
        return f"limit must be in [1, {settings.limit_max}]"
    if not (1 <= request.oversample <= settings.oversample_max):
        return f"oversample must be in [1, {settings.oversample_max}]"
    if request.ranking_mode != "explainable_precision":
        return f"ranking_mode {request.ranking_mode!r} not supported"
    if request.review_policy not in ("exclude", "include_downranked"):
        return f"invalid review_policy: {request.review_policy!r}"
    if request.diagnostics_level not in ("none", "summary", "debug"):
        return f"invalid diagnostics_level: {request.diagnostics_level!r}"
    if request.filters.master_eligibility not in ("any", "confirmed"):
        return "invalid master_eligibility"
    if request.filters.phd_eligibility not in ("any", "confirmed"):
        return "invalid phd_eligibility"
    if request.filters.topic_filter_mode not in ("soft", "hard"):
        return "invalid topic_filter_mode"
    for fld in ("university_ids", "city_names", "org_unit_ids", "title_families", "topic_ids"):
        for v in getattr(request.filters, fld):
            if not isinstance(v, str) or not v:
                return f"filters.{fld} contains empty/non-string value"
    return None
```

Change `recommend` signature and add validation + permission gate at the very top:

```python
    async def recommend(
        self,
        request: RecommendRequest,
        *,
        viewer_permissions: ViewerPermissions | None = None,
    ) -> RecommendResponse:
        vp = viewer_permissions or ViewerPermissions()

        err = validate_request(request, self._settings)
        if err is not None:
            return _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.INVALID_REQUEST, err, severity="error"),
            )

        if request.include_contacts and not vp.include_contacts:
            return _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.UNAUTHORIZED_CONTACT,
                              "include_contacts requested without permission", severity="error"),
            )
        if request.review_policy == "include_downranked" and not vp.can_view_review:
            return _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.UNAUTHORIZED_REVIEW,
                              "include_downranked requested without permission", severity="error"),
            )

        effective_include_contacts = request.include_contacts and vp.include_contacts
        # ... existing body, but replace request.include_contacts with
        # effective_include_contacts where get_detail/fetch_details is called,
        # and pass vp as viewer_permissions.
```

(Update the `fetch_details(...)` call and the `ViewerPermissions(...)` construction to use `effective_include_contacts` and `vp`.)

- [ ] **Step 8: Run invalid-request test**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_invalid_request_calls_no_ports -v`
Expected: PASS.

- [ ] **Step 9: Add unauthorized tests + fix existing positive tests**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_unauthorized_contacts_calls_no_detail():
    """include_contacts=True with default ViewerPermissions -> UNAUTHORIZED_CONTACT, no get_detail."""
    ...

async def test_recommend_unauthorized_review_policy():
    """review_policy=include_downranked with default ViewerPermissions -> UNAUTHORIZED_REVIEW."""
    ...
```

Then: find every existing test that uses `include_contacts=True` or `review_policy="include_downranked"` and add an explicit `viewer_permissions=ViewerPermissions(include_contacts=True, can_view_review=True)` kwarg to the `recommend(...)` call. This is mandatory — the default permissions are now all-off, so those tests would otherwise break.

- [ ] **Step 10: Run full recommend module + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS.

```bash
git add src/dext_recommend/config.py src/dext_recommend/errors.py src/dext_recommend/core/service.py tests/dext_recommend/test_recommend_config.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "feat(rec): W7 request validation + viewer permission boundary"
```

---

## Task 8: W6-a — default profile file + robust RankingProfileAdapter.read_profile + composition seam

**Files:**
- Create: `data/recommend/ranking-profile.json`
- Modify: `src/dext_recommend/adapters/ranking_profile.py` (add `read_profile`)
- Create: `src/dext_recommend/composition.py`
- Test: `tests/dext_recommend/test_recommend_ranking_profile_adapter.py`, `tests/dext_recommend/test_recommend_ranking_profile.py`

**Interfaces:**
- Produces: `RankingProfileAdapter.read_profile(path) -> RankingProfile` (robust to OSError/UnicodeError/JSONDecodeError/KeyError/TypeError/ValueError → `ReadinessSourceError`); `composition.assemble_core(deps, settings)`; `composition.build_test_core(deps, settings)`.

- [ ] **Step 1: Create the default profile file**

Create `data/recommend/ranking-profile.json`:

```json
{
  "version": "ranking-v1",
  "weights": {
    "semantic_score": 0.50,
    "topic_statement_score": 0.18,
    "student_fit_score": 0.12,
    "eligibility_score": 0.08,
    "provenance_score": 0.08,
    "completeness_score": 0.04
  },
  "rrf_k": 60,
  "oversample_steps": [200, 400, 800, 1000],
  "detail_rerank_window": 50,
  "detail_fetch_concurrency": 8,
  "detail_rerank_window_max": 100,
  "same_field_boost_per_topic": 0.05,
  "same_field_boost_max": 0.15,
  "match_level_thresholds": {"excellent": 0.75, "strong": 0.55, "possible": 0.35},
  "tie_break": ["score", "semantic_score", "evidence_count", "entity_id"]
}
```

- [ ] **Step 2: Write failing test — default profile loads**

Add to `tests/dext_recommend/test_recommend_ranking_profile_adapter.py`:

```python
async def test_default_ranking_profile_json_loads():
    from pathlib import Path
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.adapters.ranking_profile import RankingProfileAdapter

    adapter = RankingProfileAdapter()
    profile = await adapter.read_profile(Path("data/recommend/ranking-profile.json"))
    assert profile.version == "ranking-v1"
    assert profile.rrf_k == 60
    assert profile.same_field_boost_per_topic == 0.05
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile_adapter.py::test_default_ranking_profile_json_loads -v`
Expected: FAIL — `RankingProfileAdapter` has no `read_profile`.

- [ ] **Step 4: Implement — read_profile with robust error normalization**

In `src/dext_recommend/adapters/ranking_profile.py`, add the import and method:

```python
from dext_recommend.core.ranking_profile import RankingProfile

    async def read_profile(self, path: Path) -> RankingProfile:
        p = Path(path)

        def _read() -> RankingProfile:
            try:
                raw = p.read_text(encoding="utf-8")
            except OSError as exc:
                raise ReadinessSourceError("ranking", f"profile read failed: {exc}") from exc
            except UnicodeError as exc:
                raise ReadinessSourceError("ranking", "profile not valid UTF-8") from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ReadinessSourceError("ranking", "profile is not valid JSON") from exc
            if not isinstance(data, dict):
                raise ReadinessSourceError("ranking", "profile root must be an object")
            try:
                return RankingProfile.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                raise ReadinessSourceError("ranking", f"invalid profile: {exc}") from exc

        return await asyncio.to_thread(_read)
```

(Keep the existing `read_version` method. `RankingProfile` import must work — it's in `core.ranking_profile`, and `adapters` already depends on core; verify `adapters/__init__.py` or `ranking_profile.py` doesn't create a cycle. `core.ranking_profile` imports only `_immutable` — no cycle.)

- [ ] **Step 5: Run the default-profile test**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile_adapter.py::test_default_ranking_profile_json_loads -v`
Expected: PASS.

- [ ] **Step 6: Write failing test — malformed input normalized**

Add to `tests/dext_recommend/test_recommend_ranking_profile_adapter.py`:

```python
async def test_read_profile_normalizes_malformed(tmp_path):
    from dext_recommend.adapters.ranking_profile import RankingProfileAdapter
    from dext_recommend.ports.release_readback import ReadinessSourceError

    adapter = RankingProfileAdapter()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    try:
        await adapter.read_profile(bad)
        assert False, "should raise ReadinessSourceError"
    except ReadinessSourceError as exc:
        assert "ranking" in str(exc).lower() or exc.source == "ranking"
```

- [ ] **Step 7: Run + verify pass**

Run: `uv run pytest tests/dext_recommend/test_recommend_ranking_profile_adapter.py -q`
Expected: PASS.

- [ ] **Step 8: Create composition seam**

Create `src/dext_recommend/composition.py`:

```python
"""Composition seam for RecommendationCore.

R3c provides only explicit injection seams — assemble_core and build_test_core.
A production factory that constructs live adapters lands in R7 once all
required live adapters (snapshot/embedding/vector/facts/llm) exist and a
startup readiness check is in place. Do NOT add a fake production factory
that returns a Core whose methods raise NotImplementedError.
"""
from __future__ import annotations

from dext_recommend.config import RecommendSettings
from dext_recommend.core.service import RecommendDeps, RecommendationCore


def assemble_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return RecommendationCore(deps, settings)


def build_test_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return assemble_core(deps, settings)


__all__ = ["assemble_core", "build_test_core"]
```

- [ ] **Step 9: Write composition seam test**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
def test_composition_seam_injects_deps_verbatim():
    from dext_recommend.composition import assemble_core, build_test_core
    from dext_recommend.config import RecommendSettings
    # build minimal deps from existing fixtures; assert the returned Core
    # holds the exact deps/settings passed in.
    deps = ...  # construct from _recfixtures
    settings = RecommendSettings()
    core = assemble_core(deps, settings)
    assert core.deps is deps
    assert build_test_core(deps, settings).deps is deps
```

- [ ] **Step 10: Run + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS.

```bash
git add data/recommend/ranking-profile.json src/dext_recommend/adapters/ranking_profile.py src/dext_recommend/composition.py tests/dext_recommend/test_recommend_ranking_profile_adapter.py tests/dext_recommend/test_recommend_core.py
git -c commit.gpgsign=false commit -m "feat(rec): default ranking profile + robust read_profile + composition seam"
```

---

## Task 9: W6-b — request-local resilience, phase diagnostics, timeout, detail degradation

**Files:**
- Modify: `src/dext_recommend/models.py` (add `PhaseDiagnostic`, `RecommendResponse.phase_diagnostics`)
- Modify: `src/dext_recommend/errors.py` (add `REQUEST_TIMEOUT`, `LLM_UNAVAILABLE`, `EMBEDDING_UNAVAILABLE`, `VECTOR_UNAVAILABLE`, `HYDRATE_UNAVAILABLE`, `DETAILS_UNAVAILABLE`)
- Create: `src/dext_recommend/core/_resilience.py`
- Modify: `src/dext_recommend/core/service.py` (context, guards, timeout)
- Modify: `src/dext_recommend/core/recall.py` (guard vector/hydrate per step)
- Modify: `src/dext_recommend/core/detail_fetch.py` (per-entity guard + degradation)
- Modify: `src/dext_recommend/core/validation.py` (allow `phase_diagnostics`)
- Test: `tests/dext_recommend/test_recommend_core.py`, `tests/dext_recommend/test_recommend_validation.py`

**Interfaces:**
- Produces: `PhaseDiagnostic(phase: str, attempt: int | None, elapsed_ms: float, error_code: str | None)`; `RecommendResponse.phase_diagnostics: tuple[PhaseDiagnostic,...] = ()`; `RecommendExecutionContext` (mutable, per-request); `_guarded_async(ctx, phase, op_factory)` / `_guarded_sync(ctx, phase, fn)`.

- [ ] **Step 1: Add error codes**

In `src/dext_recommend/errors.py`, add to `RecommendationErrorCode`:

```python
    REQUEST_TIMEOUT = "request_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    EMBEDDING_UNAVAILABLE = "embedding_unavailable"
    VECTOR_UNAVAILABLE = "vector_unavailable"
    HYDRATE_UNAVAILABLE = "hydrate_unavailable"
    DETAILS_UNAVAILABLE = "details_unavailable"
```

- [ ] **Step 2: Add PhaseDiagnostic + RecommendResponse field**

In `src/dext_recommend/models.py`, add the DTO (before `RecommendResponse`):

```python
@dataclass(frozen=True, slots=True)
class PhaseDiagnostic:
    phase: str
    attempt: int | None
    elapsed_ms: float
    error_code: str | None
```

Add to `RecommendResponse` (after `warnings`):

```python
    phase_diagnostics: tuple[PhaseDiagnostic, ...] = ()
```

And in `__post_init__`, add `"phase_diagnostics"` to the tuple-immutable field loop.

- [ ] **Step 3: Write failing test — phase_diagnostics populated on success**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_phase_diagnostics_populated():
    # happy path: resp.phase_diagnostics has entries for snapshot/ranking/qu/embedding/vector_recall/candidate_hydrate/details
    # each with elapsed_ms >= 0 and error_code is None
    resp = await core.recommend(req, viewer_permissions=ViewerPermissions())
    assert len(resp.phase_diagnostics) >= 1
    for pd in resp.phase_diagnostics:
        assert pd.elapsed_ms >= 0
        assert pd.error_code is None
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_phase_diagnostics_populated -v`
Expected: FAIL — `phase_diagnostics` not yet populated / field may not exist on responses built by service.

- [ ] **Step 5: Create _resilience.py**

Create `src/dext_recommend/core/_resilience.py`:

```python
"""Request-local execution context + phase guards for RecommendationCore.

The Core instance holds NO request-mutable state. Every recommend() call
creates a fresh RecommendExecutionContext and threads it through
_recommend_inner, recall_loop, and fetch_details so two concurrent
requests on the same Core cannot cross-contaminate diagnostics or snapshot.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from dext_recommend.models import PhaseDiagnostic
from dext_recommend.readiness import ActiveBuildSnapshot
from dext_recommend.core.ranking_profile import RankingProfile


class ClassifiedRecommendError(Exception):
    def __init__(self, code: str, phase: str, cause: Exception) -> None:
        self.code = code
        self.phase = phase
        self.cause = cause
        super().__init__(f"{phase} failed: {code}")


@dataclass(slots=True)
class RecommendExecutionContext:
    snapshot: ActiveBuildSnapshot | None = None
    profile: RankingProfile | None = None
    embedding_fingerprint: str | None = None
    phase_diagnostics: list[PhaseDiagnostic] = field(default_factory=list)

    def record(self, phase: str, elapsed_ms: float, error_code: str | None,
               attempt: int | None = None) -> None:
        self.phase_diagnostics.append(PhaseDiagnostic(
            phase=phase, attempt=attempt, elapsed_ms=elapsed_ms, error_code=error_code,
        ))

    def snapshot_phase_diagnostics(self) -> tuple[PhaseDiagnostic, ...]:
        return tuple(self.phase_diagnostics)


async def _guarded_async(
    ctx: RecommendExecutionContext, phase: str, code: str,
    op_factory: Callable[[], Awaitable[Any]],
    *, attempt: int | None = None,
) -> Any:
    start = time.perf_counter()
    try:
        result = await op_factory()
    except asyncio.CancelledError:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, "cancelled", attempt=attempt)
        raise
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, code, attempt=attempt)
        raise ClassifiedRecommendError(code, phase, exc) from exc
    elapsed = (time.perf_counter() - start) * 1000
    ctx.record(phase, elapsed, None, attempt=attempt)
    return result


def _guarded_sync(
    ctx: RecommendExecutionContext, phase: str, fn: Callable[[], Any],
) -> Any:
    start = time.perf_counter()
    try:
        result = fn()
    except asyncio.CancelledError:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, "cancelled")
        raise
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, None)
        raise
    elapsed = (time.perf_counter() - start) * 1000
    ctx.record(phase, elapsed, None)
    return result


__all__ = [
    "ClassifiedRecommendError", "RecommendExecutionContext",
    "_guarded_async", "_guarded_sync",
]
```

(Note: `_guarded_sync` for `snapshot_port.get_snapshot()` records but does not classify — snapshot failure is handled by the existing `snapshot is None` branch. Pure-compute exceptions are NOT swallowed — they propagate as-is per spec §4.6.4.)

- [ ] **Step 6: Wire guards into service.recommend**

In `src/dext_recommend/core/service.py`:

Add imports:
```python
import asyncio
import time
from dext_recommend.core._resilience import (
    ClassifiedRecommendError, RecommendExecutionContext,
    _guarded_async, _guarded_sync,
)
```

Restructure `recommend` to create a context, wrap in `wait_for`, and call an inner method:

```python
    async def recommend(self, request, *, viewer_permissions=None):
        vp = viewer_permissions or ViewerPermissions()
        err = validate_request(request, self._settings)
        if err is not None:
            return _error_response(
                snapshot=None, profile=None, embedding_fingerprint=None,
                warning=_warn(RecommendationErrorCode.INVALID_REQUEST, err, severity="error"),
            )
        if request.include_contacts and not vp.include_contacts:
            return _error_response(...)  # UNAUTHORIZED_CONTACT
        if request.review_policy == "include_downranked" and not vp.can_view_review:
            return _error_response(...)  # UNAUTHORIZED_REVIEW

        ctx = RecommendExecutionContext()
        try:
            return await asyncio.wait_for(
                self._recommend_inner(request, vp, ctx),
                timeout=self._settings.total_timeout,
            )
        except asyncio.TimeoutError:
            return _error_response(
                snapshot=ctx.snapshot, profile=ctx.profile,
                embedding_fingerprint=ctx.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode.REQUEST_TIMEOUT,
                              f"recommend exceeded {self._settings.total_timeout}s",
                              severity="error"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
            )
        except ClassifiedRecommendError as exc:
            return _error_response(
                snapshot=ctx.snapshot, profile=ctx.profile,
                embedding_fingerprint=ctx.embedding_fingerprint,
                warning=_warn(RecommendationErrorCode(exc.code),
                              f"{exc.phase} failed", severity="error"),
                phase_diagnostics=ctx.snapshot_phase_diagnostics(),
            )
```

Rename the existing body to `_recommend_inner(self, request, vp, ctx)` and wrap each port call:

- `snapshot_port.get_snapshot()` → `_guarded_sync(ctx, "snapshot", lambda: self._deps.snapshot_port.get_snapshot())`
- `ranking_port.read_profile(...)` → `_guarded_async(ctx, "ranking_profile", "ranking_profile_unavailable", lambda: self._deps.ranking_port.read_profile(...))` — note: use existing `RANKING_PROFILE_UNAVAILABLE` code (reused, not new).

  Actually `_guarded_async` takes a `code` string. For ranking profile, pass `"ranking_profile_unavailable"` (the existing code's value). For query understanding: `"llm_unavailable"`. For embedding: `"embedding_unavailable"`. For vector recall + candidate hydrate inside recall_loop: see Step 7.

- Set `ctx.snapshot = snapshot`, `ctx.profile = profile`, `ctx.embedding_fingerprint = embedding.embedding_fingerprint` as each becomes available (so the timeout/error response builders can use them).

Update `_error_response` to accept `phase_diagnostics` and pass it to the `RecommendResponse`. Update all `_error_response(...)` calls to pass `phase_diagnostics=()` where no ctx exists yet (validation/permission gates).

Update the final success `RecommendResponse(...)` to include `phase_diagnostics=ctx.snapshot_phase_diagnostics()`.

- [ ] **Step 7: Wire guards into recall_loop**

`recall_loop` now needs `ctx` to record per-step vector/hydrate phases. Add `ctx: RecommendExecutionContext` to its params. Wrap:

```python
        hits = await _guarded_async(
            ctx, "vector_recall", "vector_unavailable",
            lambda: vector_port.hybrid_recall(
                snapshot, query_vector, effective_filters, step, profile.version,
                rrf_k=profile.rrf_k, sparse_vector=embedding_sparse_vector,
            ),
            attempt=step,
        )
        ...
        if new_ids:
            hydrated = await _guarded_async(
                ctx, "candidate_hydrate", "hydrate_unavailable",
                lambda: facts_port.hydrate(snapshot, new_ids),
                attempt=step,
            )
            fact_cache.update(hydrated)
```

(Use the enum values: `RecommendationErrorCode.VECTOR_UNAVAILABLE.value` and `HYDRATE_UNAVAILABLE.value` — pass strings to `_guarded_async`. The `ClassifiedRecommendError.code` is a string; service does `RecommendationErrorCode(exc.code)`.)

Update service's `recall_loop(...)` call to pass `ctx=ctx`.

- [ ] **Step 8: Wire guards into detail_fetch + partial degradation**

In `src/dext_recommend/core/detail_fetch.py`, change `_fetch_one` to catch operational failures (not just KeyError/LookupError) and record a `DETAILS_UNAVAILABLE` diagnostic:

```python
async def _fetch_one(facts_port, snapshot, entity_id, include_contacts,
                     viewer_permissions, ctx, semaphore):
    async with semaphore:
        start = time.perf_counter()
        try:
            detail = await facts_port.get_detail(
                snapshot, entity_id, include_contacts, viewer_permissions,
            )
        except (KeyError, LookupError):
            elapsed = (time.perf_counter() - start) * 1000
            ctx.record("details", elapsed, None, attempt=None)
            return entity_id, None
        except Exception:
            elapsed = (time.perf_counter() - start) * 1000
            ctx.record("details", elapsed, "details_unavailable", attempt=None)
            return entity_id, None
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record("details", elapsed, None, attempt=None)
        return entity_id, detail
```

Update `fetch_details` signature to accept `ctx` and pass it; update its call in service. If any detail returned None due to operational failure (not just missing), service appends a `DETAILS_UNAVAILABLE` warning:

```python
        detail_failures = [eid for eid, d in detail_map.items() if d is None and eid in rerank_window_ids]
        if detail_failures:
            warnings.append(_warn(RecommendationErrorCode.DETAILS_UNAVAILABLE,
                                  f"{len(detail_failures)} detail(s) unavailable; degraded"))
```

(Refine: only count entities that were in the rerank window and came back None. Use the actual mechanism — `fetch_details` could return a separate `failed: set[str]` if cleaner. For minimal change, infer from `detail_map` None values within the requested window.)

- [ ] **Step 9: Update validation.py to allow phase_diagnostics**

In `src/dext_recommend/core/validation.py`, ensure `validate` accepts `phase_diagnostics` (it's a tuple of `PhaseDiagnostic` — no special validation needed beyond type; if the validator enumerates allowed fields, add it).

- [ ] **Step 10: Run the phase-diagnostics test**

Run: `uv run pytest tests/dext_recommend/test_recommend_core.py::test_recommend_phase_diagnostics_populated -v`
Expected: PASS.

- [ ] **Step 11: Add classified-failure + timeout + concurrent-isolation tests**

Add to `tests/dext_recommend/test_recommend_core.py`:

```python
async def test_recommend_llm_failure_classified():
    """FakeLLMGenerationPort.generate raises -> llm_unavailable, no 500."""
    ...

async def test_recommend_embedding_failure_classified():
    ...

async def test_recommend_vector_failure_classified():
    ...

async def test_recommend_hydrate_failure_not_classified_as_vector():
    """hydrate raises -> hydrate_unavailable (not vector_unavailable)."""
    ...

async def test_recommend_single_detail_failure_degrades():
    """One get_detail raises -> that detail None, others ok, DETAILS_UNAVAILABLE warning, request succeeds."""
    ...

async def test_recommend_total_timeout():
    """A fake port sleeps past total_timeout -> request_timeout response,
    phase_diagnostics shows the in-flight phase."""
    ...

async def test_recommend_concurrent_diagnostics_isolated():
    """Two concurrent recommend() calls on the same Core: their phase_diagnostics
    do not interleave. Use asyncio.gather with two requests that hit different
    phases; assert each response's phase_diagnostics contains only its own phases."""
    ...

async def test_recommend_early_returns_preserve_accumulated_warnings():
    """needs_clarification / unsupported / no_candidates early returns still
    carry route warnings + the triggering warning, and phase_diagnostics."""
    ...
```

(Implement each using the recording fakes; the concurrent test should use `asyncio.gather(c1.recommend(req1), c1.recommend(req2))` on the SAME core instance and assert the two `resp.phase_diagnostics` tuples are disjoint by a phase marker — e.g. give one request a query that triggers needs_clarification and another that triggers a vector failure, then assert the codes match.)

- [ ] **Step 12: Run full recommend module + commit**

Run: `uv run pytest tests/dext_recommend/ -q`
Expected: PASS (all new tests green; existing tests may need `viewer_permissions` kwarg fixes from Task 7 — verify those landed).

```bash
git add src/dext_recommend/models.py src/dext_recommend/errors.py src/dext_recommend/core/_resilience.py src/dext_recommend/core/service.py src/dext_recommend/core/recall.py src/dext_recommend/core/detail_fetch.py src/dext_recommend/core/validation.py tests/dext_recommend/test_recommend_core.py tests/dext_recommend/test_recommend_validation.py
git -c commit.gpgsign=false commit -m "feat(rec): request-local resilience — phase diagnostics, classified errors, total timeout, detail degradation"
```

---

## Task 10: Docs — update 3b status + final test count

**Files:**
- Modify: `docs/superpowers/specs/2026-07-02-dext-recommend-03b-recommend-core-impl-design.md` (status line)
- Modify: `docs/superpowers/specs/2026-07-02-dext-recommend-03c-r3-hardening-design.md` (status → implemented, add final test count)

- [ ] **Step 1: Update 3b status header**

In `docs/superpowers/specs/2026-07-02-dext-recommend-03b-recommend-core-impl-design.md`, change the status line:

```markdown
> 状态：实现完成（部分条款被 [3c R3 hardening](2026-07-02-dext-recommend-03c-r3-hardening-design.md) supersedes：§3.1 编排、§4.2 tie-break、§5.2 org_unit 降级、§8 explanation）
```

- [ ] **Step 2: Update 3c status + final test count**

In `docs/superpowers/specs/2026-07-02-dext-recommend-03c-r3-hardening-design.md`, change the status line to `已实现` and append the final test count after running:

Run: `uv run pytest tests/dext_recommend/ -q --co 2>/dev/null | tail -3`
(Use the actual collected/passed count.) Add to the 3c status block:

```markdown
> 实现结果：tests/dext_recommend/ 全模块绿；新增/改写测试 N 条（见 §6.1）。
```

- [ ] **Step 3: Commit docs**

```bash
git add docs/superpowers/specs/2026-07-02-dext-recommend-03b-recommend-core-impl-design.md docs/superpowers/specs/2026-07-02-dext-recommend-03c-r3-hardening-design.md
git -c commit.gpgsign=false commit -m "docs(rec): mark 3c implemented + 3b superseded clauses"
```

---

## Self-Review

**Spec coverage check** (spec §4 workstreams → tasks):
- W3 (§4.1) → Task 1 ✓
- W4a (§4.4 hybrid_recall contract) → Task 2 ✓
- W2 (§4.2) → Task 3 ✓
- W1 (§4.3) → Task 4 ✓
- W4b (§4.4 tie_break + steps_used) → Task 5 ✓
- W5 (§4.5) → Task 6 ✓
- W7 (§4.7) → Task 7 ✓
- W6-a (§4.6.1–4.6.2) → Task 8 ✓
- W6-b (§4.6.3–4.6.4) → Task 9 ✓
- docs → Task 10 ✓

All §6.1 acceptance tests mapped to a task step (same_field, org_unit, rrf_k, sparse_vector, timeout, steps_used, explanation, classified failures, concurrent isolation, composition seam, default profile, invalid request, unauthorized).

**Placeholder scan:** Several test bodies use `...` placeholders (Tasks 3, 4, 7, 9) where the construction mirrors an existing test pattern. These are intentional "mirror the nearest existing test" instructions — the implementer must read the referenced existing test. This is acceptable per the writing-plans guidance (repeat the pattern reference), but the implementer should treat each `...` as "read the named existing test and mirror its fixture construction."

**Type consistency check:**
- `RecallResult` fields (Task 4) match usage in service (Task 4 Step 5) and the test (Task 4 Step 1).
- `PhaseDiagnostic` (Task 9 Step 2) matches `_resilience.py` (Step 5) and `RecommendResponse` (Step 2).
- `_guarded_async` signature (Task 9 Step 5) matches recall_loop usage (Step 7).
- `validate_request` (Task 7 Step 7) returns `str | None`, used in service (Step 7).
- `ViewerPermissions` default constructor used in Task 7 + Task 9 — consistent.

**Known soft spots** (implementer should be alert, not blocking):
- Task 4 Step 6: the existing `test_recommend_oversample_step_progression` may assert cumulative survivors and will need updating to current-step-authoritative semantics.
- Task 7 Step 9: every existing test using `include_contacts=True` or `review_policy="include_downranked"` needs an explicit `viewer_permissions` kwarg — a search-and-fix pass.
- Task 9 Step 6: the `_error_response` helper signature changes to accept `phase_diagnostics`; all call sites updated.

The plan is complete and internally consistent.
