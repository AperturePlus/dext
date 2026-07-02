from __future__ import annotations

import pytest

from dext_recommend import (
    RecommendSettings, RecommendationFilters, RecommendationRuntimeError,
)
from dext_recommend.adapters.qdrant_search import LiveVectorSearchAdapter
from tests.dext_recommend._recfixtures import snapshot


class Point:
    def __init__(self, point_id, score, payload):
        self.id = point_id
        self.score = score
        self.payload = payload


class FakeQdrant:
    def __init__(self, points=()):
        self.points = list(points)
        self.query_calls = []
        self.count_calls = []
        self.closed = False

    async def query_points(self, **kwargs):
        self.query_calls.append(kwargs)
        return type("Response", (), {"points": self.points})()

    async def count(self, **kwargs):
        self.count_calls.append(kwargs)
        return type("Count", (), {"count": 7})()

    def close(self):
        self.closed = True


async def test_hybrid_recall_uses_pinned_collection_rrf_and_safe_filter():
    snap = snapshot()
    client = FakeQdrant([
        Point("good", 0.9, {"build_id": snap.build_id}),
        Point("poison", 0.8, {"build_id": "other"}),
        Point("wrong-schema", 0.7, {
            "build_id": snap.build_id, "payload_schema_version": 999,
        }),
    ])
    adapter = LiveVectorSearchAdapter(client=client, settings=RecommendSettings())
    hits = await adapter.hybrid_recall(
        snap,
        [0.1, 0.2],
        RecommendationFilters(
            university_ids=("u1",), city_names=("北京",),
            org_unit_ids=("org-never-push",),
        ),
        20,
        "r1",
        rrf_k=60,
        sparse_vector={"indices": [1], "values": [0.5]},
    )
    assert [hit.entity_id for hit in hits] == ["good"]
    call = client.query_calls[0]
    assert call["collection_name"] == snap.qdrant_alias_target
    assert [prefetch.using for prefetch in call["prefetch"]] == ["dense", "sparse"]
    assert all(prefetch.limit == 60 for prefetch in call["prefetch"])
    assert call["query"].fusion.value == "rrf"
    keys = [condition.key for condition in call["query_filter"].must]
    assert "build_id" in keys
    assert "university_id" in keys
    assert "city" in keys
    assert "org_unit_ids" not in keys
    assert "role_status" not in keys
    assert "payload_schema_version" not in keys


async def test_hybrid_recall_requires_sparse_vector_and_physical_collection():
    adapter = LiveVectorSearchAdapter(client=FakeQdrant(), settings=RecommendSettings())
    with pytest.raises(RecommendationRuntimeError) as raised:
        await adapter.hybrid_recall(
            snapshot(), [0.1], RecommendationFilters(), 10, "r1", rrf_k=60,
        )
    assert raised.value.code == "vector_unavailable"
    empty_collection = snapshot()
    object.__setattr__(empty_collection, "qdrant_alias_target", "")
    with pytest.raises(RecommendationRuntimeError):
        await adapter.hybrid_recall(
            empty_collection, [0.1], RecommendationFilters(), 10, "r1",
            rrf_k=60, sparse_vector={"indices": [1], "values": [1.0]},
        )


async def test_count_readback_is_exact_and_pinned():
    client = FakeQdrant()
    adapter = LiveVectorSearchAdapter(client=client, settings=RecommendSettings())
    value = await adapter.count_readback(snapshot(), {"city": "北京"})
    assert value == 7
    call = client.count_calls[0]
    assert call["collection_name"] == "phys-1"
    assert call["exact"] is True
    assert {condition.key for condition in call["count_filter"].must} == {
        "build_id", "city",
    }


async def test_qdrant_failure_is_retryable_and_close_handles_sync_client():
    class Broken(FakeQdrant):
        async def query_points(self, **kwargs):
            raise RuntimeError("connection failed")

    client = Broken()
    adapter = LiveVectorSearchAdapter(client=client, settings=RecommendSettings())
    with pytest.raises(RecommendationRuntimeError) as raised:
        await adapter.hybrid_recall(
            snapshot(), [0.1], RecommendationFilters(), 10, "r1",
            rrf_k=60, sparse_vector={"indices": [1], "values": [1.0]},
        )
    assert raised.value.code == "vector_unavailable"
    assert raised.value.retryable is True
    await adapter.aclose()
    assert client.closed is True
