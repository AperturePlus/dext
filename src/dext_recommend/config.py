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
        populate_by_name=True,
    )

    # Published-artifact locations
    catalog_path: Path = Field(
        default=Path("data/catalog/catalog.db"),
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_CATALOG_PATH",
            "DEXT_CATALOG_PATH",
        ),
    )
    qdrant_url: str = Field(
        default="http://127.0.0.1:6333",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_QDRANT_URL",
            "DEXT_QDRANT_URL",
        ),
    )
    qdrant_alias: str = "dext_professors_current"
    neo4j_uri: str = Field(
        default="bolt://127.0.0.1:7687",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_NEO4J_URL",
            "DEXT_RECOMMEND_NEO4J_URI",
            "DEXT_NEO4J_URI",
        ),
    )
    neo4j_database: str = Field(
        default="neo4j",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_NEO4J_DATABASE",
            "DEXT_NEO4J_DATABASE",
        ),
    )
    neo4j_username: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_NEO4J_USERNAME",
            "DEXT_NEO4J_USERNAME",
        ),
    )
    neo4j_password: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_NEO4J_PASSWORD",
            "DEXT_NEO4J_PASSWORD",
        ),
    )

    # Embedding (must align with ACTIVE build fingerprint)
    embedding_provider: str = Field(
        default="siliconflow",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_EMBEDDING_PROVIDER",
            "DEXT_EMBEDDING_PROVIDER",
        ),
    )
    embedding_model: str = Field(
        default="BAAI/bge-m3",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_EMBEDDING_MODEL",
            "DEXT_EMBEDDING_MODEL",
        ),
    )
    embedding_api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_EMBEDDING_API_KEY",
            "DEXT_EMBEDDING_API_KEY",
        ),
    )
    embedding_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_EMBEDDING_BASE_URL",
            "DEXT_EMBEDDING_BASE_URL",
        ),
    )
    embedding_query_prefix: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_EMBEDDING_QUERY_PREFIX",
            "DEXT_EMBEDDING_QUERY_PREFIX",
        ),
    )
    bm25_tokenizer_version: str = Field(
        default="bm25-simple-v1",
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_BM25_TOKENIZER_VERSION",
            "DEXT_BM25_TOKENIZER_VERSION",
        ),
    )
    embedding_timeout: float = Field(default=10.0, gt=0.0)
    embedding_max_retries: int = Field(default=1, ge=0, le=5)

    # LLM (constrained generation)
    llm_api_key: SecretStr = Field(
        default_factory=lambda: SecretStr(""),
        validation_alias=AliasChoices(
            "DEXT_RECOMMEND_LLM_API_KEY",
            "DEXT_LLM_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
    )
    llm_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("DEXT_RECOMMEND_LLM_BASE_URL", "DEXT_LLM_BASE_URL"),
    )
    llm_model: str = Field(
        default="deepseek-v4-flash",
        validation_alias=AliasChoices("DEXT_RECOMMEND_LLM_MODEL", "DEXT_LLM_MODEL"),
    )
    llm_max_retries: int = Field(
        default=1,
        ge=0,
        le=5,
        validation_alias=AliasChoices("DEXT_RECOMMEND_LLM_MAX_RETRIES", "DEXT_LLM_MAX_RETRIES"),
    )
    llm_timeout: float = Field(
        default=20.0,
        gt=0.0,
        validation_alias=AliasChoices("DEXT_RECOMMEND_LLM_TIMEOUT", "DEXT_LLM_TIMEOUT"),
    )

    # Profile paths
    ranking_profile_path: Path = Path("data/recommend/ranking-profile.json")
    generation_profile_path: Path = Path("data/recommend/generation-profile.json")

    # Timeouts / limits (overview §6.6, §11)
    total_timeout: float = Field(default=90.0, gt=0.0)
    oversample_default: int = 200
    oversample_max: int = 1000
    query_max_chars: int = Field(default=4096, gt=0)
    limit_max: int = Field(default=50, gt=0)

    # Readiness (R2)
    readiness_readback_timeout: float = Field(default=5.0, gt=0.0)
    readiness_sample_size: int = Field(default=50, ge=0)
    coverage_threshold_org_unit_ids: float = Field(default=0.95, gt=0.0, le=1.0)
    coverage_threshold_profile_hash: float = Field(default=0.99, gt=0.0, le=1.0)
    coverage_threshold_role_status: float = Field(default=0.95, gt=0.0, le=1.0)
    coverage_threshold_eligibility: float = Field(default=0.95, gt=0.0, le=1.0)

    # Live runtime lifecycle
    runtime_refresh_interval: float = Field(default=60.0, gt=0.0)
    runtime_snapshot_max_age: float = Field(default=300.0, gt=0.0)
    runtime_startup_timeout: float = Field(default=30.0, gt=0.0)

    # Live Qdrant/Neo4j clients
    qdrant_timeout: float = Field(default=10.0, gt=0.0)
    qdrant_query_limit_max: int = Field(default=1000, ge=1)
    qdrant_payload_schema_version: int = Field(default=2, ge=1)
    neo4j_max_connection_lifetime: float | None = Field(default=None, gt=0.0)

    # R4 catalog fact reads
    fact_read_timeout: float = Field(default=5.0, gt=0.0)
    fact_chunk_size: int = Field(default=900, ge=1)

    def safe_snapshot(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"embedding_api_key", "llm_api_key", "neo4j_password"},
        )


__all__ = ["RecommendSettings"]
