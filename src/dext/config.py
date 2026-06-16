"""Runtime configuration loaded from env / .env / defaults.

Single source of truth for all tunables. Pure data: reading this never
touches the DB, network, or LLM. A missing DEEPSEEK_API_KEY is NOT an error
here — SP5/SP7 validate it right before the first LLM call.
"""

from __future__ import annotations

import functools
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEXT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    data_dir: Path = Path("data/universities")
    seed_path: Path = Path("entrances.yaml")  # falls back to assets/entrances.yaml in dext.seed

    # Fetch bridge server (contract-fixed port, but host/port configurable)
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 21520
    fetch_timeout_seconds: int = 60

    # LLM (DeepSeek / OpenAI chat-completions format)
    deepseek_api_key: str = Field(default="", validation_alias="DEEPSEEK_API_KEY")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_enable_thinking: bool = True  # V4 enables thinking by default
    llm_reasoning_effort: str = "high"  # first-pass effort tier
    llm_reasoning_effort_retry: str = "max"  # escalated on strict retry
    llm_max_page_tokens: int = 24000  # page-text truncation budget
    llm_workers: int = 6  # compatibility/summary total; runtime uses the split pools below
    decision_workers: int = 3
    extract_workers: int = 3
    invalid_json_max_retry: int = 2

    # Scheduling / retry
    max_depth: int = 4
    max_attempts: int = 3
    followup_page_limit: int = 36
    facet_node_budget: int = 150  # per-org_unit facet/list/pagination node cap (deterministic anti-explosion backstop)
    attempt_penalty: float = 5.0

    # Logging
    log_level: str = "INFO"


@functools.lru_cache
def get_settings() -> Settings:
    """Return the cached process-wide Settings singleton."""
    return Settings()
