"""Configuration for dext_recommend; loaded from env / .env / defaults.

All timeouts, limits, and ranking/generation profile paths are configurable.
API keys are read from env only, never serialized into the config object's
loggable representation, the manifest, or logs (overview §6.6, §18).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RecommendSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEXT_RECOMMEND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Published-artifact locations
    catalog_path: Path = Path("data/catalog/catalog.db")
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_alias: str = "dext_professors_current"
    neo4j_uri: str = Field(
        default="bolt://127.0.0.1:7687",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_NEO4J_URL",
            "DEXT_RECOMMEND_NEO4J_URI",
        ),
    )
    neo4j_database: str = "neo4j"
    neo4j_username: str = ""
    neo4j_password: SecretStr = Field(default_factory=SecretStr)

    # Embedding (must align with ACTIVE build fingerprint)
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_api_key: SecretStr = Field(default_factory=SecretStr)

    # LLM (constrained generation)
    llm_api_key: SecretStr = Field(default_factory=SecretStr)
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"

    # Profile paths
    ranking_profile_path: Path = Path("data/recommend/ranking-profile.json")
    generation_profile_path: Path = Path("data/recommend/generation-profile.json")

    # Timeouts / limits (overview §6.6, §11)
    total_timeout: float = 30.0
    oversample_default: int = 200
    oversample_max: int = 1000

    # Readiness (R2)
    readiness_readback_timeout: float = Field(default=5.0, gt=0.0)
    readiness_sample_size: int = Field(default=50, ge=0)
    coverage_threshold_org_unit_ids: float = Field(default=0.95, gt=0.0, le=1.0)
    coverage_threshold_profile_hash: float = Field(default=0.99, gt=0.0, le=1.0)
    coverage_threshold_role_status: float = Field(default=0.95, gt=0.0, le=1.0)
    coverage_threshold_eligibility: float = Field(default=0.95, gt=0.0, le=1.0)

    def safe_snapshot(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"embedding_api_key", "llm_api_key", "neo4j_password"},
        )


__all__ = ["RecommendSettings"]
