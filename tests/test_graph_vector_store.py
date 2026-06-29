from types import SimpleNamespace

import pytest

from dext_graph.artifacts import collection_name
from dext_graph.models import ProfileRecord, ValueValidationError
from dext_graph.vector_store import TemporaryQdrant


class FakeQdrant:
    def __init__(self):
        self.exists = False
        self.created = None
        self.points = []

    async def collection_exists(self, _name):
        return self.exists

    async def create_collection(self, **kwargs):
        self.created = kwargs
        self.exists = True

    async def upsert(self, **kwargs):
        self.points.extend(kwargs["points"])

    async def query_points(self, **_kwargs):
        return SimpleNamespace(points=[])


def _record():
    return ProfileRecord(
        source_id=1,
        source_row_key="hash:professors:1",
        point_id="5f763643-e0f2-5993-8944-00c1c29e30b7",
        name="不应进入 payload",
        university="测试大学",
        org_units=("测试学院",),
        title="教授",
        template_version="baseline-v1",
        normalized_profile="学校：测试大学",
        profile_hash="abc",
        token_count=5,
    )


async def test_temporary_qdrant_namespace_and_payload():
    fake = FakeQdrant()
    store = TemporaryQdrant("http://unused", client=fake)
    name = collection_name("experiment", "BAAI/bge-m3", "baseline-v1")
    await store.create(name, 3)
    await store.upsert(name, [_record()], [[1.0, 0.0, 0.0]])
    payload = fake.points[0].payload
    assert payload["source_row_key"] == "hash:professors:1"
    assert "name" not in payload
    with pytest.raises(ValueValidationError, match="non-temporary"):
        await store.create("dext_professors__production", 3)


async def test_temporary_qdrant_refuses_existing_collection():
    fake = FakeQdrant()
    fake.exists = True
    store = TemporaryQdrant("http://unused", client=fake)
    with pytest.raises(ValueValidationError, match="already exists"):
        await store.create("dext_eval__existing", 3)
