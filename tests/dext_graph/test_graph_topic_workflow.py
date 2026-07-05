import asyncio
import json
import sqlite3

import pytest

from dext_graph.catalog import topic_workflow as topic_workflow_module
from dext_graph.catalog.db import CatalogWriter, initialize_catalog
from dext_graph.catalog.topic_workflow import (
    TopicLinkDeferredRetryError,
    _claim_statement_batch,
    _ensure_topic_link_jobs,
    _link_statements,
    _reuse_topic_links_from_active_build,
    run_topic_stage,
)
from dext_graph.catalog.topics import TopicConcept, import_taxonomy, load_taxonomy
from dext_graph.config import GraphSettings
from dext_graph.models import ValueValidationError


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


class EmptyCandidateSink(FakeTopicSink):
    def __init__(self):
        super().__init__()
        self.queries = []

    async def query(self, name, vector, *, taxonomy_version, kind, limit):
        self.queries.append(
            {
                "name": name,
                "vector": vector,
                "taxonomy_version": taxonomy_version,
                "kind": kind,
                "limit": limit,
            }
        )
        return []


class UnusedLLM:
    async def extract(self, _text):
        raise AssertionError("no statement should invoke the LLM")


class FailingPreflightLLM:
    def __init__(self):
        self.preflight_calls = 0

    async def preflight(self):
        self.preflight_calls += 1
        raise ValueValidationError("Topic LLM preflight failed: Invalid token")

    async def extract_with_diagnostics(self, _raw_text):
        raise AssertionError("statement linking should not invoke the LLM")

    async def select(self, _concept, _candidates):
        raise AssertionError("statement linking should not invoke selection")


class PreflightForbiddenLLM(UnusedLLM):
    async def preflight(self):
        raise AssertionError("completed statement links should not preflight the LLM")


class ConcurrentLLM:
    def __init__(self, *, fail_on=None, delays=None):
        self.fail_on = set(fail_on or ())
        self.delays = dict(delays or {})
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def extract_with_diagnostics(self, raw_text):
        statement_id = raw_text.split(" ", 1)[0]
        self.calls.append(statement_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delays.get(statement_id, 0.01))
            if statement_id in self.fail_on:
                raise RuntimeError(f"boom {statement_id}")
            return [
                TopicConcept(
                    evidence_span="机器学习",
                    canonical_name="机器学习",
                    kind="method",
                    relation_type="USES_METHOD",
                )
            ], 0
        finally:
            self.active -= 1

    async def select(self, _concept, _candidates):
        raise AssertionError("exact alias concepts should not invoke selection")


class InvalidOutputLLM(ConcurrentLLM):
    def __init__(self, *, invalid_on=None, delays=None):
        super().__init__(delays=delays)
        self.invalid_on = set(invalid_on or ())

    async def extract_with_diagnostics(self, raw_text):
        statement_id = raw_text.split(" ", 1)[0]
        self.calls.append(statement_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delays.get(statement_id, 0.01))
            if statement_id in self.invalid_on:
                raise ValueValidationError(
                    "Topic extractor response lacks concepts array"
                )
            return [
                TopicConcept(
                    evidence_span="机器学习",
                    canonical_name="机器学习",
                    kind="method",
                    relation_type="USES_METHOD",
                )
            ], 0
        finally:
            self.active -= 1


class UnmatchedConceptLLM:
    def __init__(self):
        self.select_calls = 0

    async def extract_with_diagnostics(self, raw_text):
        return [
            TopicConcept(
                evidence_span="不存在方法",
                canonical_name="不存在方法",
                kind="method",
                relation_type="USES_METHOD",
            )
        ], 0

    async def select(self, _concept, _candidates):
        self.select_calls += 1
        raise AssertionError("empty candidate lists should not invoke selection")


def _prepare_link_catalog(tmp_path, statements):
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
            "UPDATE graph_builds SET taxonomy_version=? WHERE id='build-1'",
            (manifest.version,),
        )
        for index, (statement_id, raw_text) in enumerate(statements, start=1):
            connection.execute(
                """
                INSERT INTO research_statements(
                  id,build_id,entity_id,observation_id,raw_text,normalized_text,
                  language,statement_hash
                ) VALUES (?,'build-1',?,?,?,?,'zh',?)
                """,
                (
                    statement_id,
                    f"entity-{index}",
                    f"observation-{index}",
                    raw_text,
                    raw_text,
                    f"hash-{index}",
                ),
            )
        connection.commit()
    return path, manifest


def _topic_settings(path, *, concurrency):
    return GraphSettings(
        catalog_path=path,
        taxonomy_path="taxonomy/research-topics.yaml",
        embedding_dimension=3,
        embedding_request_batch=16,
        topic_link_concurrency=concurrency,
    )


async def _run_linking(path, manifest, settings, llm):
    async with CatalogWriter(path, max_queue=2) as writer:
        return await _link_statements(
            writer,
            build_id="build-1",
            taxonomy_version=manifest.version,
            collection_name="topic-candidates",
            tokenizer=CharacterTokenizer(),
            settings=settings,
            embedding_client=FakeEmbedding(),
            topic_sink=FakeTopicSink(),
            llm_client=llm,
        )


async def _run_linking_with_sink(path, manifest, settings, llm, sink):
    async with CatalogWriter(path, max_queue=2) as writer:
        return await _link_statements(
            writer,
            build_id="build-1",
            taxonomy_version=manifest.version,
            collection_name="topic-candidates",
            tokenizer=CharacterTokenizer(),
            settings=settings,
            embedding_client=FakeEmbedding(),
            topic_sink=sink,
            llm_client=llm,
        )


def test_topic_link_jobs_initialize_idempotently_without_overwriting_existing_jobs(
    tmp_path,
):
    path, _manifest = _prepare_link_catalog(
        tmp_path,
        [
            ("statement-01", "statement-01 使用机器学习"),
            ("statement-02", "statement-02 使用机器学习"),
            ("statement-03", "statement-03 使用机器学习"),
            ("statement-04", "statement-04 使用机器学习"),
        ],
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO topic_link_jobs(
              build_id,statement_id,status,candidate_ids_json,attempt_count,last_error,updated_at
            ) VALUES ('build-1',?,?, ?, ?, ?, '2026-01-01T00:00:00+00:00')
            """,
            [
                ("statement-01", "succeeded", '["kept"]', 7, None),
                ("statement-02", "terminal-invalid-input", "[]", 3, "empty"),
                ("statement-03", "retry", "[]", 2, "retry me"),
            ],
        )
        first = _ensure_topic_link_jobs(connection, "build-1")
        second = _ensure_topic_link_jobs(connection, "build-1")
        rows = connection.execute(
            """
            SELECT statement_id,status,candidate_ids_json,attempt_count,last_error
            FROM topic_link_jobs WHERE build_id='build-1' ORDER BY statement_id
            """
        ).fetchall()

    assert first == 1
    assert second == 0
    assert rows == [
        ("statement-01", "succeeded", '["kept"]', 7, None),
        ("statement-02", "terminal-invalid-input", "[]", 3, "empty"),
        ("statement-03", "retry", "[]", 2, "retry me"),
        ("statement-04", "pending", "[]", 0, None),
    ]


def test_reuse_topic_links_from_active_build_reuses_only_nonterminal_matching_jobs(
    tmp_path,
):
    path, manifest = _prepare_link_catalog(
        tmp_path,
        [
            ("statement-01", "statement-01 使用机器学习"),
            ("statement-02", "statement-02 使用机器学习"),
            ("statement-03", "statement-03 使用机器学习"),
            ("statement-04", "statement-04 使用机器学习"),
        ],
    )
    with sqlite3.connect(path) as connection:
        topic_id = connection.execute(
            "SELECT id FROM topics WHERE taxonomy_version=? AND kind='method' LIMIT 1",
            (manifest.version,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO graph_builds(
              id,status,curation_version,graph_schema_version,vector_schema_version,
              taxonomy_version,settings_json,summary_json,started_at,finished_at
            ) VALUES ('old-active','ACTIVE','v1',1,1,?,'{}','{}','2026-01-01','2026-01-02')
            """,
            (manifest.version,),
        )
        for statement_id in ("statement-01", "statement-02", "statement-03"):
            connection.execute(
                """
                INSERT INTO research_statements(
                  id,build_id,entity_id,observation_id,raw_text,normalized_text,
                  language,statement_hash
                ) VALUES (?,'old-active','entity-old','observation-old',?,?,'zh',?)
                """,
                (statement_id, statement_id, statement_id, f"old-{statement_id}"),
            )
        connection.executemany(
            """
            INSERT INTO topic_link_jobs(
              build_id,statement_id,status,candidate_ids_json,attempt_count,last_error,updated_at
            ) VALUES ('old-active',?,?, ?, ?, ?, '2026-01-02')
            """,
            [
                ("statement-01", "succeeded", '["old-candidate"]', 4, None),
                ("statement-02", "terminal-invalid-input", "[]", 2, "empty"),
                ("statement-03", "terminal-invalid-input", "[]", 9, "old"),
            ],
        )
        connection.execute(
            """
            INSERT INTO statement_topic_links(
              build_id,statement_id,taxonomy_version,topic_id,relation_type,
              evidence_span,method,confidence,review_status,provenance_ref
            ) VALUES (
              'old-active','statement-01',?,?,'USES_METHOD',
              '机器学习','topic_llm',0.9,'approved','old-ref'
            )
            """,
            (manifest.version, topic_id),
        )
        _ensure_topic_link_jobs(connection, "build-1")
        connection.execute(
            """
            UPDATE topic_link_jobs
            SET status='succeeded',candidate_ids_json='["current"]',attempt_count=1
            WHERE build_id='build-1' AND statement_id='statement-03'
            """
        )

        reuse = _reuse_topic_links_from_active_build(
            connection, "build-1", manifest.version
        )
        rows = connection.execute(
            """
            SELECT statement_id,status,candidate_ids_json,attempt_count,last_error
            FROM topic_link_jobs WHERE build_id='build-1' ORDER BY statement_id
            """
        ).fetchall()
        links = connection.execute(
            """
            SELECT statement_id,topic_id,relation_type,evidence_span,review_status
            FROM statement_topic_links WHERE build_id='build-1' ORDER BY statement_id
            """
        ).fetchall()
        checkpoint = connection.execute(
            """
            SELECT rows_written FROM sink_checkpoints
            WHERE build_id='build-1' AND sink='topic_link' AND partition_key='statements'
            """
        ).fetchone()

    assert reuse == {
        "previous_build_id": "old-active",
        "reused_links": 1,
        "reused_jobs": 2,
    }
    assert rows == [
        ("statement-01", "succeeded", '["old-candidate"]', 4, None),
        ("statement-02", "terminal-invalid-input", "[]", 2, "empty"),
        ("statement-03", "succeeded", '["current"]', 1, None),
        ("statement-04", "pending", "[]", 0, None),
    ]
    assert links == [
        ("statement-01", topic_id, "USES_METHOD", "机器学习", "approved")
    ]
    assert checkpoint[0] == 2


def test_reuse_topic_links_from_active_build_requires_matching_taxonomy(tmp_path):
    path, manifest = _prepare_link_catalog(
        tmp_path,
        [("statement-01", "statement-01 使用机器学习")],
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO graph_builds(
              id,status,curation_version,graph_schema_version,vector_schema_version,
              taxonomy_version,settings_json,summary_json,started_at,finished_at
            ) VALUES ('old-active','ACTIVE','v1',1,1,'other-taxonomy','{}','{}','2026-01-01','2026-01-02')
            """
        )
        connection.execute(
            """
            INSERT INTO topic_link_jobs(
              build_id,statement_id,status,candidate_ids_json,attempt_count,updated_at
            ) VALUES ('old-active','statement-01','succeeded','[]',1,'2026-01-02')
            """
        )
        _ensure_topic_link_jobs(connection, "build-1")

        reuse = _reuse_topic_links_from_active_build(
            connection, "build-1", manifest.version
        )
        row = connection.execute(
            "SELECT status FROM topic_link_jobs WHERE build_id='build-1'"
        ).fetchone()

    assert reuse == {
        "previous_build_id": None,
        "reused_links": 0,
        "reused_jobs": 0,
    }
    assert row[0] == "pending"


def test_topic_claim_batch_uses_status_index_and_reclaims_retry(tmp_path):
    path, _manifest = _prepare_link_catalog(
        tmp_path,
        [
            ("statement-01", "statement-01 使用机器学习"),
            ("statement-02", "statement-02 使用机器学习"),
            ("statement-03", "statement-03 使用机器学习"),
        ],
    )
    with sqlite3.connect(path) as connection:
        _ensure_topic_link_jobs(connection, "build-1")
        connection.execute(
            """
            UPDATE topic_link_jobs
            SET status='succeeded',attempt_count=1
            WHERE build_id='build-1' AND statement_id='statement-01'
            """
        )
        connection.execute(
            """
            UPDATE topic_link_jobs
            SET status='retry',attempt_count=2,last_error='try again'
            WHERE build_id='build-1' AND statement_id='statement-02'
            """
        )
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT j.statement_id AS id,s.raw_text
            FROM topic_link_jobs j
            JOIN research_statements s
              ON s.build_id=j.build_id AND s.id=j.statement_id
            WHERE j.build_id=? AND j.status=?
            ORDER BY j.statement_id LIMIT ?
            """,
            ("build-1", "pending", 64),
        ).fetchall()
        claimed = _claim_statement_batch(connection, "build-1", limit=2)
        rows = connection.execute(
            """
            SELECT statement_id,status,attempt_count,last_error
            FROM topic_link_jobs WHERE build_id='build-1' ORDER BY statement_id
            """
        ).fetchall()

    details = " ".join(str(row[3]) for row in plan)
    assert "ix_topic_link_jobs_status" in details
    assert "LEFT-JOIN" not in details
    assert [statement["id"] for statement in claimed] == [
        "statement-02",
        "statement-03",
    ]
    assert rows == [
        ("statement-01", "succeeded", 1, None),
        ("statement-02", "running", 3, None),
        ("statement-03", "running", 1, None),
    ]


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
    events = []

    async def fake_neo4j(_writer, _build_id, _settings):
        return {}

    async with CatalogWriter(path, max_queue=2) as writer:
        result = await run_topic_stage(
            writer,
            "build-1",
            settings,
            progress=events.append,
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
    assert any(event.stage == "topics" and event.action == "progress" for event in events)
    assert any(event.stage == "graph_export" and event.action == "completed" for event in events)
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


@pytest.mark.asyncio
async def test_topic_stage_preflight_fails_before_embedding_or_qdrant(tmp_path):
    path, _manifest = _prepare_link_catalog(
        tmp_path, [("statement-01", "statement-01 使用机器学习")]
    )
    settings = _topic_settings(path, concurrency=1)
    embedding = FakeEmbedding()
    sink = FakeTopicSink()
    llm = FailingPreflightLLM()

    async def fake_neo4j(_writer, _build_id, _settings):
        raise AssertionError("preflight failure should not reach Neo4j")

    async with CatalogWriter(path, max_queue=2) as writer:
        result = await run_topic_stage(
            writer,
            "build-1",
            settings,
            embedding_client=embedding,
            topic_sink=sink,
            llm_client=llm,
            tokenizer=CharacterTokenizer(),
            neo4j_writer=fake_neo4j,
        )

    assert result["build"]["status"] == "FAILED"
    assert result["topics"]["status"] == "FAILED"
    assert "Topic LLM preflight failed" in result["build"]["last_error"]
    assert llm.preflight_calls == 1
    assert embedding.calls == []
    assert sink.created == []
    assert sink.points == {}
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status,last_error FROM topic_runs WHERE build_id='build-1'"
        ).fetchone()
        assert row[0] == "FAILED"
        assert "Topic LLM preflight failed" in row[1]
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_candidate_collections"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_link_jobs WHERE build_id='build-1'"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_topic_stage_skips_preflight_when_statement_links_are_terminal(tmp_path):
    path, _manifest = _prepare_link_catalog(
        tmp_path,
        [
            ("statement-01", "statement-01 使用机器学习"),
            ("statement-02", "statement-02 使用机器学习"),
        ],
    )
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO topic_link_jobs(
              build_id,statement_id,status,candidate_ids_json,attempt_count,updated_at
            ) VALUES ('build-1',?,?, '[]', 1, '2026-01-01T00:00:00+00:00')
            """,
            [
                ("statement-01", "succeeded"),
                ("statement-02", "terminal-invalid-input"),
            ],
        )
        connection.commit()
    settings = _topic_settings(path, concurrency=1)

    async def fake_neo4j(_writer, _build_id, _settings):
        return {}

    async with CatalogWriter(path, max_queue=2) as writer:
        result = await run_topic_stage(
            writer,
            "build-1",
            settings,
            embedding_client=FakeEmbedding(),
            topic_sink=FakeTopicSink(),
            llm_client=PreflightForbiddenLLM(),
            tokenizer=CharacterTokenizer(),
            neo4j_writer=fake_neo4j,
        )

    assert result["build"]["status"] == "WRITING_VECTOR"
    assert result["topics"]["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_topic_statement_linking_runs_external_calls_concurrently(tmp_path):
    statements = [
        (f"statement-{index:02d}", f"statement-{index:02d} 使用机器学习")
        for index in range(1, 7)
    ]
    path, manifest = _prepare_link_catalog(tmp_path, statements)
    settings = _topic_settings(path, concurrency=4)
    delays = {"statement-01": 0.05}
    llm = ConcurrentLLM(delays=delays)

    completed = await _run_linking(path, manifest, settings, llm)

    assert completed == 6
    assert 1 < llm.max_active <= 4
    with sqlite3.connect(path) as connection:
        statuses = connection.execute(
            "SELECT status FROM topic_link_jobs WHERE build_id='build-1' ORDER BY statement_id"
        ).fetchall()
        assert [row[0] for row in statuses] == ["succeeded"] * 6
        assert connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links WHERE build_id='build-1'"
        ).fetchone()[0] == 6
        checkpoint = connection.execute(
            "SELECT last_key,rows_written FROM sink_checkpoints "
            "WHERE build_id='build-1' AND sink='topic_link'"
        ).fetchone()
        assert checkpoint == ("statement-06", 6)


@pytest.mark.asyncio
async def test_topic_statement_linking_claims_large_work_in_batches(
    tmp_path, monkeypatch
):
    statements = [
        (f"statement-{index:03d}", f"statement-{index:03d} 使用机器学习")
        for index in range(1, 131)
    ]
    path, manifest = _prepare_link_catalog(tmp_path, statements)
    settings = _topic_settings(path, concurrency=128)
    claim_sizes = []
    original = topic_workflow_module._claim_statement_batch

    def tracking_claim_batch(*args, **kwargs):
        batch = original(*args, **kwargs)
        claim_sizes.append(len(batch))
        return batch

    monkeypatch.setattr(
        topic_workflow_module, "_claim_statement_batch", tracking_claim_batch
    )

    completed = await _run_linking(path, manifest, settings, ConcurrentLLM())

    assert completed == 130
    assert [size for size in claim_sizes if size] == [130]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_link_jobs "
            "WHERE build_id='build-1' AND status='succeeded'"
        ).fetchone()[0] == 130


@pytest.mark.asyncio
async def test_topic_statement_linking_skips_selector_when_no_candidates(tmp_path):
    path, manifest = _prepare_link_catalog(
        tmp_path, [("statement-01", "statement-01 使用不存在方法")]
    )
    settings = _topic_settings(path, concurrency=1)
    llm = UnmatchedConceptLLM()
    sink = EmptyCandidateSink()

    completed = await _run_linking_with_sink(path, manifest, settings, llm, sink)

    assert completed == 1
    assert len(sink.queries) == 1
    assert llm.select_calls == 0
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT method,review_status FROM statement_topic_links "
            "WHERE build_id='build-1' AND statement_id='statement-01'"
        ).fetchone()
        assert row == ("llm_new_topic", "review")


@pytest.mark.asyncio
async def test_topic_statement_linking_resets_running_and_skips_terminal_jobs(tmp_path):
    statements = [
        (f"statement-{index:02d}", f"statement-{index:02d} 使用机器学习")
        for index in range(1, 5)
    ]
    path, manifest = _prepare_link_catalog(tmp_path, statements)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO topic_link_jobs(
              build_id,statement_id,status,candidate_ids_json,attempt_count,updated_at
            ) VALUES ('build-1',?,?, '[]', ?, '2026-01-01T00:00:00+00:00')
            """,
            [
                ("statement-01", "running", 1),
                ("statement-02", "succeeded", 1),
                ("statement-03", "terminal-invalid-input", 1),
            ],
        )
        connection.commit()
    settings = _topic_settings(path, concurrency=2)
    llm = ConcurrentLLM()

    completed = await _run_linking(path, manifest, settings, llm)

    assert completed == 2
    assert sorted(llm.calls) == ["statement-01", "statement-04"]
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT statement_id,status,attempt_count FROM topic_link_jobs "
            "WHERE build_id='build-1' ORDER BY statement_id"
        ).fetchall()
        assert rows == [
            ("statement-01", "succeeded", 2),
            ("statement-02", "succeeded", 1),
            ("statement-03", "terminal-invalid-input", 1),
            ("statement-04", "succeeded", 1),
        ]


@pytest.mark.asyncio
async def test_topic_statement_linking_defers_llm_validation_failure_without_cancelling_peers(
    tmp_path,
):
    statements = [
        (f"statement-{index:02d}", f"statement-{index:02d} 使用机器学习")
        for index in range(1, 6)
    ]
    path, manifest = _prepare_link_catalog(tmp_path, statements)
    settings = _topic_settings(path, concurrency=3)
    llm = InvalidOutputLLM(invalid_on={"statement-02"})

    with pytest.raises(TopicLinkDeferredRetryError, match="deferred 1"):
        await _run_linking(path, manifest, settings, llm)

    assert llm.calls.count("statement-02") == 1
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT statement_id,status,last_error FROM topic_link_jobs "
            "WHERE build_id='build-1' ORDER BY statement_id"
        ).fetchall()
        assert rows == [
            ("statement-01", "succeeded", None),
            (
                "statement-02",
                "retry",
                "Topic extractor response lacks concepts array",
            ),
            ("statement-03", "succeeded", None),
            ("statement-04", "succeeded", None),
            ("statement-05", "succeeded", None),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_link_jobs "
            "WHERE build_id='build-1' AND last_error='cancelled'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links WHERE build_id='build-1'"
        ).fetchone()[0] == 4

    resume_llm = ConcurrentLLM()
    completed = await _run_linking(path, manifest, settings, resume_llm)

    assert completed == 1
    assert resume_llm.calls == ["statement-02"]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_link_jobs "
            "WHERE build_id='build-1' AND status='succeeded'"
        ).fetchone()[0] == 5
        checkpoint = connection.execute(
            "SELECT rows_written FROM sink_checkpoints "
            "WHERE build_id='build-1' AND sink='topic_link'"
        ).fetchone()
        assert checkpoint == (5,)


@pytest.mark.asyncio
async def test_topic_statement_linking_marks_failure_retry_and_resume_completes(tmp_path):
    statements = [
        (f"statement-{index:02d}", f"statement-{index:02d} 使用机器学习")
        for index in range(1, 4)
    ]
    path, manifest = _prepare_link_catalog(tmp_path, statements)
    settings = _topic_settings(path, concurrency=1)

    with pytest.raises(RuntimeError, match="boom statement-02"):
        await _run_linking(
            path,
            manifest,
            settings,
            ConcurrentLLM(fail_on={"statement-02"}),
        )

    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT statement_id,status,last_error FROM topic_link_jobs "
            "WHERE build_id='build-1' ORDER BY statement_id"
        ).fetchall()
        assert rows[0] == ("statement-01", "succeeded", None)
        assert rows[1][0:2] == ("statement-02", "retry")
        assert "boom statement-02" in rows[1][2]

    resume_llm = ConcurrentLLM()
    completed = await _run_linking(path, manifest, settings, resume_llm)

    assert completed == 2
    assert resume_llm.calls == ["statement-02", "statement-03"]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_link_jobs "
            "WHERE build_id='build-1' AND status='succeeded'"
        ).fetchone()[0] == 3
        checkpoint = connection.execute(
            "SELECT last_key,rows_written FROM sink_checkpoints "
            "WHERE build_id='build-1' AND sink='topic_link'"
        ).fetchone()
        assert checkpoint == ("statement-03", 3)
