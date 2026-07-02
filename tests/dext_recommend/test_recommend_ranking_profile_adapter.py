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


async def test_default_ranking_profile_json_loads():
    from pathlib import Path
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.adapters.ranking_profile import RankingProfileAdapter

    adapter = RankingProfileAdapter()
    profile = await adapter.read_profile(Path("data/recommend/ranking-profile.json"))
    assert profile.version == "ranking-v1"
    assert profile.rrf_k == 60
    assert profile.same_field_boost_per_topic == 0.05


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
