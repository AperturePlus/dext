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
