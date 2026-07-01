from __future__ import annotations

import pytest

from dext_recommend import FakeVectorSearchPort, RecommendationFilters, VectorHit
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.recall import (
    compute_oversample_steps, normalize_rrf, recall_loop,
)

from tests.dext_recommend._recfixtures import ranking_profile_dict, snapshot


def _profile(**over) -> RankingProfile:
    d = ranking_profile_dict(**over)
    return RankingProfile.from_dict(d)


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
    hits = [VectorHit(f"e{i}", 1.0 - i * 0.01, {}) for i in range(50)]
    port = FakeVectorSearchPort(hits=hits)
    out, steps_used = await recall_loop(
        snapshot(), port, [0.1, 0.2],
        RecommendationFilters(), _profile(), oversample_max=1000,
        request_oversample=200, limit=10,
    )
    # first step 200 returns 50 hits >= 10 -> break immediately
    assert len(out) == 50
    assert steps_used == 1
    assert len(port.hybrid_recall_calls) == 1
    assert port.hybrid_recall_calls[0]["oversample"] == 200


async def test_recall_loop_progresses_through_steps_when_insufficient():
    hits = [VectorHit(f"e{i}", 0.5, {}) for i in range(3)]
    port = FakeVectorSearchPort(hits=hits)
    out, steps_used = await recall_loop(
        snapshot(), port, [0.1], RecommendationFilters(),
        _profile(), oversample_max=1000, request_oversample=200, limit=10,
    )
    assert steps_used == 4  # all steps exhausted; only 3 hits returned
    assert len(out) == 3
    assert [c["oversample"] for c in port.hybrid_recall_calls] == [200, 400, 800, 1000]
