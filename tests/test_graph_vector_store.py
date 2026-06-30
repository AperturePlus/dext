from types import SimpleNamespace

import pytest

from dext_graph.artifacts import collection_name
from dext_graph.catalog.vector_sink import (
    CURRENT_PROFESSOR_ALIAS,
    ProfessorQdrant,
    ProfessorVectorPoint,
    professor_collection_name,
)
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


class FakeProfessorQdrant:
    def __init__(self):
        self.exists = False
        self.created = None
        self.indexes = []
        self.points = []

    async def collection_exists(self, _name):
        return self.exists

    async def create_collection(self, **kwargs):
        self.created = kwargs
        self.exists = True

    async def create_payload_index(self, **kwargs):
        self.indexes.append(kwargs)

    async def upsert(self, **kwargs):
        self.points.extend(kwargs["points"])

    async def count(self, **_kwargs):
        return SimpleNamespace(count=len(self.points))


async def test_professor_qdrant_collection_schema_indexes_and_payload():
    fake = FakeProfessorQdrant()
    sink = ProfessorQdrant("http://unused", client=fake)
    name = professor_collection_name("build-uuid")
    assert name == "dext_professors__build-uuid"

    await sink.create_collection(name, dimension=3)
    assert fake.created["collection_name"] == name
    assert "dense" in fake.created["vectors_config"]
    assert "sparse" in fake.created["sparse_vectors_config"]
    dense = fake.created["vectors_config"]["dense"]
    assert dense.size == 3
    assert str(dense.distance).lower().endswith("cosine")
    assert dense.on_disk is True
    indexed_fields = {item["field_name"] for item in fake.indexes}
    assert {
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
    }.issubset(indexed_fields)

    point = ProfessorVectorPoint(
        entity_id="5f763643-e0f2-5993-8944-00c1c29e30b7",
        dense=[1.0, 0.0, 0.0],
        sparse={"indices": [2], "values": [0.5]},
        payload={
            "build_id": "build-uuid",
            "entity_id": "5f763643-e0f2-5993-8944-00c1c29e30b7",
            "university_id": "univ:test",
            "org_unit_ids": ["org-1"],
            "role_status": "review",
            "role_reason_codes": ["identity_conflict"],
            "master_eligibility": "unknown",
            "phd_eligibility": "unknown",
            "title_family": "professor",
            "city": "上海市",
            "topic_ids": [],
            "method_topic_ids": [],
            "application_domain_topic_ids": [],
            "task_topic_ids": [],
            "profile_hash": "profile-a",
            "embedding_provider": "siliconflow",
            "embedding_model": "BAAI/bge-m3",
            "embedding_fingerprint": "fingerprint-a",
            "provenance_ref": "catalog:entity:5f763643-e0f2-5993-8944-00c1c29e30b7",
            "name": "不应写入 payload",
        },
    )
    await sink.upsert(name, [point])
    uploaded = fake.points[0]
    assert uploaded.id == point.entity_id
    assert set(uploaded.vector) == {"dense", "sparse"}
    assert uploaded.payload["role_status"] == "review"
    assert "name" not in uploaded.payload


class FakeProfessorQueryClient:
    def __init__(self):
        self.query_kwargs = None
        self.alias_target = None
        self.scroll_calls = 0

    async def query_points(self, **kwargs):
        self.query_kwargs = kwargs
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="entity-1",
                    score=0.75,
                    payload={"entity_id": "entity-1", "role_status": "included"},
                )
            ]
        )

    async def scroll(self, **_kwargs):
        self.scroll_calls += 1
        if self.scroll_calls == 1:
            return (
                [SimpleNamespace(id="entity-1", payload={"profile_hash": "hash-1"})],
                "next",
            )
        return (
            [SimpleNamespace(id="entity-2", payload={"profile_hash": "hash-2"})],
            None,
        )

    async def get_aliases(self):
        aliases = []
        if self.alias_target is not None:
            aliases.append(
                SimpleNamespace(
                    alias_name=CURRENT_PROFESSOR_ALIAS,
                    collection_name=self.alias_target,
                )
            )
        return SimpleNamespace(aliases=aliases)

    async def collection_exists(self, _name):
        return True

    async def update_collection_aliases(self, *, change_aliases_operations):
        for operation in change_aliases_operations:
            if getattr(operation, "delete_alias", None) is not None:
                self.alias_target = None
            if getattr(operation, "create_alias", None) is not None:
                self.alias_target = operation.create_alias.collection_name
        return True


async def test_professor_qdrant_hybrid_query_filters_payloads_and_alias():
    fake = FakeProfessorQueryClient()
    sink = ProfessorQdrant("http://unused", client=fake)
    hits = await sink.query(
        CURRENT_PROFESSOR_ALIAS,
        dense=[1.0, 0.0],
        sparse={"indices": [7], "values": [0.8]},
        filters={"university_id": ["u1", "u2"], "role_status": "included"},
        limit=5,
        prefetch_limit=10,
    )
    assert hits[0].entity_id == "entity-1"
    assert hits[0].score == 0.75
    assert fake.query_kwargs["with_vectors"] is False
    assert len(fake.query_kwargs["prefetch"]) == 2
    assert len(fake.query_kwargs["query_filter"].must) == 2

    payloads = [
        item
        async for item in sink.iter_payloads(
            professor_collection_name("build-1"), batch_size=1
        )
    ]
    assert [item["entity_id"] for item in payloads] == ["entity-1", "entity-2"]

    await sink.switch_current_alias(professor_collection_name("build-1"))
    assert await sink.resolve_current_alias() == professor_collection_name("build-1")
    await sink.switch_current_alias(professor_collection_name("build-2"))
    assert await sink.resolve_current_alias() == professor_collection_name("build-2")

    with pytest.raises(ValueValidationError, match="unsupported professor filter"):
        await sink.query(
            CURRENT_PROFESSOR_ALIAS,
            dense=[1.0, 0.0],
            sparse={"indices": [], "values": []},
            filters={"email": "private@example.test"},
        )
