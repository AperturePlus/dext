"""Qdrant adapter for versioned active Topic candidate collections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dext_graph.models import ValueValidationError

_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
_INDEX_FIELDS = ("taxonomy_version", "canonical_name", "kind", "status", "alias_keys")


@dataclass(frozen=True)
class TopicVectorPoint:
    topic_id: str
    dense: list[float]
    payload: dict[str, Any]


def topic_collection_name(taxonomy_version: str, embedding_fingerprint: str) -> str:
    if not taxonomy_version or not embedding_fingerprint:
        raise ValueValidationError("taxonomy version and embedding fingerprint are required")
    if not _SAFE.fullmatch(taxonomy_version) or not _SAFE.fullmatch(embedding_fingerprint):
        raise ValueValidationError("unsafe Topic collection component")
    return f"dext_topics__{taxonomy_version}__{embedding_fingerprint}"


class TopicQdrant:
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

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name.startswith("dext_topics__"):
            raise ValueValidationError(f"refusing non-Topic collection: {name}")

    async def create_collection(self, name: str, *, dimension: int) -> None:
        from qdrant_client import models

        self._validate_name(name)
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
        )
        for field in _INDEX_FIELDS:
            await self._client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    async def upsert(self, name: str, points: list[TopicVectorPoint]) -> None:
        from qdrant_client import models

        self._validate_name(name)
        structs = [
            models.PointStruct(
                id=point.topic_id,
                vector={"dense": point.dense},
                payload=dict(point.payload),
            )
            for point in points
        ]
        if structs:
            await self._client.upsert(collection_name=name, wait=True, points=structs)

    async def query(
        self,
        name: str,
        vector: list[float],
        *,
        taxonomy_version: str,
        kind: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        from qdrant_client import models

        self._validate_name(name)
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="taxonomy_version",
                    match=models.MatchValue(value=taxonomy_version),
                ),
                models.FieldCondition(key="kind", match=models.MatchValue(value=kind)),
                models.FieldCondition(key="status", match=models.MatchValue(value="active")),
            ]
        )
        response = await self._client.query_points(
            collection_name=name,
            query=vector,
            using="dense",
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        result: list[dict[str, Any]] = []
        for point in response.points:
            payload = dict(point.payload or {})
            result.append(
                {
                    "id": str(point.id),
                    "canonical_name": payload.get("canonical_name"),
                    "kind": payload.get("kind"),
                    "score": float(point.score),
                }
            )
        return result

    async def count(self, name: str) -> int:
        self._validate_name(name)
        response = await self._client.count(collection_name=name, exact=True)
        return int(response.count)


__all__ = ["TopicQdrant", "TopicVectorPoint", "topic_collection_name"]
