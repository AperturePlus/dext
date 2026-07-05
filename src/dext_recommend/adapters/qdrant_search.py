"""Pinned physical-collection dense+sparse Qdrant search adapter."""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from dext_recommend.adapters._vector_reader import parse_build_id_from_collection
from dext_recommend.config import RecommendSettings
from dext_recommend.core.location import expand_city_names
from dext_recommend.errors import RecommendationRuntimeError
from dext_recommend.models import RecommendationFilters
from dext_recommend.ports.vector_search import AliasReadback, VectorHit
from dext_recommend.readiness import ActiveBuildSnapshot


def _qdrant_models():
    from qdrant_client import models
    return models


def _match_condition(key: str, value: object):
    models = _qdrant_models()
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        if not values:
            return None
        return models.FieldCondition(key=key, match=models.MatchAny(any=values))
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _filter_from_pairs(pairs: list[tuple[str, object]]):
    models = _qdrant_models()
    conditions = []
    for key, value in pairs:
        condition = _match_condition(key, value)
        if condition is not None:
            conditions.append(condition)
    return models.Filter(must=conditions)


def _build_filter(
    snapshot: ActiveBuildSnapshot, filters: RecommendationFilters,
):
    """Translate only stable, authority-safe payload predicates."""
    pairs: list[tuple[str, object]] = [("build_id", snapshot.build_id)]
    if filters.university_ids:
        pairs.append(("university_id", filters.university_ids))
    if filters.city_names:
        # The current publisher writes ``city``. Final filtering remains in core.
        pairs.append(("city", expand_city_names(filters.city_names)))
    if filters.title_families:
        pairs.append(("title_family", filters.title_families))
    if filters.master_eligibility == "confirmed":
        pairs.append(("master_eligibility", "confirmed"))
    if filters.phd_eligibility == "confirmed":
        pairs.append(("phd_eligibility", "confirmed"))
    if filters.topic_filter_mode == "hard" and filters.topic_ids:
        pairs.append(("topic_ids", filters.topic_ids))
    # org_unit_ids, role_status and review policy deliberately stay in core.
    return _filter_from_pairs(pairs)


class LiveVectorSearchAdapter:
    def __init__(self, *, client: Any, settings: RecommendSettings) -> None:
        self._client = client
        self._settings = settings
        self._closed = False

    def _build_filter(
        self, snapshot: ActiveBuildSnapshot, filters: RecommendationFilters,
    ):
        return _build_filter(snapshot, filters)

    async def hybrid_recall(
        self,
        snapshot: ActiveBuildSnapshot,
        query_vector: list[float],
        filters: RecommendationFilters,
        oversample: int,
        profile_version: str,
        *,
        rrf_k: int,
        sparse_vector: Mapping | None = None,
    ) -> list[VectorHit]:
        del profile_version
        collection = snapshot.qdrant_alias_target
        if not collection:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="ACTIVE snapshot has no physical vector collection",
                retryable=True,
            )
        if sparse_vector is None:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="sparse query vector is unavailable",
                retryable=True,
            )
        if oversample <= 0 or oversample > self._settings.qdrant_query_limit_max:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="vector query limit is outside configured bounds",
            )
        try:
            indices = [int(item) for item in sparse_vector["indices"]]
            values = [float(item) for item in sparse_vector["values"]]
            if not indices or len(indices) != len(values):
                raise ValueError("invalid sparse query vector")
            models = _qdrant_models()
            prefetch_limit = max(oversample, rrf_k)
            if prefetch_limit > self._settings.qdrant_query_limit_max:
                raise ValueError("prefetch limit exceeds configured maximum")
            response = await asyncio.wait_for(
                self._client.query_points(
                    collection_name=collection,
                    prefetch=[
                        models.Prefetch(
                            query=[float(value) for value in query_vector],
                            using="dense",
                            limit=prefetch_limit,
                        ),
                        models.Prefetch(
                            query=models.SparseVector(indices=indices, values=values),
                            using="sparse",
                            limit=prefetch_limit,
                        ),
                    ],
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    query_filter=self._build_filter(snapshot, filters),
                    limit=oversample,
                    with_payload=True,
                    with_vectors=False,
                ),
                timeout=self._settings.qdrant_timeout,
            )
        except RecommendationRuntimeError:
            raise
        except Exception as exc:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="vector search request failed",
                retryable=True,
            ) from exc

        points = getattr(response, "points", response)
        hits: list[VectorHit] = []
        for point in points:
            payload = dict(getattr(point, "payload", None) or {})
            if payload.get("build_id") != snapshot.build_id:
                continue
            payload_schema_version = payload.get("payload_schema_version")
            if (
                payload_schema_version is not None
                and payload_schema_version != snapshot.qdrant_payload_schema_version
            ):
                continue
            hits.append(VectorHit(
                entity_id=str(point.id),
                score=float(point.score),
                payload=payload,
            ))
        return hits

    async def alias_readback(self, snapshot: ActiveBuildSnapshot) -> AliasReadback:
        del snapshot
        try:
            aliases = await asyncio.wait_for(
                self._client.get_aliases(), timeout=self._settings.qdrant_timeout,
            )
            matches = [
                str(alias.collection_name)
                for alias in aliases.aliases
                if str(getattr(alias, "alias_name", "")) == self._settings.qdrant_alias
            ]
            if len(matches) != 1:
                raise ValueError("vector alias missing or ambiguous")
            target = matches[0]
            return AliasReadback(
                alias=self._settings.qdrant_alias,
                target_collection=target,
                build_id=parse_build_id_from_collection(target),
                payload_schema_version=self._settings.qdrant_payload_schema_version,
            )
        except Exception as exc:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="vector alias readback failed",
                retryable=True,
            ) from exc

    async def count_readback(
        self, snapshot: ActiveBuildSnapshot, filter: dict | None = None,
    ) -> int:
        collection = snapshot.qdrant_alias_target
        if not collection:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="ACTIVE snapshot has no physical vector collection",
                retryable=True,
            )
        pairs: list[tuple[str, object]] = [("build_id", snapshot.build_id)]
        for key, value in (filter or {}).items():
            if key in {
                "university_id", "city", "city_name", "title_family",
                "master_eligibility", "phd_eligibility", "topic_ids",
            }:
                pairs.append((key, value))
        try:
            result = await asyncio.wait_for(
                self._client.count(
                    collection_name=collection,
                    count_filter=_filter_from_pairs(pairs),
                    exact=True,
                ),
                timeout=self._settings.qdrant_timeout,
            )
            return int(result.count)
        except Exception as exc:
            raise RecommendationRuntimeError(
                code="vector_unavailable",
                message="vector count readback failed",
                retryable=True,
            ) from exc

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["LiveVectorSearchAdapter"]
