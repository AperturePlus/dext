from __future__ import annotations

import pytest

from dext_recommend import FakeVectorSearchPort, RecommendationFilters, VectorHit
from dext_recommend.core.intent import RecommendRoute
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.recall import (
    RecallResult, compute_oversample_steps, normalize_rrf, recall_loop,
)
from dext_recommend.core._resilience import RecommendExecutionContext
from dext_recommend.ports._fakes import FakeProfessorFactPort
from dext_recommend.ports.professor_facts import ProfessorFact

from tests.dext_recommend._recfixtures import ranking_profile_dict, snapshot


def _profile(**over) -> RankingProfile:
    d = ranking_profile_dict(**over)
    return RankingProfile.from_dict(d)


def _route() -> RecommendRoute:
    return RecommendRoute(
        intent="new_search", exclude_entity_ids=(), anchor_entity_id=None,
        refine_merge=False, unsupported=None, warnings=(),
    )


def _fact(eid: str, *, role: str = "active") -> ProfessorFact:
    return ProfessorFact(
        entity_id=eid, display_name=eid, university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="any",
        phd_eligibility="any", role_status=role, profile_url=None,
        profile_hash=None, research_summary=None,
    )


def _facts_port_for(eids) -> FakeProfessorFactPort:
    return FakeProfessorFactPort(facts={eid: _fact(eid) for eid in eids})


def test_compute_oversample_steps_starts_at_request_oversample():
    p = _profile()
    steps = compute_oversample_steps(200, p, oversample_max=1000)
    assert steps == (200, 400, 800, 1000)


def test_compute_oversample_steps_clamps_to_max():
    p = _profile()
    steps = compute_oversample_steps(500, p, oversample_max=700)
    # profile steps (200,400,800,1000); first >= 500 is 800 but 800 > 700 max,
    # so no profile step fits -> clamp to min(request_oversample, oversample_max)=500.
    assert steps == (500,)


def test_compute_oversample_steps_caps_to_max_value():
    p = _profile()
    steps = compute_oversample_steps(100, p, oversample_max=500)
    assert all(s <= 500 for s in steps)
    assert steps[-1] <= 500


def test_normalize_rrf_single_candidate_is_one():
    hits = [VectorHit("e1", 0.5, {})]
    out = normalize_rrf(hits)
    assert out == {"e1": 1.0}


def test_normalize_rrf_empty_is_empty():
    assert normalize_rrf([]) == {}


def test_normalize_rrf_multiple_candidates_normalized_to_unit():
    hits = [VectorHit("e1", 1.0, {}), VectorHit("e2", 0.5, {})]
    out = normalize_rrf(hits)
    assert out["e1"] == 1.0
    assert out["e2"] == 0.0


def test_normalize_rrf_all_equal_scores_avoid_divzero():
    hits = [VectorHit("e1", 0.7, {}), VectorHit("e2", 0.7, {})]
    out = normalize_rrf(hits)
    assert out["e1"] == 1.0
    assert out["e2"] == 1.0


async def test_recall_loop_breaks_when_enough_hits():
    """Step 200 yields 50 hits with active facts; survivors >= limit -> break
    after step 1 (current-step authoritative)."""
    hits = [VectorHit(f"e{i}", 1.0 - i * 0.01, {}) for i in range(50)]
    port = FakeVectorSearchPort(hits=hits)
    facts_port = _facts_port_for(h.entity_id for h in hits)
    result = await recall_loop(
        snapshot(), port, [0.1, 0.2], RecommendationFilters(), _profile(),
        facts_port=facts_port, route=_route(), coverage_flags={},
        review_policy="exclude", embedding_sparse_vector=None,
        oversample_max=1000, request_oversample=200, limit=10,
        ctx=RecommendExecutionContext(),
    )
    assert isinstance(result, RecallResult)
    # 50 post-filter survivors >= 10 -> break immediately
    assert result.steps_used == 1
    assert len(result.survivors) == 50
    assert len(port.hybrid_recall_calls) == 1
    assert port.hybrid_recall_calls[0]["oversample"] == 200


async def test_recall_loop_progresses_through_steps_when_insufficient():
    """3 hits per step, limit 10 -> all 4 steps used; final survivors are the
    current (last) step's 3 hits, not cumulative."""
    hits = [VectorHit(f"e{i}", 0.5, {}) for i in range(3)]
    port = FakeVectorSearchPort(hits=hits)
    facts_port = _facts_port_for(h.entity_id for h in hits)
    result = await recall_loop(
        snapshot(), port, [0.1], RecommendationFilters(),
        _profile(), facts_port=facts_port, route=_route(), coverage_flags={},
        review_policy="exclude", embedding_sparse_vector=None,
        oversample_max=1000, request_oversample=200, limit=10,
        ctx=RecommendExecutionContext(),
    )
    assert result.steps_used == 4  # all steps exhausted; 3 survivors (< limit)
    assert len(result.survivors) == 3  # current-step authoritative, not cumulative
    assert [c["oversample"] for c in port.hybrid_recall_calls] == [200, 400, 800, 1000]
    # fact cache dedup: hydrate only on first step (same 3 ids each step)
    assert len(facts_port.hydrate_calls) == 1


async def test_recall_loop_filters_per_step_and_dedups_hydrate():
    from dext_recommend.core.recall import recall_loop, RecallResult
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports._fakes import FakeProfessorFactPort
    from dext_recommend.ports.professor_facts import ProfessorFact

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
        ctx=RecommendExecutionContext(),
    )
    assert isinstance(result, RecallResult)
    assert result.steps_used == 2  # step 100 had 0 survivors (e1 excluded) -> continued
    assert [h.entity_id for h in result.survivors] == ["e2"]  # current step authoritative
    # e1 hydrated once (cached), not re-hydrated at step 200
    assert sum(len(c["entity_ids"]) for c in facts_port.hydrate_calls) == 2  # e1 then e2
