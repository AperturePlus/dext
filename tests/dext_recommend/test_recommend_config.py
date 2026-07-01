# tests/test_recommend_config.py
from __future__ import annotations

from pathlib import Path

from dext_recommend import RecommendSettings


def test_recommend_settings_defaults():
    s = RecommendSettings()
    assert s.qdrant_alias == "dext_professors_current"
    assert s.total_timeout == 30
    assert s.oversample_default == 200
    assert s.oversample_max == 1000


def test_recommend_settings_safe_snapshot_excludes_api_keys():
    s = RecommendSettings(
        embedding_api_key="secret-embed",
        llm_api_key="secret-llm",
    )
    snap = s.safe_snapshot()
    assert "secret-embed" not in str(snap)
    assert "secret-llm" not in str(snap)
    assert "embedding_api_key" not in snap
    assert "llm_api_key" not in snap
    # but operational fields are present
    assert snap["qdrant_alias"] == "dext_professors_current"


def test_recommend_settings_paths_are_path_objects():
    s = RecommendSettings()
    assert isinstance(s.catalog_path, Path)


def test_recommend_settings_accepts_canonical_neo4j_url(monkeypatch):
    monkeypatch.setenv("DEXT_RECOMMEND_NEO4J_URL", "bolt://canonical:7687")
    monkeypatch.setenv("DEXT_RECOMMEND_NEO4J_URI", "bolt://legacy:7687")
    settings = RecommendSettings(_env_file=None)
    assert settings.neo4j_uri == "bolt://canonical:7687"


def test_recommend_settings_accepts_legacy_neo4j_uri(monkeypatch):
    monkeypatch.delenv("DEXT_RECOMMEND_NEO4J_URL", raising=False)
    monkeypatch.setenv("DEXT_RECOMMEND_NEO4J_URI", "bolt://legacy:7687")
    settings = RecommendSettings(_env_file=None)
    assert settings.neo4j_uri == "bolt://legacy:7687"
