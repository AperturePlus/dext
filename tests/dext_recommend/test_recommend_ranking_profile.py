from __future__ import annotations

from pathlib import Path

import pytest

from dext_recommend import FakeRankingProfilePort, RankingProfile, RankingProfilePort
from tests.dext_recommend._recfixtures import ranking_profile_dict


def test_ranking_profile_from_dict_validates_schema():
    p = RankingProfile.from_dict(ranking_profile_dict())
    assert p.version == "r1"
    assert p.weights["semantic_score"] == 0.50
    assert p.rrf_k == 60
    assert p.oversample_steps == (200, 400, 800, 1000)
    assert p.detail_rerank_window == 50
    assert p.detail_fetch_concurrency == 8
    assert p.detail_rerank_window_max == 100
    assert p.match_level_thresholds == {"excellent": 0.75, "strong": 0.55, "possible": 0.35}
    assert p.tie_break == ("score", "semantic_score", "evidence_count", "entity_id")


def test_ranking_profile_rejects_weights_not_summing_to_one():
    bad = ranking_profile_dict()
    bad["weights"] = {
        "semantic_score": 0.50, "topic_statement_score": 0.18,
        "student_fit_score": 0.12, "eligibility_score": 0.08,
        "provenance_score": 0.08, "completeness_score": 0.03,  # 0.01 short
    }
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_non_monotonic_thresholds():
    bad = ranking_profile_dict()
    bad["match_level_thresholds"] = {"excellent": 0.55, "strong": 0.75, "possible": 0.35}
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_window_above_max():
    bad = ranking_profile_dict()
    bad["detail_rerank_window"] = 200
    bad["detail_rerank_window_max"] = 100
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_non_increasing_steps():
    bad = ranking_profile_dict()
    bad["oversample_steps"] = (200, 100, 800, 1000)
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_is_immutable():
    p = RankingProfile.from_dict(ranking_profile_dict())
    with pytest.raises((AttributeError, TypeError)):
        p.weights["semantic_score"] = 0.99
    with pytest.raises((AttributeError, TypeError)):
        p.oversample_steps.append(2000)


async def test_fake_ranking_profile_port_read_profile_returns_preset_and_records():
    p = RankingProfile.from_dict(ranking_profile_dict())
    port = FakeRankingProfilePort(profile=p)
    assert isinstance(port, RankingProfilePort)
    got = await port.read_profile(Path("data/recommend/ranking-profile.json"))
    assert got is p
    assert len(port.read_profile_calls) == 1
    assert port.read_profile_calls[0]["path"] == Path("data/recommend/ranking-profile.json")


async def test_fake_ranking_profile_port_read_profile_raises_on_error():
    from dext_recommend import ReadinessSourceError
    port = FakeRankingProfilePort(error=ReadinessSourceError("ranking", "boom"))
    with pytest.raises(ReadinessSourceError):
        await port.read_profile(Path("x"))


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


# ---- R3c leftover: numeric validation gaps (P2-B) ----


def _base_profile_dict(**over):
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
    base.update(over)
    return base


def test_ranking_profile_rejects_negative_weight():
    """A negative weight still sums to ~1.0 but is invalid; must be rejected."""
    import math
    bad = _base_profile_dict()
    bad["weights"] = {
        "semantic_score": 0.70, "topic_statement_score": 0.18,
        "student_fit_score": 0.12, "eligibility_score": 0.08,
        "provenance_score": 0.08, "completeness_score": -0.16,  # sums to 1.0
    }
    assert math.isclose(sum(bad["weights"].values()), 1.0, abs_tol=0.01)
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_nan_weight():
    """NaN weight poisons comparisons and must be rejected even though sum()
    returns NaN (which fails the != 1.0 check, but for the wrong reason)."""
    bad = _base_profile_dict()
    bad["weights"] = {
        "semantic_score": float("nan"), "topic_statement_score": 0.18,
        "student_fit_score": 0.12, "eligibility_score": 0.08,
        "provenance_score": 0.08, "completeness_score": 0.04,
    }
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_infinity_weight():
    """Infinity weight must be rejected explicitly."""
    bad = _base_profile_dict()
    bad["weights"] = {
        "semantic_score": float("inf"), "topic_statement_score": 0.18,
        "student_fit_score": 0.12, "eligibility_score": 0.08,
        "provenance_score": 0.08, "completeness_score": 0.04,
    }
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_non_positive_oversample_step():
    """oversample_steps must be strictly increasing AND all positive. A step of
    0 (or negative) is invalid even if the sequence is strictly increasing."""
    bad = _base_profile_dict(oversample_steps=(0, 200, 400))
    # strictly increasing holds, but 0 is not a valid oversample
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)


def test_ranking_profile_rejects_negative_oversample_step():
    bad = _base_profile_dict(oversample_steps=(-100, 200, 400))
    with pytest.raises(ValueError):
        RankingProfile.from_dict(bad)
