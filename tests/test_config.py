from pathlib import Path

from dext.config import Settings, get_settings


def test_defaults_are_sane():
    s = Settings(_env_file=None)
    assert s.data_dir == Path("data/universities")
    assert s.seed_path == Path("entrances.yaml")
    assert s.bridge_host == "127.0.0.1"
    assert s.bridge_port == 21520
    assert s.fetch_timeout_seconds == 60
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.llm_model == "deepseek-chat"
    assert s.llm_model_retry == "deepseek-reasoner"
    assert s.llm_enable_thinking is False
    assert s.llm_workers == 4
    assert s.invalid_json_max_retry == 2
    assert s.max_depth == 4
    assert s.max_attempts == 3
    assert s.followup_page_limit == 36
    assert s.attempt_penalty == 5.0
    assert s.log_level == "INFO"


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
    monkeypatch.setenv("DEXT_LLM_WORKERS", "8")
    monkeypatch.setenv("DEXT_BRIDGE_PORT", "30000")
    s = Settings(_env_file=None)
    assert s.llm_model == "custom-model"
    assert s.llm_workers == 8
    assert s.bridge_port == 30000


def test_get_settings_is_cached():
    get_settings.cache_clear()
    a = get_settings()
    b = get_settings()
    assert a is b
    get_settings.cache_clear()
