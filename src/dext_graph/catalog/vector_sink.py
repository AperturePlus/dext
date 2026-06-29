"""Qdrant sink for isolated stage-4 professor semantic collections."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ProfessorVectorPoint:
    entity_id: str
    dense: list[float]
    sparse: dict[str, list[int] | list[float]]
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
        if not name.startswith("dext_professors__") or name == "dext_professors_current":
            raise ValueValidationError(f"refusing non-professor Qdrant collection: {name}")

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


__all__ = [
    "ProfessorQdrant",
    "ProfessorVectorPoint",
    "professor_collection_name",
]
