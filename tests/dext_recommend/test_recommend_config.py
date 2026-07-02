# tests/test_recommend_config.py
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_live_runtime_settings_defaults_and_secret_exclusion():
    s = RecommendSettings(
        embedding_api_key="embed-secret",
        llm_api_key="llm-secret",
        neo4j_password="neo-secret",
    )
    assert s.runtime_refresh_interval == 60.0
    assert s.runtime_snapshot_max_age == 300.0
    assert s.runtime_startup_timeout == 30.0
    assert s.embedding_timeout == 10.0
    assert s.embedding_max_retries == 1
    assert s.qdrant_timeout == 10.0
    assert s.qdrant_payload_schema_version == 2
    assert s.llm_timeout == 20.0
    snapshot = s.safe_snapshot()
    assert not {"embedding_api_key", "llm_api_key", "neo4j_password"} & snapshot.keys()
    assert "embed-secret" not in str(snapshot)
    assert "llm-secret" not in str(snapshot)
    assert "neo-secret" not in str(snapshot)
