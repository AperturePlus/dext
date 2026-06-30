"""Qdrant sink for isolated stage-4 professor semantic collections."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

from dext_graph.models import ValueValidationError

_SENSITIVE_PAYLOAD_FIELDS = {"name", "email", "phone"}
_INDEX_FIELDS = (
    "entity_id",
    "build_id",
    "university_id",
    "org_unit_ids",
    "role_status",
    "master_eligibility",
    "phd_eligibility",
    "city",
    "topic_ids",
    "method_topic_ids",
    "application_domain_topic_ids",
    "task_topic_ids",
)
CURRENT_PROFESSOR_ALIAS = "dext_professors_current"


@dataclass(frozen=True)
class ProfessorVectorPoint:
    entity_id: str
    dense: list[float]
    sparse: dict[str, list[int] | list[float]]
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProfessorQueryHit:
    entity_id: str
    score: float
    payload: dict[str, Any]


def professor_collection_name(build_id: str) -> str:
    if not build_id or any(character.isspace() for character in build_id):
        raise ValueValidationError(f"invalid build ID for Qdrant collection: {build_id!r}")
    return f"dext_professors__{build_id}"


class ProfessorQdrant:
    def __init__(self, url: str, *, client: Any | None = None) -> None:
        if client is None:
            from qdrant_client import AsyncQdrantClient

            client = AsyncQdrantClient(url=url)
            self._owns_client = True
        else:
            self._owns_client = False
        self._client = client

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def __aenter__(self) -> "ProfessorQdrant":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    @staticmethod
    def _validate_collection_name(name: str) -> None:
        if not name.startswith("dext_professors__") or name == CURRENT_PROFESSOR_ALIAS:
            raise ValueValidationError(f"refusing non-professor Qdrant collection: {name}")

    @classmethod
    def _validate_read_name(cls, name: str) -> None:
        if name == CURRENT_PROFESSOR_ALIAS:
            return
        cls._validate_collection_name(name)

    @staticmethod
    def _query_filter(filters: Mapping[str, object] | None) -> Any | None:
        if not filters:
            return None
        from qdrant_client import models

        conditions = []
        for field, value in sorted(filters.items()):
            if field not in _INDEX_FIELDS:
                raise ValueValidationError(f"unsupported professor filter: {field}")
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                values = list(value)
                if not values:
                    raise ValueValidationError(f"professor filter {field} cannot be empty")
                match = models.MatchAny(any=values)
            else:
                match = models.MatchValue(value=value)
            conditions.append(models.FieldCondition(key=field, match=match))
        return models.Filter(must=conditions)

    async def create_collection(self, name: str, *, dimension: int) -> None:
        from qdrant_client import models

        self._validate_collection_name(name)
        if await self._client.collection_exists(name):
            return
        await self._client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": models.VectorParams(
                    size=dimension,
                    distance=models.Distance.COSINE,
                    on_disk=True,
                )
            },
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )
        for field in _INDEX_FIELDS:
            await self._client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, name: str, points: list[ProfessorVectorPoint]) -> None:
        from qdrant_client import models

        self._validate_collection_name(name)
        structs = []
        for point in points:
            payload = {
                key: value
                for key, value in point.payload.items()
                if key not in _SENSITIVE_PAYLOAD_FIELDS
            }
            structs.append(
                models.PointStruct(
                    id=point.entity_id,
                    vector={
                        "dense": point.dense,
                        "sparse": models.SparseVector(
                            indices=[int(item) for item in point.sparse.get("indices", [])],
                            values=[float(item) for item in point.sparse.get("values", [])],
                        ),
                    },
                    payload=payload,
                )
            )
        if structs:
            await self._client.upsert(collection_name=name, wait=True, points=structs)

    async def count(self, name: str) -> int:
        self._validate_collection_name(name)
        result = await self._client.count(collection_name=name, exact=True)
        return int(result.count)

    async def query(
        self,
        name: str,
        *,
        dense: list[float],
        sparse: Mapping[str, Sequence[int] | Sequence[float]],
        filters: Mapping[str, object] | None = None,
        limit: int = 20,
        prefetch_limit: int | None = None,
    ) -> list[ProfessorQueryHit]:
        from qdrant_client import models

        self._validate_read_name(name)
        if limit <= 0:
            raise ValueValidationError("professor query limit must be positive")
        candidate_limit = prefetch_limit or max(limit, 50)
        if candidate_limit < limit:
            raise ValueValidationError("prefetch_limit cannot be smaller than limit")
        sparse_vector = models.SparseVector(
            indices=[int(item) for item in sparse.get("indices", [])],
            values=[float(item) for item in sparse.get("values", [])],
        )
        response = await self._client.query_points(
            collection_name=name,
            prefetch=[
                models.Prefetch(query=dense, using="dense", limit=candidate_limit),
                models.Prefetch(
                    query=sparse_vector, using="sparse", limit=candidate_limit
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=self._query_filter(filters),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            ProfessorQueryHit(
                entity_id=str(point.id),
                score=float(point.score),
                payload=dict(point.payload or {}),
            )
            for point in response.points
        ]

    async def iter_payloads(
        self, name: str, *, batch_size: int = 256
    ) -> AsyncIterator[dict[str, Any]]:
        self._validate_read_name(name)
        if batch_size <= 0:
            raise ValueValidationError("Qdrant payload batch size must be positive")
        offset: Any | None = None
        while True:
            points, offset = await self._client.scroll(
                collection_name=name,
                limit=batch_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                yield {"entity_id": str(point.id), "payload": dict(point.payload or {})}
            if offset is None:
                return

    async def resolve_current_alias(self) -> str | None:
        aliases = await self._client.get_aliases()
        matches = [
            str(alias.collection_name)
            for alias in aliases.aliases
            if str(alias.alias_name) == CURRENT_PROFESSOR_ALIAS
        ]
        if len(matches) > 1:
            raise ValueValidationError("Qdrant current professor alias is ambiguous")
        return matches[0] if matches else None

    async def switch_current_alias(self, name: str) -> None:
        from qdrant_client import models

        self._validate_collection_name(name)
        if not await self._client.collection_exists(name):
            raise ValueValidationError(f"Qdrant professor collection does not exist: {name}")
        current = await self.resolve_current_alias()
        if current == name:
            return
        operations: list[Any] = []
        if current is not None:
            operations.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=CURRENT_PROFESSOR_ALIAS)
                )
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=name, alias_name=CURRENT_PROFESSOR_ALIAS
                )
            )
        )
        await self._client.update_collection_aliases(
            change_aliases_operations=operations
        )
        if await self.resolve_current_alias() != name:
            raise ValueValidationError("Qdrant professor alias readback mismatch")


__all__ = [
    "CURRENT_PROFESSOR_ALIAS",
    "ProfessorQdrant",
    "ProfessorQueryHit",
    "ProfessorVectorPoint",
    "professor_collection_name",
]
