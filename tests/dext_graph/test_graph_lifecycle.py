import json
import sqlite3

import pytest

from dext_graph.catalog.db import CatalogError, CatalogWriter
from dext_graph.catalog.lifecycle import run_promotion, run_validation
from dext_graph.catalog.vector_workflow import run_vector_stage
from dext_graph.catalog.workflow import create_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


class CharacterTokenizer:
    identity = "character-v1"

    def encode(self, text, *, add_special_tokens=False):
        values = [ord(char) for char in text]
        return ([1] + values + [2]) if add_special_tokens else values

    def decode(self, token_ids):
        return "".join(chr(value) for value in token_ids if value > 2)


class FakeEmbeddingClient:
    async def embed(self, texts, *, purpose):
        return type(
            "EmbeddingResult",
            (),
            {
                "vectors": [[1.0, 0.0, 0.0] for _text in texts],
                "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
            },
        )()


class CapturingProfessorSink:
    def __init__(self):
        self.points = []

    async def create_collection(self, _name, *, dimension):
        assert dimension == 3

    async def upsert(self, _name, points):
        self.points.extend(points)

    async def count(self, _name):
        return len(self.points)

    async def iter_payloads(self, _name):
        for point in self.points:
            yield {"entity_id": point.entity_id, "payload": point.payload}


class PromotionQdrant:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.target = None

    async def switch_current_alias(self, name):
        if self.fail:
            raise RuntimeError("qdrant switch failed")
        self.target = name

    async def resolve_current_alias(self):
        return self.target


async def _prepare_validating_build(tmp_path, monkeypatch, *, with_gold=True):
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
    sink = CapturingProfessorSink()
    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        await run_vector_stage(
            writer,
            build_id,
            settings,
            embedding_client=FakeEmbeddingClient(),
            qdrant_sink=sink,
            tokenizer=CharacterTokenizer(),
        )

    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "INSERT INTO taxonomy_versions(id,status,manifest_hash,created_at) "
            "VALUES ('taxonomy-v1','published','taxonomy-hash','now')"
        )
        topic_summary = {
            "gold_status": "evaluated" if with_gold else "not_evaluated",
            "gold": {
                "topic_gate_passed": True,
                "relation_gate_passed": True,
                "subtopic_gate_passed": True,
                "dag_gate_passed": True,
            }
            if with_gold
            else {},
        }
        connection.execute(
            """
            INSERT INTO topic_runs(
              id,build_id,taxonomy_version,manifest_hash,status,summary_json,
              started_at,finished_at
            ) VALUES (?,?,?,'taxonomy-hash','COMPLETED',?,'now','now')
            """,
            (
                f"{build_id}:topic",
                build_id,
                "taxonomy-v1",
                json.dumps(topic_summary),
            ),
        )
        connection.execute(
            "UPDATE graph_builds SET taxonomy_version='taxonomy-v1' WHERE id=?",
            (build_id,),
        )
        for table, key, gold in (
            (
                "curation_runs",
                "gold",
                {
                    "status": "evaluated",
                    "auto_merge_pairwise_precision": 1.0,
                    "excluded_precision": 1.0,
                    "supervised_lecturer_retention": 1.0,
                },
            ),
            (
                "graph_runs",
                "graph_gold",
                {"status": "evaluated", "gate_passed": True},
            ),
        ):
            row = connection.execute(
                f"SELECT summary_json FROM {table} WHERE build_id=?", (build_id,)
            ).fetchone()
            summary = json.loads(row[0])
            if with_gold:
                summary[key] = gold
                summary[
                    "gold_status" if table == "curation_runs" else "graph_gold_status"
                ] = "evaluated"
            connection.execute(
                f"UPDATE {table} SET summary_json=? WHERE build_id=?",
                (json.dumps(summary), build_id),
            )
    return settings, build_id, sink


@pytest.mark.asyncio
async def test_validation_is_deterministic_and_missing_gold_blocks_ready(
    tmp_path, monkeypatch
):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=False
    )

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        failed = await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
        )
    assert failed["build"]["status"] == "FAILED_VALIDATION"
    failed_checks = {
        check["name"]
        for check in failed["validation"]["manifest_json"]["checks"]
        if not check["passed"]
    }
    assert {"curation_gold_gate", "graph_gold_gate", "topic_gold_gate"}.issubset(
        failed_checks
    )


@pytest.mark.asyncio
async def test_validation_can_explicitly_skip_missing_gold_gates(tmp_path, monkeypatch):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=False
    )

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        result = await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
            skip_gold_gates=True,
        )

    assert result["build"]["status"] == "READY"
    manifest = result["validation"]["manifest_json"]
    assert manifest["passed"] is True
    assert manifest["release_mode"] == "skip_gold_gates"
    assert set(manifest["skipped_checks"]) == {
        "curation_gold_gate",
        "graph_gold_gate",
        "topic_gold_gate",
    }
    skipped = {
        check["name"]: check
        for check in manifest["checks"]
        if check.get("skipped")
    }
    assert set(skipped) == set(manifest["skipped_checks"])


@pytest.mark.asyncio
async def test_validation_skip_gold_does_not_skip_qdrant_failures(
    tmp_path, monkeypatch
):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=False
    )
    sink.points[0].payload["org_unit_ids"] = []

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        result = await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
            skip_gold_gates=True,
        )

    assert result["build"]["status"] == "FAILED_VALIDATION"
    failed = {
        check["name"]
        for check in result["validation"]["manifest_json"]["checks"]
        if not check["passed"]
    }
    assert failed == {"qdrant_payload_reconciliation"}


@pytest.mark.asyncio
async def test_validation_passes_and_manifest_hash_is_stable(tmp_path, monkeypatch):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=True
    )

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        first = await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
        )
        second = await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
        )
    assert first["build"]["status"] == "READY"
    assert second["build"]["status"] == "READY"
    assert first["validation"]["manifest_hash"] == second["validation"]["manifest_hash"]
    assert all(
        check["passed"] for check in second["validation"]["manifest_json"]["checks"]
    )


@pytest.mark.asyncio
async def test_validation_rejects_qdrant_profile_or_org_drift(tmp_path, monkeypatch):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=True
    )
    sink.points[0].payload["org_unit_ids"] = []

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        result = await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
        )
    assert result["build"]["status"] == "FAILED_VALIDATION"
    failed = {
        check["name"]
        for check in result["validation"]["manifest_json"]["checks"]
        if not check["passed"]
    }
    assert "qdrant_payload_reconciliation" in failed


@pytest.mark.asyncio
async def test_promotion_failure_keeps_old_active_and_retry_converges(
    tmp_path, monkeypatch
):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=True
    )

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
        )

    old_build = "old-active"
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.execute(
            """
            INSERT INTO graph_builds(
              id,status,curation_version,graph_schema_version,vector_schema_version,
              settings_json,summary_json
            ) VALUES (?,'ACTIVE','v1',1,1,'{}','{}')
            """,
            (old_build,),
        )

    neo4j_state = {"active": old_build}

    async def set_neo4j(target, _settings):
        neo4j_state["active"] = target

    async def read_neo4j(_settings):
        return neo4j_state["active"]

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        with pytest.raises(RuntimeError, match="qdrant switch failed"):
            await run_promotion(
                writer,
                build_id,
                settings,
                qdrant_sink=PromotionQdrant(fail=True),
                neo4j_setter=set_neo4j,
                neo4j_reader=read_neo4j,
            )
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id=?", (old_build,)
        ).fetchone()[0] == "ACTIVE"
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()[0] == "READY"
        assert connection.execute(
            "SELECT status FROM promotion_runs WHERE build_id=?", (build_id,)
        ).fetchone()[0] == "FAILED"

    retry_qdrant = PromotionQdrant()
    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        promoted = await run_promotion(
            writer,
            build_id,
            settings,
            qdrant_sink=retry_qdrant,
            neo4j_setter=set_neo4j,
            neo4j_reader=read_neo4j,
        )
    assert promoted["build"]["status"] == "ACTIVE"
    assert promoted["promotion"]["status"] == "COMPLETED"
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id=?", (old_build,)
        ).fetchone()[0] == "READY"


@pytest.mark.parametrize("failure_step", ["neo4j", "readback"])
@pytest.mark.asyncio
async def test_promotion_other_failure_points_keep_catalog_active(
    tmp_path, monkeypatch, failure_step
):
    settings, build_id, sink = await _prepare_validating_build(
        tmp_path, monkeypatch, with_gold=True
    )

    async def neo4j_ok(_build_id, _settings):
        return None

    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        await run_validation(
            writer,
            build_id,
            settings,
            qdrant_sink=sink,
            neo4j_validator=neo4j_ok,
        )
    old_build = f"old-active-{failure_step}"
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.execute(
            "INSERT INTO graph_builds(id,status,curation_version,graph_schema_version,"
            "vector_schema_version,settings_json,summary_json) "
            "VALUES (?,'ACTIVE','v1',1,1,'{}','{}')",
            (old_build,),
        )

    async def setter(_target, _settings):
        if failure_step == "neo4j":
            raise RuntimeError("neo4j switch failed")

    async def reader(_settings):
        return old_build if failure_step == "readback" else build_id

    expected_exception = RuntimeError if failure_step == "neo4j" else CatalogError
    async with CatalogWriter(settings.catalog_path, max_queue=2) as writer:
        with pytest.raises(expected_exception):
            await run_promotion(
                writer,
                build_id,
                settings,
                qdrant_sink=PromotionQdrant(),
                neo4j_setter=setter,
                neo4j_reader=reader,
            )
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id=?", (old_build,)
        ).fetchone()[0] == "ACTIVE"
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()[0] == "READY"
