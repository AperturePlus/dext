"""QueryEmbeddingPort — encode query with ACTIVE-build embedding settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dext_recommend.readiness import ActiveBuildSnapshot


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vector: list[float]
    embedding_fingerprint: str
    sparse_vector: dict | None = None   # {indices: [...], values: [...]}


@runtime_checkable
class QueryEmbeddingPort(Protocol):
    def embed(self, snapshot: ActiveBuildSnapshot, query_text: str) -> EmbeddingResult:
        ...


__all__ = ["EmbeddingResult", "QueryEmbeddingPort"]
