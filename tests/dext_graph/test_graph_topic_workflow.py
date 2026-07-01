import json
import sqlite3

import pytest

from dext_graph.catalog.db import CatalogWriter, initialize_catalog
from dext_graph.catalog.topic_workflow import run_topic_stage
from dext_graph.catalog.topics import import_taxonomy, load_taxonomy
from dext_graph.config import GraphSettings


class CharacterTokenizer:
    identity = "character-v1"

    def encode(self, text, *, add_special_tokens=False):
        values = [ord(char) for char in text]
        return ([1] + values + [2]) if add_special_tokens else values

    def decode(self, token_ids):
        return "".join(chr(value) for value in token_ids if value > 2)


class FakeEmbedding:
    def __init__(self):
        self.calls = []

    async def embed(self, texts, *, purpose):
        self.calls.append((purpose, list(texts)))
        return type(
            "Result",
            (),
            {"vectors": [[1.0, 0.0, 0.0] for _ in texts], "usage": {}},
        )()


class FakeTopicSink:
    def __init__(self):
        self.created = []
        self.points = {}

    async def create_collection(self, name, *, dimension):
        self.created.append((name, dimension))

    async def upsert(self, _name, points):
        self.points.update({point.topic_id: point for point in points})

    async def count(self, _name):
        return len(self.points)


class UnusedLLM:
    async def extract(self, _text):
        raise AssertionError("no statement should invoke the LLM")


@pytest.mark.asyncio
async def test_topic_stage_builds_active_collection_and_frozen_graph_manifest(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO graph_builds(
              id,status,curation_version,graph_schema_version,vector_schema_version,
              settings_json,summary_json
            ) VALUES ('build-1','WRITING_VECTOR','v1',1,1,'{}','{}')
            """
        )
        manifest = load_taxonomy("taxonomy/research-topics.yaml")
        import_taxonomy(connection, manifest)
        connection.execute(
            """
            INSERT INTO topics(
              taxonomy_version,id,canonical_name,normalized_name,kind,status,created_method
            ) VALUES (?, '10000000-0000-5000-8000-000000000001',
              '待审方法','待审方法','method','provisional','test')
            """,
            (manifest.version,),
        )
    settings = GraphSettings(
        catalog_path=path,
        taxonomy_path="taxonomy/research-topics.yaml",
        embedding_dimension=3,
        embedding_request_batch=16,
    )
    embedding = FakeEmbedding()
    sink = FakeTopicSink()

    async def fake_neo4j(_writer, _build_id, _settings):
        return {}

    async with CatalogWriter(path, max_queue=2) as writer:
        result = await run_topic_stage(
            writer,
            "build-1",
            settings,
            embedding_client=embedding,
            topic_sink=sink,
            llm_client=UnusedLLM(),
            tokenizer=CharacterTokenizer(),
            neo4j_writer=fake_neo4j,
        )

    assert result["build"]["status"] == "WRITING_VECTOR"
    assert result["build"]["taxonomy_version"] == "research-topics-v1"
    assert result["topics"]["status"] == "COMPLETED"
    assert len(sink.points) == 100
    assert all(point.payload["status"] == "active" for point in sink.points.values())
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT row_count FROM graph_export_partitions "
            "WHERE build_id='build-1' AND partition_key='node:Topic'"
        ).fetchone()[0] == 100
        assert connection.execute(
            "SELECT row_count FROM graph_export_partitions "
            "WHERE build_id='build-1' AND partition_key='rel:SUBTOPIC_OF'"
        ).fetchone()[0] > 20
        relation_payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM graph_export_rows "
                "WHERE build_id='build-1' AND partition_key='rel:SUBTOPIC_OF' LIMIT 1"
            ).fetchone()[0]
        )
        assert relation_payload["method"] == "taxonomy_yaml"
        assert relation_payload["provenance_ref"].startswith("taxonomy:")
