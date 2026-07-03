import json
import sqlite3

import pytest

from dext_graph.catalog.db import CatalogWriter
from dext_graph.catalog.vector_workflow import run_vector_stage
from dext_graph.config import GraphSettings
from test_catalog_workflow import _patch_runtime, _settings, _source_db
from dext_graph.catalog.workflow import create_build


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = []

    async def embed(self, texts, *, purpose):
        self.calls.append((purpose, list(texts)))
        return type(
            "EmbeddingResult",
            (),
            {
                "vectors": [
                    [float(index + 1), 0.0, 0.0]
                    for index, _text in enumerate(texts)
                ],
                "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
            },
        )()


class FakeProfessorSink:
    def __init__(self):
        self.created = []
        self.points = []

    async def create_collection(self, name, *, dimension):
        self.created.append((name, dimension))

    async def upsert(self, _name, points):
        self.points.extend(points)

    async def count(self, _name):
        return len(self.points)


class CharacterTokenizer:
    identity = "character-v1"

    def encode(self, text, *, add_special_tokens=False):
        values = [ord(char) for char in text]
        return ([1] + values + [2]) if add_special_tokens else values

    def decode(self, token_ids):
        return "".join(chr(value) for value in token_ids if value > 2)


@pytest.mark.asyncio
async def test_vector_stage_uploads_only_eligible_professors_and_preserves_review(
    tmp_path, monkeypatch
):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_SKIP_VECTOR", "1")
    settings = _settings(tmp_path, batch=1).model_copy(
        update={
            "embedding_dimension": 3,
            "embedding_request_batch": 2,
            "qdrant_upsert_batch": 2,
            "embedding_api_key": "test-key",
        }
    )
    _source_db(settings.source_data_dir / "test.db", count=3)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    with sqlite3.connect(settings.catalog_path) as connection:
        entities = [
            row[0]
            for row in connection.execute(
                "SELECT entity_id FROM canonical_professors WHERE build_id=? ORDER BY entity_id",
                (build_id,),
            )
        ]
        connection.execute(
            "UPDATE canonical_professors SET role_status='excluded' "
            "WHERE build_id=? AND entity_id=?",
            (build_id, entities[0]),
        )
        connection.execute(
            "UPDATE canonical_professors SET role_status='review', "
            "role_reason_codes=? "
            "WHERE build_id=? AND entity_id=?",
            ('["identity_conflict"]', build_id, entities[1]),
        )
    embedding = FakeEmbeddingClient()
    sink = FakeProfessorSink()
    events = []
    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        output = await run_vector_stage(
            writer,
            build_id,
            settings,
            progress=events.append,
            embedding_client=embedding,
            qdrant_sink=sink,
            tokenizer=CharacterTokenizer(),
        )

    assert output["build"]["status"] == "VALIDATING"
    assert sink.created == [(f"dext_professors__{build_id}", 3)]
    assert len(sink.points) == 2
    assert {point.entity_id for point in sink.points} == set(entities[1:])
    review_payload = next(
        point.payload for point in sink.points if point.entity_id == entities[1]
    )
    assert review_payload["role_status"] == "review"
    assert review_payload["role_reason_codes"] == ["identity_conflict"]
    assert all(point.payload["provenance_ref"].startswith("catalog:entity:") for point in sink.points)
    assert len(embedding.calls) == 1
    assert all(point.payload["org_unit_ids"] for point in sink.points)

    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM professor_profiles WHERE build_id=?", (build_id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM embedding_jobs WHERE build_id=? AND status='succeeded'",
            (build_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT last_key FROM sink_checkpoints "
            "WHERE build_id=? AND sink='qdrant' AND partition_key='professors'",
            (build_id,),
        ).fetchone()[0] == max(entities[1:])
        exported_profiles = {
            row[0]: json.loads(row[1])["profile_hash"]
            for row in connection.execute(
                "SELECT row_key,payload_json FROM graph_export_rows "
                "WHERE build_id=? AND partition_key='node:Professor'",
                (build_id,),
            )
        }
        affiliation_org_ids = {
            row[0].removeprefix(f"{build_id}:")
            for row in connection.execute(
                "SELECT end_graph_key FROM graph_export_rows "
                "WHERE build_id=? AND partition_key='rel:AFFILIATED_WITH'",
                (build_id,),
            )
        }
    assert exported_profiles[entities[0]] is None
    for point in sink.points:
        assert exported_profiles[point.entity_id] == point.payload["profile_hash"]
        assert set(point.payload["org_unit_ids"]) == affiliation_org_ids
    assert output["vector"]["status"] == "COMPLETED"
    assert output["vector"]["collection_name"] == f"dext_professors__{build_id}"
    assert any(event.stage == "vector" and event.action == "progress" for event in events)
    assert any(
        event.stage == "vector"
        and event.action == "completed"
        and event.current == 2
        and event.total == 2
        for event in events
    )


@pytest.mark.asyncio
async def test_vector_stage_resume_uses_embedding_cache_without_provider_call(
    tmp_path, monkeypatch
):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_SKIP_VECTOR", "1")
    settings = _settings(tmp_path, batch=1).model_copy(
        update={
            "embedding_dimension": 3,
            "embedding_request_batch": 2,
            "qdrant_upsert_batch": 2,
            "embedding_api_key": "test-key",
        }
    )
    _source_db(settings.source_data_dir / "test.db", count=2)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    first_embedding = FakeEmbeddingClient()
    first_sink = FakeProfessorSink()
    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        await run_vector_stage(
            writer,
            build_id,
            settings,
            embedding_client=first_embedding,
            qdrant_sink=first_sink,
            tokenizer=CharacterTokenizer(),
        )
    assert len(first_embedding.calls) == 1

    with sqlite3.connect(settings.catalog_path) as connection:
        connection.execute("UPDATE graph_builds SET status='WRITING_VECTOR' WHERE id=?", (build_id,))
        connection.execute("UPDATE vector_runs SET status='RUNNING' WHERE build_id=?", (build_id,))
        connection.execute(
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink='qdrant'",
            (build_id,),
        )

    second_embedding = FakeEmbeddingClient()
    second_sink = FakeProfessorSink()
    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        await run_vector_stage(
            writer,
            build_id,
            settings,
            embedding_client=second_embedding,
            qdrant_sink=second_sink,
            tokenizer=CharacterTokenizer(),
        )
    assert second_embedding.calls == []
    assert len(second_sink.points) == 2
