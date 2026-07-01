"""QueryEmbeddingPort — encode query with ACTIVE-build embedding settings."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dext_recommend.readiness import ActiveBuildSnapshot
from dext_recommend._immutable import freeze_mapping


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    embedding_fingerprint: str
    sparse_vector: Mapping | None = None   # {indices: [...], values: [...]}

    def __post_init__(self) -> None:
        object.__setattr__(self, "vector", tuple(float(value) for value in self.vector))
        if self.sparse_vector is not None:
            object.__setattr__(self, "sparse_vector", freeze_mapping(self.sparse_vector))


@runtime_checkable
class QueryEmbeddingPort(Protocol):
    async def embed(
        self, snapshot: ActiveBuildSnapshot, query_text: str
    ) -> EmbeddingResult:
        ...


__all__ = ["EmbeddingResult", "QueryEmbeddingPort"]
