"""ActiveBuildSnapshot + ReadinessService (overview §9, readiness spec).

The snapshot is the immutable per-request binding to an ACTIVE build. One
recommendation / detail / match / outreach / compare request uses exactly
one snapshot; refresh never merges old + new. Real build/refresh logic
lands in R2; this module fixes the data shape now so ports can take it as
an explicit parameter (foundations §5).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dext_recommend.errors import RecommendationError


@dataclass(frozen=True, slots=True)
class ActiveBuildSnapshot:
    build_id: str
    catalog_schema_version: int
    neo4j_active_build_id: str
    qdrant_alias_target: str
    qdrant_payload_schema_version: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_fingerprint: str
    taxonomy_version: str | None
    ranking_profile_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    snapshot: ActiveBuildSnapshot | None
    errors: list[RecommendationError]
    payload_coverage: dict[str, "CoverageStat"]


@dataclass(frozen=True, slots=True)
class CoverageStat:
    field: str
    covered: float          # 0..1 fraction
    sample_size: int
    passes: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.covered <= 1.0:
            raise ValueError(
                f"CoverageStat.covered must be in [0.0, 1.0], got {self.covered}"
            )
        if self.sample_size < 0:
            raise ValueError(
                f"CoverageStat.sample_size must be >= 0, got {self.sample_size}"
            )


class ReadinessService:
    """Placeholder; R2 implements real ACTIVE-build construction + checks."""

    def check(self) -> ReadinessReport:
        raise NotImplementedError("readiness implemented in R2")

    def get_snapshot(self) -> ActiveBuildSnapshot:
        raise NotImplementedError("readiness implemented in R2")


__all__ = ["ActiveBuildSnapshot", "CoverageStat", "ReadinessReport", "ReadinessService"]
