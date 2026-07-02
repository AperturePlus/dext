"""In-memory fake ports for R2+ unit tests; never used in composition roots."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from typing import TYPE_CHECKING

from dext_recommend.models import ConversationContext, ConversationSummary, RecommendationFilters
from dext_recommend.ports.conversation_store import TurnSnapshot
from dext_recommend.ports.embedding import EmbeddingResult
from dext_recommend.ports.professor_facts import (
    ProfessorDetail,
    ProfessorFact,
    ProfessorFactNotFound,
    ViewerPermissions,
)
from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation,
    GraphReleaseObservation,
    ProfessorReleaseSample,
    ReadinessSourceError,
    VectorReleaseObservation,
)

if TYPE_CHECKING:
    from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.ports.vector_search import AliasReadback, VectorHit
from dext_recommend.readiness import ActiveBuildSnapshot

from dext_recommend.core.generation_profile import RecommendGenerationProfile


class FakeActiveSnapshotProvider:
    def __init__(self, snapshot: ActiveBuildSnapshot | None) -> None:
        self._snapshot = snapshot
        self.get_snapshot_calls = 0

    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        self.get_snapshot_calls += 1
        return self._snapshot


class FakeCatalogReleasePort:
    def __init__(
        self,
        observation: CatalogReleaseObservation | None = None,
        samples: tuple[ProfessorReleaseSample, ...] | list[ProfessorReleaseSample] = (),
        error: ReadinessSourceError | None = None,
    ) -> None:
        self._observation = observation
        self._samples = tuple(samples)
        self._error = error

    async def read_active(self) -> CatalogReleaseObservation | None:
        if self._error is not None:
            raise self._error
        return self._observation

    async def read_samples(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[ProfessorReleaseSample, ...]:
        if self._error is not None:
            raise self._error
        wanted = set(sample_ids)
        return tuple(sample for sample in self._samples if sample.entity_id in wanted)


class FakeVectorReleasePort:
    def __init__(
        self,
        observation: VectorReleaseObservation | None = None,
        error: ReadinessSourceError | None = None,
    ) -> None:
        self._observation = observation
        self._error = error

    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> VectorReleaseObservation | None:
        if self._error is not None:
            raise self._error
        return self._observation


class FakeGraphReleasePort:
    def __init__(
        self,
        observation: GraphReleaseObservation | None = None,
        error: ReadinessSourceError | None = None,
    ) -> None:
        self._observation = observation
        self._error = error

    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> GraphReleaseObservation | None:
        if self._error is not None:
            raise self._error
        return self._observation


class FakeRankingProfilePort:
    def __init__(
        self,
        version: str = "ranking-v1",
        profile: "RankingProfile | None" = None,
        error: ReadinessSourceError | None = None,
    ) -> None:
        self._version = version
        self._profile = profile
        self._error = error
        self.read_profile_calls: list[dict] = []

    async def read_version(self, path: Path) -> str:
        if self._error is not None:
            raise self._error
        return self._version

    async def read_profile(self, path: Path) -> "RankingProfile":
        if self._error is not None:
            raise self._error
        self.read_profile_calls.append({"path": path})
        if self._profile is None:
            raise ReadinessSourceError("ranking", "no profile preset")
        return self._profile


class FakeQueryEmbeddingPort:
    def __init__(self, vector: list[float] | tuple[float, ...], fingerprint: str) -> None:
        self._vector = tuple(float(value) for value in vector)
        self._fingerprint = fingerprint

    async def embed(
        self, snapshot: ActiveBuildSnapshot, query_text: str
    ) -> EmbeddingResult:
        return EmbeddingResult(
            vector=self._vector,
            embedding_fingerprint=self._fingerprint,
        )


class FakeVectorSearchPort:
    def __init__(
        self,
        hits: list[VectorHit] | tuple[VectorHit, ...] | None = None,
        alias: AliasReadback | None = None,
        count: int = 0,
    ) -> None:
        self._hits = tuple(hits or ())
        self._alias = alias
        self._count = int(count)
        self.hybrid_recall_calls: list[dict] = []

    async def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters | None,
        oversample: int,
        profile_version: str,
        *,
        rrf_k: int,
        sparse_vector: Mapping | None = None,
    ) -> list[VectorHit]:
        self.hybrid_recall_calls.append({
            "oversample": oversample,
            "filters": filters,
            "profile_version": profile_version,
            "snapshot_build_id": snapshot.build_id,
            "rrf_k": rrf_k,
            "sparse_vector": dict(sparse_vector) if sparse_vector else None,
        })
        return list(self._hits)

    async def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback:
        return self._alias or AliasReadback(
            alias="dext_professors_current",
            target_collection="phys-1",
            build_id=snapshot.build_id,
            payload_schema_version=2,
        )

    async def count_readback(
        self, snapshot: ActiveBuildSnapshot, filter: dict | None = None,
    ) -> int:
        return self._count


class FakeProfessorFactPort:
    def __init__(
        self,
        details: dict[str, ProfessorDetail] | None = None,
        facts: dict[str, ProfessorFact] | None = None,
    ) -> None:
        self._details = dict(details or {})
        self._facts = dict(facts or {})
        self.hydrate_calls: list[dict] = []
        self.get_detail_calls: list[dict] = []

    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        self.get_detail_calls.append({
            "entity_id": entity_id, "include_contacts": include_contacts,
            "snapshot_build_id": snapshot.build_id,
            "viewer_permissions": viewer_permissions,
        })
        try:
            return self._details[entity_id]
        except KeyError as exc:
            raise ProfessorFactNotFound(entity_id, snapshot.build_id) from exc

    async def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]:
        self.hydrate_calls.append({
            "entity_ids": list(entity_ids), "snapshot_build_id": snapshot.build_id,
        })
        return {eid: self._facts[eid] for eid in entity_ids if eid in self._facts}


class FakeRecommendGenerationProfilePort:
    def __init__(self, profile: "RecommendGenerationProfile") -> None:
        self._profile = profile
        self.read_profile_calls: list[dict] = []

    async def read_profile(self, path: Path) -> "RecommendGenerationProfile":
        self.read_profile_calls.append({"path": path})
        return self._profile


class FakeConversationStorePort:
    def __init__(self, *, initial=None, summaries=None) -> None:
        self._contexts = dict(initial or {})
        self._summaries = dict(summaries or {})
        self.saved_turns: list[tuple[str, str, ConversationContext, TurnSnapshot]] = []

    async def load_context(self, session_id: str, turn_id: str | None):
        return self._contexts.get((session_id, turn_id))

    async def load_summary(self, session_id: str, through_turn_id: str | None):
        return self._summaries.get((session_id, through_turn_id))

    async def save_turn(self, session_id: str, turn_id: str,
                        context: ConversationContext, snapshot: TurnSnapshot) -> None:
        self._contexts[(session_id, turn_id)] = context
        self.saved_turns.append((session_id, turn_id, context, snapshot))

    async def list_prior_entity_ids(self, session_id: str, limit: int = 50):
        result: list[str] = []
        seen: set[str] = set()
        for sid, _tid, _ctx, snapshot in self.saved_turns:
            if sid != session_id:
                continue
            for entity_id in snapshot.result_entity_ids:
                if entity_id not in seen:
                    seen.add(entity_id)
                    result.append(entity_id)
                if len(result) >= limit:
                    return tuple(result)
        return tuple(result)

    async def resolve_fork(self, main_session_id: str, source_turn_id: str):
        return self._contexts.get((main_session_id, source_turn_id))


__all__ = [
    "FakeActiveSnapshotProvider",
    "FakeCatalogReleasePort",
    "FakeConversationStorePort",
    "FakeGraphReleasePort",
    "FakeProfessorFactPort",
    "FakeQueryEmbeddingPort",
    "FakeRankingProfilePort",
    "FakeRecommendGenerationProfilePort",
    "FakeVectorReleasePort",
    "FakeVectorSearchPort",
]
