"""Configuration for graph-build tooling; intentionally independent of dext."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GraphSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DEXT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    value_validation_root: Path = Path("data/value-validation")
    catalog_path: Path = Path("data/catalog/catalog.db")
    catalog_backup_retention: int = 2
    source_data_dir: Path = Path("data/universities")
    seed_path: Path = Path("entrances.yaml")
    taxonomy_path: Path = Path("taxonomy/research-topics.yaml")
    qdrant_url: str = "http://127.0.0.1:6333"
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_database: str = "neo4j"
    neo4j_username: str = ""
    neo4j_password: str = Field(default="", repr=False)
    neo4j_max_retry_seconds: float = 30.0

    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = Field(default="", repr=False)
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024
    embedding_max_input_tokens: int = 8192
    embedding_request_batch: int = 16
    embedding_max_concurrency: int = 8
    embedding_timeout_seconds: float = 60.0
    embedding_max_retries: int = 5
    embedding_queue_maxsize: int = 8
    embedding_passage_prefix: str = ""
    embedding_query_prefix: str = ""
    bm25_tokenizer_version: str = "bm25-simple-v1"

    topic_llm_api_key: str = Field(default="", repr=False)
    topic_llm_base_url: str = "https://api.deepseek.com"
    topic_llm_model: str = "deepseek-v4-flash"
    topic_llm_timeout_seconds: float = 90.0
    topic_llm_max_retries: int = 2
    topic_candidate_top_k: int = 8
    topic_link_concurrency: int = 8
    topic_merge_min_score: float = 0.90

    tokenizer_model: str = "BAAI/bge-m3"
    tokenizer_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    profile_max_tokens: int = 4096
    source_read_batch: int = 100
    qdrant_upsert_batch: int = 64

    build_read_batch: int = 100
    build_write_queue: int = 2
    build_max_rss_mb: int = 1024
    build_min_free_disk_mb: int = 512
    build_min_source_retention_ratio: float = 0.80
    curation_queue: int = 16
    build_neo4j_batch: int = 200

    @field_validator("embedding_base_url")
    @classmethod
    def base_url_must_end_at_v1(cls, value: str) -> str:
        normalized = value.rstrip("/")
        if not normalized.endswith("/v1"):
            raise ValueError("DEXT_EMBEDDING_BASE_URL must end at /v1")
        return normalized

    @field_validator(
        "embedding_dimension",
        "catalog_backup_retention",
        "embedding_max_input_tokens",
        "embedding_request_batch",
        "embedding_max_concurrency",
        "embedding_max_retries",
        "embedding_queue_maxsize",
        "topic_llm_max_retries",
        "topic_candidate_top_k",
        "topic_link_concurrency",
        "profile_max_tokens",
        "source_read_batch",
        "qdrant_upsert_batch",
        "build_read_batch",
        "build_write_queue",
        "build_max_rss_mb",
        "build_min_free_disk_mb",
        "curation_queue",
        "build_neo4j_batch",
    )
    @classmethod
    def positive_integers(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("configuration value must be positive")
        return value

    @field_validator("build_min_source_retention_ratio")
    @classmethod
    def retention_ratio_is_fraction(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("DEXT_BUILD_MIN_SOURCE_RETENTION_RATIO must be in (0, 1]")
        return value

    @field_validator("topic_merge_min_score")
    @classmethod
    def topic_score_is_fraction(cls, value: float) -> float:
        if not 0 < value <= 1:
            raise ValueError("DEXT_TOPIC_MERGE_MIN_SCORE must be in (0, 1]")
        return value

    def safe_snapshot(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"embedding_api_key", "topic_llm_api_key", "neo4j_password"},
        )


__all__ = ["GraphSettings"]
