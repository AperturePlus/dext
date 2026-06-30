from types import SimpleNamespace
import os
import uuid

import pytest

from dext_graph.catalog.topic_sink import (
    TopicQdrant,
    TopicVectorPoint,
    topic_collection_name,
)
from dext_graph.models import ValueValidationError


class FakeTopicQdrant:
    def __init__(self):
        self.exists = False
        self.created = None
        self.indexes = []
        self.points = []
        self.query = None

    async def collection_exists(self, _name):
        return self.exists

    async def create_collection(self, **kwargs):
        self.created = kwargs
        self.exists = True

    async def create_payload_index(self, **kwargs):
        self.indexes.append(kwargs)

    async def upsert(self, **kwargs):
        self.points.extend(kwargs["points"])

    async def query_points(self, **kwargs):
        self.query = kwargs
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="00000000-0000-5000-8000-000000000027",
                    score=0.97,
                    payload={"canonical_name": "机器学习", "kind": "method"},
                )
            ]
        )

    async def count(self, **_kwargs):
        return SimpleNamespace(count=len(self.points))


@pytest.mark.asyncio
async def test_topic_collection_schema_active_payload_and_kind_filter():
    fake = FakeTopicQdrant()
    sink = TopicQdrant("http://unused", client=fake)
    name = topic_collection_name("research-topics-v1", "abc123")
    assert name == "dext_topics__research-topics-v1__abc123"
    await sink.create_collection(name, dimension=3)
    assert fake.created["vectors_config"]["dense"].on_disk is True
    assert {item["field_name"] for item in fake.indexes} >= {
        "taxonomy_version",
        "canonical_name",
        "kind",
        "status",
        "alias_keys",
    }
    await sink.upsert(
        name,
        [
            TopicVectorPoint(
                topic_id="00000000-0000-5000-8000-000000000027",
                dense=[1.0, 0.0, 0.0],
                payload={
                    "taxonomy_version": "research-topics-v1",
                    "canonical_name": "机器学习",
                    "kind": "method",
                    "status": "active",
                    "alias_keys": ["机器学习", "machine learning"],
                    "embedding_fingerprint": "abc123",
                },
            )
        ],
    )
    result = await sink.query(
        name,
        [1.0, 0.0, 0.0],
        taxonomy_version="research-topics-v1",
        kind="method",
        limit=8,
    )
    assert result[0]["id"] == "00000000-0000-5000-8000-000000000027"
    conditions = fake.query["query_filter"].must
    matches = {condition.key: condition.match.value for condition in conditions}
    assert matches == {
        "taxonomy_version": "research-topics-v1",
        "kind": "method",
        "status": "active",
    }


def test_topic_collection_rejects_unsafe_names():
    with pytest.raises(ValueValidationError):
        topic_collection_name("bad version", "abc")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_qdrant_topic_kind_filter():
    url = os.getenv("DEXT_TEST_QDRANT_URL")
    if not url:
        pytest.skip("DEXT_TEST_QDRANT_URL is required for real Qdrant integration")
    from qdrant_client import AsyncQdrantClient

    client = AsyncQdrantClient(url=url)
    sink = TopicQdrant(url, client=client)
    suffix = uuid.uuid4().hex
    name = topic_collection_name(f"integration-{suffix}", suffix)
    try:
        await sink.create_collection(name, dimension=3)
        await sink.upsert(
            name,
            [
                TopicVectorPoint(
                    topic_id=str(uuid.uuid4()),
                    dense=[1.0, 0.0, 0.0],
                    payload={
                        "taxonomy_version": f"integration-{suffix}",
                        "canonical_name": "方法",
                        "kind": "method",
                        "status": "active",
                        "alias_keys": ["方法"],
                        "embedding_fingerprint": suffix,
                    },
                ),
                TopicVectorPoint(
                    topic_id=str(uuid.uuid4()),
                    dense=[1.0, 0.0, 0.0],
                    payload={
                        "taxonomy_version": f"integration-{suffix}",
                        "canonical_name": "任务",
                        "kind": "task",
                        "status": "active",
                        "alias_keys": ["任务"],
                        "embedding_fingerprint": suffix,
                    },
                ),
            ],
        )
        result = await sink.query(
            name,
            [1.0, 0.0, 0.0],
            taxonomy_version=f"integration-{suffix}",
            kind="method",
            limit=10,
        )
        assert len(result) == 1
        assert result[0]["kind"] == "method"
    finally:
        if await client.collection_exists(name):
            await client.delete_collection(name)
        await client.close()
