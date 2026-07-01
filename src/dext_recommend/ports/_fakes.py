"""Fake ports for unit tests (foundations §6). Test-only; never in composition root."""
from __future__ import annotations

from dext_recommend.ports.build_snapshot import BuildSnapshotPort
from dext_recommend.ports.embedding import EmbeddingResult, QueryEmbeddingPort
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactPort, ViewerPermissions,
)
from dext_recommend.ports.vector_search import (
    AliasReadback, VectorHit, VectorSearchPort,
)
from dext_recommend.readiness import ActiveBuildSnapshot
from dext_recommend.models import RecommendationFilters


class FakeBuildSnapshotPort:
    """Inject a preset snapshot (None simulates no ACTIVE build)."""

    def __init__(self, snapshot: ActiveBuildSnapshot | None) -> None:
        self._snapshot = snapshot

    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        return self._snapshot

    def refresh(self) -> ActiveBuildSnapshot | None:
        return self._snapshot


class FakeQueryEmbeddingPort:
    def __init__(self, vector: list[float], fingerprint: str) -> None:
        self._vector = vector
        self._fingerprint = fingerprint

    def embed(self, snapshot: ActiveBuildSnapshot, query_text: str) -> EmbeddingResult:
        return EmbeddingResult(vector=list(self._vector), embedding_fingerprint=self._fingerprint)


class FakeVectorSearchPort:
    def __init__(
        self,
        hits: list[VectorHit] | None = None,
        alias: AliasReadback | None = None,
        count: int = 0,
    ) -> None:
        self._hits = hits or []
        self._alias = alias
        self._count = count

    def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters | None,
        oversample: int,
        profile_version: str,
    ) -> list[VectorHit]:
        return list(self._hits)

    def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback:
        return self._alias or AliasReadback(
            alias="dext_professors_current", target_collection="phys-1",
            build_id=snapshot.build_id, payload_schema_version=2,
        )

    def count_readback(self, snapshot: ActiveBuildSnapshot, filter: dict | None = None) -> int:
        return self._count


class FakeProfessorFactPort:
    def __init__(
        self,
        details: dict[str, ProfessorDetail] | None = None,
        facts: dict[str, ProfessorFact] | None = None,
    ) -> None:
        self._details = details or {}
        self._facts = facts or {}

    def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        return self._details[entity_id]

    def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]:
        return {eid: self._facts[eid] for eid in entity_ids if eid in self._facts}


__all__ = [
    "FakeBuildSnapshotPort",
    "FakeProfessorFactPort",
    "FakeQueryEmbeddingPort",
    "FakeVectorSearchPort",
]
