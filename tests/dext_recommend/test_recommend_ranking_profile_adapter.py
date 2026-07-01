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
