"""Offline, review-only Topic merge suggestions."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Mapping

from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    backup_existing_catalog,
    catalog_write_lock,
    initialize_catalog,
    json_dumps,
    utcnow_iso,
)
from dext_graph.catalog.ids import hash_parts
from dext_graph.catalog.topics import complete_link_mutual_clusters
from dext_graph.config import GraphSettings
from dext_graph.embeddings import EmbeddingClient

MERGE_METHOD = "mutual_knn_complete_link_v1"


def _formal_fingerprint(connection: sqlite3.Connection, build_id: str) -> str:
    digest = hashlib.sha256()
    queries = (
        ("topics", "SELECT * FROM topics ORDER BY taxonomy_version,id", ()),
        ("topic_aliases", "SELECT * FROM topic_aliases ORDER BY id", ()),
        (
            "statement_topic_links",
            "SELECT * FROM statement_topic_links WHERE build_id=? ORDER BY statement_id,topic_id",
            (build_id,),
        ),
        (
            "topic_relations",
            "SELECT * FROM topic_relations WHERE build_id=? ORDER BY from_topic_id,to_topic_id",
            (build_id,),
        ),
    )
    for label, query, parameters in queries:
        digest.update(label.encode("ascii"))
        for row in connection.execute(query, parameters):
            digest.update(json_dumps(dict(row)).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def persist_merge_suggestions(
    connection: sqlite3.Connection,
    *,
    taxonomy_version: str,
    neighbors_by_kind: Mapping[str, Mapping[str, Mapping[str, float]]],
    minimum_score: float,
) -> int:
    written = 0
    for kind, neighbors in sorted(neighbors_by_kind.items()):
        for topic_ids, score in complete_link_mutual_clusters(
            neighbors, minimum_score=minimum_score
        ):
            value = json_dumps(list(topic_ids))
            connection.execute(
                """
                INSERT INTO topic_merge_suggestions(
                  id,taxonomy_version,topic_ids_json,method,score,status,created_at
                ) VALUES (?,?,?,?,?,'review',?)
                ON CONFLICT(taxonomy_version,topic_ids_json,method) DO UPDATE SET
                  score=excluded.score,status='review'
                """,
                (
                    hash_parts(taxonomy_version, kind, MERGE_METHOD, *topic_ids),
                    taxonomy_version,
                    value,
                    MERGE_METHOD,
                    score,
                    utcnow_iso(),
                ),
            )
            written += 1
    return written


class _TemporarySuggestionQdrant:
    def __init__(self, url: str, *, client: Any | None = None) -> None:
        if client is None:
            from qdrant_client import AsyncQdrantClient

            client = AsyncQdrantClient(url=url)
            self._owns = True
        else:
            self._owns = False
        self.client = client

    async def close(self) -> None:
        if self._owns:
            await self.client.close()

    async def create(self, *, dimension: int) -> str:
        from qdrant_client import models

        name = f"dext_topic_suggestions__{uuid.uuid4().hex}"
        await self.client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": models.VectorParams(
                    size=dimension, distance=models.Distance.COSINE, on_disk=True
                )
            },
        )
        return name

    async def upsert(
        self,
        name: str,
        rows: list[dict[str, str]],
        vectors: list[list[float]],
    ) -> None:
        from qdrant_client import models

        await self.client.upsert(
            collection_name=name,
            wait=True,
            points=[
                models.PointStruct(
                    id=row["id"],
                    vector={"dense": vector},
                    payload={"kind": row["kind"]},
                )
                for row, vector in zip(rows, vectors, strict=True)
            ],
        )

    async def neighbors(
        self,
        name: str,
        rows: list[dict[str, str]],
        *,
        top_k: int,
    ) -> dict[str, dict[str, dict[str, float]]]:
        from qdrant_client import models

        result: dict[str, dict[str, dict[str, float]]] = {}
        for row in rows:
            response = await self.client.query_points(
                collection_name=name,
                query=row["id"],
                using="dense",
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="kind", match=models.MatchValue(value=row["kind"])
                        )
                    ]
                ),
                limit=top_k + 1,
                with_payload=False,
                with_vectors=False,
            )
            result.setdefault(row["kind"], {})[row["id"]] = {
                str(point.id): float(point.score)
                for point in response.points
                if str(point.id) != row["id"]
            }
        return result

    async def delete(self, name: str) -> None:
        await self.client.delete_collection(name)


async def suggest_topic_merges(
    build_id: str,
    settings: GraphSettings | None = None,
    *,
    embedding_client: Any | None = None,
    qdrant: Any | None = None,
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            build = await writer.execute(
                lambda connection: connection.execute(
                    "SELECT taxonomy_version FROM graph_builds WHERE id=?", (build_id,)
                ).fetchone(),
                transactional=False,
            )
            if build is None or build[0] is None:
                raise CatalogError("build has no taxonomy version")
            taxonomy_version = str(build[0])
            before = await writer.execute(
                lambda connection: _formal_fingerprint(connection, build_id),
                transactional=False,
            )
            rows = await writer.execute(
                lambda connection: [
                    dict(row)
                    for row in connection.execute(
                        "SELECT id,canonical_name,kind FROM topics "
                        "WHERE taxonomy_version=? AND status='provisional' ORDER BY kind,id",
                        (taxonomy_version,),
                    )
                ],
                transactional=False,
            )
            if len(rows) < 2:
                return {"taxonomy_version": taxonomy_version, "suggestions": 0}
            owns_embedding = embedding_client is None
            owns_qdrant = qdrant is None
            if embedding_client is None:
                embedding_client = EmbeddingClient(settings, lambda _metric: None)
            if qdrant is None:
                qdrant = _TemporarySuggestionQdrant(settings.qdrant_url)
            temporary_name: str | None = None
            try:
                temporary_name = await qdrant.create(
                    dimension=settings.embedding_dimension
                )
                for offset in range(0, len(rows), settings.embedding_request_batch):
                    batch = rows[offset : offset + settings.embedding_request_batch]
                    embedded = await embedding_client.embed(
                        [str(row["canonical_name"]) for row in batch],
                        purpose="topic_merge_suggestions",
                    )
                    await qdrant.upsert(temporary_name, batch, embedded.vectors)
                neighbors = await qdrant.neighbors(
                    temporary_name,
                    rows,
                    top_k=settings.topic_candidate_top_k,
                )
                written = await writer.execute(
                    lambda connection: persist_merge_suggestions(
                        connection,
                        taxonomy_version=taxonomy_version,
                        neighbors_by_kind=neighbors,
                        minimum_score=settings.topic_merge_min_score,
                    )
                )
            finally:
                if temporary_name is not None:
                    await qdrant.delete(temporary_name)
                if owns_embedding:
                    await embedding_client.close()
                if owns_qdrant:
                    await qdrant.close()
            after = await writer.execute(
                lambda connection: _formal_fingerprint(connection, build_id),
                transactional=False,
            )
            if before != after:
                raise CatalogError("merge suggestion workflow modified formal taxonomy facts")
            return {"taxonomy_version": taxonomy_version, "suggestions": written}


__all__ = ["MERGE_METHOD", "persist_merge_suggestions", "suggest_topic_merges"]
