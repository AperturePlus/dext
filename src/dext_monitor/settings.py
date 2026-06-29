"""Configuration for the decoupled dext monitor service."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MonitorSettings(BaseSettings):
    """Settings for the read-only monitor service.

    The monitor intentionally shares only the catalog path convention with graph
    tooling. It must not depend on graph workflow code.
    """

    model_config = SettingsConfigDict(
        env_prefix="DEXT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    catalog_path: Path = Path("data/catalog/catalog.db")
    monitor_host: str = "localhost"
    monitor_port: int = 21530
    monitor_static_dir: Path = Path("webui/dist")
    monitor_poll_seconds: int = 5
    monitor_graph_preview_limit: int = 300

    @field_validator("monitor_port", "monitor_poll_seconds", "monitor_graph_preview_limit")
    @classmethod
    def positive_integer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("monitor numeric settings must be positive")
        return value


__all__ = ["MonitorSettings"]
