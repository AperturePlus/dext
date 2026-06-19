import os
from pathlib import Path

import pytest

from dext.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Isolate every test from ambient DEXT_* / DEEPSEEK_API_KEY env vars,
    so a developer/CI shell that exports them can't cause spurious failures."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    for key in list(os.environ):
        if key.startswith("DEXT_"):
            monkeypatch.delenv(key, raising=False)


def test_defaults_are_sane():
    s = Settings(_env_file=None)
    assert s.data_dir == Path("data/universities")
    assert s.seed_path == Path("entrances.yaml")
    assert s.bridge_host == "127.0.0.1"
    assert s.bridge_port == 21520
    assert s.fetch_timeout_seconds == 60
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.llm_model == "deepseek-v4-flash"
    assert s.llm_enable_thinking is True
    assert s.llm_reasoning_effort == "high"
    assert s.llm_reasoning_effort_retry == "max"
    assert s.llm_max_page_tokens == 24000
    assert s.decision_workers == 3
    assert s.extract_workers == 3
    assert s.invalid_json_max_retry == 2
    assert s.max_depth == 4
    assert s.max_attempts == 3
    assert s.followup_page_limit == 36
    assert s.facet_node_budget == 150
    assert s.attempt_penalty == 5.0
    assert s.log_level == "INFO"
    assert s.probe_redirect_enabled is True
    assert s.probe_status_enabled is True


def test_missing_api_key_does_not_raise(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == ""


def test_deepseek_api_key_read_from_unprefixed_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    s = Settings(_env_file=None)
    assert s.deepseek_api_key == "sk-test-123"


def test_dext_prefixed_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("DEXT_LLM_MODEL", "custom-model")
    monkeypatch.setenv("DEXT_DECISION_WORKERS", "2")
    monkeypatch.setenv("DEXT_EXTRACT_WORKERS", "6")
    monkeypatch.setenv("DEXT_BRIDGE_PORT", "30000")
    s = Settings(_env_file=None)
    assert s.llm_model == "custom-model"
    assert s.decision_workers == 2
    assert s.extract_workers == 6
    assert s.bridge_port == 30000


def test_probe_toggle_env_overrides_defaults(monkeypatch):
    monkeypatch.setenv("DEXT_PROBE_REDIRECT_ENABLED", "false")
    monkeypatch.setenv("DEXT_PROBE_STATUS_ENABLED", "false")
    s = Settings(_env_file=None)
    assert s.probe_redirect_enabled is False
    assert s.probe_status_enabled is False


def test_get_settings_is_cached():
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
    get_settings.cache_clear()
