"""RecommendRequest -> RecommendResponse orchestrator (placeholder; pipeline in R3).

The core holds its four ports via an immutable RecommendDeps. The real pipeline
(query understanding -> embedding -> hybrid recall -> hydration -> rerank ->
explanation -> cards) lands in R3; for now only the wiring shape is fixed so
import boundaries and the fake-port contract can be exercised.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_recommend.models import RecommendRequest, RecommendResponse
from dext_recommend.ports import (
    ActiveSnapshotProvider, ProfessorFactPort, QueryEmbeddingPort, VectorSearchPort,
)


@dataclass(frozen=True, slots=True)
class RecommendDeps:
    snapshot_port: ActiveSnapshotProvider
    embedding_port: QueryEmbeddingPort
    vector_port: VectorSearchPort
    facts_port: ProfessorFactPort


class RecommendationCore:
    def __init__(self, deps: RecommendDeps) -> None:
        self._deps = deps

    @property
    def deps(self) -> RecommendDeps:
        return self._deps

    def recommend(self, request: RecommendRequest) -> RecommendResponse:
        raise NotImplementedError("recommend pipeline implemented in R3")


__all__ = ["RecommendDeps", "RecommendationCore"]
