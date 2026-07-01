"""VectorSearchPort — hybrid recall + alias/count readback (foundations §5).

All methods take the request-pinned ActiveBuildSnapshot explicitly so a
mid-request alias/pointer switch cannot mix new+old build versions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from dext_recommend.models import RecommendationFilters
from dext_recommend.readiness import ActiveBuildSnapshot


@dataclass(frozen=True, slots=True)
class VectorHit:
    entity_id: str
    score: float
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AliasReadback:
    alias: str
    target_collection: str
    build_id: str | None
    payload_schema_version: int | None


@runtime_checkable
class VectorSearchPort(Protocol):
    def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters,
        oversample: int,
        profile_version: str,
    ) -> list[VectorHit]: ...

    def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback: ...

    def count_readback(
        self, snapshot: ActiveBuildSnapshot, filter: dict | None = None,
    ) -> int: ...


__all__ = ["AliasReadback", "VectorHit", "VectorSearchPort"]
