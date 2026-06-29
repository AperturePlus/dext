"""Qdrant adapter restricted to isolated temporary experiment collections."""

from __future__ import annotations

from typing import Any

from dext_graph.models import ProfileRecord, ValueValidationError


class TemporaryQdrant:
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

    async def __aenter__(self) -> "TemporaryQdrant":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    @staticmethod
    def _validate_temporary_name(name: str) -> None:
        if not name.startswith("dext_eval__") or name.startswith("dext_professors__"):
            raise ValueValidationError(f"refusing non-temporary Qdrant collection: {name}")

    async def create(self, name: str, dimension: int) -> None:
        from qdrant_client import models

        self._validate_temporary_name(name)
        if await self._client.collection_exists(name):
            raise ValueValidationError(f"temporary Qdrant collection already exists: {name}")
        await self._client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
        )

    async def upsert(
        self,
        name: str,
        records: list[ProfileRecord],
        vectors: list[list[float]],
    ) -> None:
        from qdrant_client import models

        self._validate_temporary_name(name)
        if len(records) != len(vectors):
            raise ValueError("profile/vector count mismatch")
        points = [
            models.PointStruct(
                id=record.point_id,
                vector=vector,
                payload={
                    "source_row_key": record.source_row_key,
                    "university": record.university,
                    "org_units": list(record.org_units),
                    "title": record.title,
                    "profile_hash": record.profile_hash,
                },
            )
            for record, vector in zip(records, vectors, strict=True)
        ]
        if points:
            await self._client.upsert(collection_name=name, wait=True, points=points)

    async def query(self, name: str, vector: list[float], *, limit: int = 20) -> list[dict[str, Any]]:
        self._validate_temporary_name(name)
        response = await self._client.query_points(
            collection_name=name,
            query=vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        points = getattr(response, "points", response)
        return [
            {
                "point_id": str(point.id),
                "score": float(point.score),
                "payload": dict(point.payload or {}),
            }
            for point in points
        ]

    async def count(self, name: str) -> int:
        self._validate_temporary_name(name)
        result = await self._client.count(collection_name=name, exact=True)
        return int(result.count)

    async def delete(self, name: str) -> None:
        self._validate_temporary_name(name)
        if await self._client.collection_exists(name):
            await self._client.delete_collection(collection_name=name)


__all__ = ["TemporaryQdrant"]
