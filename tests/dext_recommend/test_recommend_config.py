# tests/test_recommend_config.py
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from dext_recommend import RecommendSettings


def test_recommend_settings_defaults():
    s = RecommendSettings()
    assert s.qdrant_alias == "dext_professors_current"
    assert s.embedding_provider == "siliconflow"
    assert s.embedding_model == "BAAI/bge-m3"
    assert s.embedding_base_url == "https://api.siliconflow.cn/v1"
    assert s.total_timeout == 30
    assert s.oversample_default == 200
    assert s.oversample_max == 1000


def test_recommend_settings_secret_defaults_are_empty_strings():
    s = RecommendSettings(_env_file=None)
    assert s.neo4j_password.get_secret_value() == ""
    assert s.embedding_api_key.get_secret_value() == ""
    assert s.llm_api_key.get_secret_value() == ""


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


def test_recommend_settings_accepts_graph_neo4j_env_aliases(monkeypatch):
    monkeypatch.delenv("DEXT_RECOMMEND_NEO4J_URL", raising=False)
    monkeypatch.delenv("DEXT_RECOMMEND_NEO4J_URI", raising=False)
    monkeypatch.delenv("DEXT_RECOMMEND_NEO4J_USERNAME", raising=False)
    monkeypatch.delenv("DEXT_RECOMMEND_NEO4J_PASSWORD", raising=False)
    monkeypatch.setenv("DEXT_NEO4J_URI", "bolt://graph-env:7687")
    monkeypatch.setenv("DEXT_NEO4J_USERNAME", "neo4j-user")
    monkeypatch.setenv("DEXT_NEO4J_PASSWORD", "neo4j-pass")
    settings = RecommendSettings(_env_file=None)
    assert settings.neo4j_uri == "bolt://graph-env:7687"
    assert settings.neo4j_username == "neo4j-user"
    assert settings.neo4j_password.get_secret_value() == "neo4j-pass"


def test_recommend_settings_accepts_graph_embedding_env_aliases(monkeypatch):
    monkeypatch.delenv("DEXT_RECOMMEND_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("DEXT_RECOMMEND_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("DEXT_RECOMMEND_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("DEXT_RECOMMEND_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.setenv("DEXT_EMBEDDING_PROVIDER", "graph-provider")
    monkeypatch.setenv("DEXT_EMBEDDING_MODEL", "graph-model")
    monkeypatch.setenv("DEXT_EMBEDDING_API_KEY", "graph-key")
    monkeypatch.setenv("DEXT_EMBEDDING_BASE_URL", "https://embedding.test/v1")
    settings = RecommendSettings(_env_file=None)
    assert settings.embedding_provider == "graph-provider"
    assert settings.embedding_model == "graph-model"
    assert settings.embedding_api_key.get_secret_value() == "graph-key"
    assert settings.embedding_base_url == "https://embedding.test/v1"


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
