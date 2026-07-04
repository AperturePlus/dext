import json
import sqlite3
from dataclasses import replace

import pytest

from dext_graph.catalog.db import initialize_catalog
from dext_graph.catalog.db import CatalogError
from dext_graph.catalog.topic_merge import persist_merge_suggestions
from dext_graph.catalog.topic_repair import repair_incompatible_topic_links
from dext_graph.catalog.topics import (
    apply_statement_concepts,
    complete_link_mutual_clusters,
    import_taxonomy,
    insert_topic_relation,
    load_taxonomy,
    materialize_taxonomy_relations,
    validate_concepts,
)
from dext_graph.config import GraphSettings
from dext_graph.catalog.vector_workflow import _entity_topics
from dext_graph.catalog.topic_workflow import _mark_job
from dext_graph.catalog.evidence import _relationship_rows


def _catalog(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
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
    connection.execute(
        """
        INSERT INTO source_snapshots(
          id,university_id,source_path,snapshot_path,file_hash,schema_version,
          row_counts_json,created_at
        ) VALUES (
          'snapshot-1','university-1','source.db','snapshot.db','snapshot-hash',
          1,'{}','now'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO professor_observations(
          id,university_id,source_snapshot_id,source_professor_id,source_url,
          source_page_kind,extraction_batch_size,source_content_hash,
          source_document_id,org_unit_source_id,name_raw,name_key,payload_json,
          row_hash,provenance_grade,first_seen_build,last_seen_build,active
        ) VALUES (
          'observation-1','university-1','snapshot-1',1,NULL,'unknown',NULL,
          'content-hash',NULL,NULL,'张三','zhang san','{}','row-hash',
          'legacy_merged','build-1','build-1',1
        )
        """
    )
    connection.execute(
        """
        INSERT INTO entities(id,kind,status,created_at,updated_at)
        VALUES ('entity-1','professor','active','now','now')
        """
    )
    connection.execute(
        """
        INSERT INTO research_statements(
          id,build_id,entity_id,observation_id,raw_text,normalized_text,
          language,statement_hash
        ) VALUES ('statement-1','build-1','entity-1','observation-1',
          '使用机器学习进行故障诊断','使用机器学习进行故障诊断','zh','hash-1')
        """
    )
    connection.commit()
    return connection, manifest


def test_seed_taxonomy_is_versioned_and_has_five_kinds():
    manifest = load_taxonomy("taxonomy/research-topics.yaml")
    assert manifest.version == "research-topics-v1"
    assert len(manifest.topics) == 100
    assert len(manifest.manifest_hash) == 64
    assert {topic.kind for topic in manifest.topics} == {
        "discipline",
        "method",
        "task",
        "application_domain",
        "research_object",
    }
    graph_neural_network = next(
        topic for topic in manifest.topics if topic.canonical_name == "图神经网络"
    )
    assert len(graph_neural_network.parents) == 2


def test_taxonomy_version_rejects_changed_manifest(tmp_path):
    connection, manifest = _catalog(tmp_path)
    try:
        with pytest.raises(CatalogError, match="publish a new version"):
            import_taxonomy(
                connection,
                replace(manifest, manifest_hash="0" * 64),
            )
    finally:
        connection.close()


def test_taxonomy_materializes_multi_parent_dag_and_rejects_cycles(tmp_path):
    connection, manifest = _catalog(tmp_path)
    try:
        count = materialize_taxonomy_relations(
            connection, build_id="build-1", manifest=manifest
        )
        assert count > 20
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_relations WHERE build_id='build-1' "
            "AND from_topic_id='00000000-0000-5000-8000-000000000029'"
        ).fetchone()[0] == 2
        assert insert_topic_relation(
            connection,
            build_id="build-1",
            taxonomy_version=manifest.version,
            child_id="00000000-0000-5000-8000-000000000001",
            parent_id="00000000-0000-5000-8000-000000000005",
        ) is False
        finding = connection.execute(
            "SELECT code,details_json FROM quality_findings "
            "WHERE build_id='build-1' AND code='taxonomy_cycle_rejected'"
        ).fetchone()
        assert finding["code"] == "taxonomy_cycle_rejected"
        assert json.loads(finding["details_json"])["reason"] == "directed_cycle"
        assert insert_topic_relation(
            connection,
            build_id="build-1",
            taxonomy_version=manifest.version,
            child_id="00000000-0000-5000-8000-000000000027",
            parent_id="00000000-0000-5000-8000-000000000010",
        ) is False
    finally:
        connection.close()


def test_statement_links_exact_only_and_keeps_new_topic_in_review(tmp_path):
    connection, _manifest = _catalog(tmp_path)
    try:
        links = apply_statement_concepts(
            connection,
            build_id="build-1",
            statement_id="statement-1",
            raw_text="使用机器学习进行故障诊断",
            concepts=[
                {
                    "evidence_span": "机器学习",
                    "canonical_name": "机器学习",
                    "kind": "method",
                    "relation_type": "USES_METHOD",
                },
                {
                    "evidence_span": "故障诊断",
                    "canonical_name": "设备异常识别",
                    "kind": "task",
                    "relation_type": "TARGETS_TASK",
                },
            ],
            semantic_choices={"故障诊断": "new_topic"},
        )
        assert links[0]["method"] == "alias_exact"
        assert links[0]["review_status"] == "approved"
        assert links[1]["method"] == "llm_new_topic"
        assert links[1]["review_status"] == "review"
        provisional = connection.execute(
            "SELECT status FROM topics WHERE canonical_name='设备异常识别'"
        ).fetchone()
        assert provisional[0] == "provisional"
        profile_topics, topic_ids = _entity_topics(connection, "build-1", "entity-1")
        assert profile_topics == ["方法：机器学习"]
        assert topic_ids["method"] == [
            "00000000-0000-5000-8000-000000000027"
        ]
        approved_exports = list(
            _relationship_rows(connection, "build-1", "rel:USES_METHOD")
        )
        review_exports = list(
            _relationship_rows(connection, "build-1", "rel:TARGETS_TASK")
        )
        assert len(approved_exports) == 1
        assert review_exports == []
    finally:
        connection.close()


def test_invalid_evidence_relation_and_multiple_primary_are_not_auto_approved(tmp_path):
    connection, _manifest = _catalog(tmp_path)
    try:
        assert validate_concepts(
            "机器学习",
            [
                {
                    "evidence_span": "不存在",
                    "canonical_name": "机器学习",
                    "kind": "method",
                    "relation_type": "USES_METHOD",
                },
                {
                    "evidence_span": "机器学习",
                    "canonical_name": "机器学习",
                    "kind": "discipline",
                    "relation_type": "USES_METHOD",
                },
            ],
        ) == []
        rows = apply_statement_concepts(
            connection,
            build_id="build-1",
            statement_id="statement-1",
            raw_text="机器学习和故障诊断",
            concepts=[
                {
                    "evidence_span": "机器学习",
                    "canonical_name": "机器学习",
                    "kind": "method",
                    "relation_type": "PRIMARY_TOPIC",
                },
                {
                    "evidence_span": "故障诊断",
                    "canonical_name": "故障诊断",
                    "kind": "task",
                    "relation_type": "PRIMARY_TOPIC",
                },
            ],
        )
        assert {row["review_status"] for row in rows} == {"review"}
    finally:
        connection.close()


def test_primary_topic_requires_discipline_topic_kind(tmp_path):
    connection, _manifest = _catalog(tmp_path)
    try:
        rows = apply_statement_concepts(
            connection,
            build_id="build-1",
            statement_id="statement-1",
            raw_text="机器学习",
            concepts=[
                {
                    "evidence_span": "机器学习",
                    "canonical_name": "机器学习",
                    "kind": "method",
                    "relation_type": "PRIMARY_TOPIC",
                },
            ],
        )

        assert rows == [
            {
                "topic_id": "00000000-0000-5000-8000-000000000027",
                "relation_type": "PRIMARY_TOPIC",
                "review_status": "review",
                "method": "alias_exact",
            }
        ]
        approved = connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links "
            "WHERE build_id='build-1' AND relation_type='PRIMARY_TOPIC' "
            "AND review_status='approved'"
        ).fetchone()[0]
        assert approved == 0
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_repair_incompatible_topic_links_resets_derived_stages(tmp_path):
    path = tmp_path / "catalog.db"
    connection, manifest = _catalog(tmp_path)
    try:
        connection.execute(
            """
            INSERT INTO statement_topic_links(
              build_id,statement_id,taxonomy_version,topic_id,relation_type,
              evidence_span,method,confidence,review_status,provenance_ref
            ) VALUES (
              'build-1','statement-1',?,
              '00000000-0000-5000-8000-000000000027','PRIMARY_TOPIC',
              '机器学习','alias_exact',1.0,'approved',
              'catalog:research-statement:build-1:statement-1'
            )
            """,
            (manifest.version,),
        )
        connection.execute(
            """
            INSERT INTO topic_runs(
              id,build_id,taxonomy_version,manifest_hash,status,summary_json,
              started_at,finished_at
            ) VALUES (
              'build-1:topics','build-1',?,?, 'COMPLETED',
              '{"approved_links":1,"review_links":0}','now','now'
            )
            """,
            (manifest.version, manifest.manifest_hash),
        )
        connection.execute(
            """
            INSERT INTO vector_runs(
              id,build_id,status,profile_template_version,tokenizer_identity,
              sparse_tokenizer_version,embedding_fingerprint,collection_name,
              summary_json,started_at,finished_at
            ) VALUES (
              'build-1:vector','build-1','COMPLETED','baseline-v1',
              'character-v1','bm25-simple-v1','fingerprint',
              'dext_professors__build-1','{}','now','now'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO sink_checkpoints(
              build_id,sink,partition_key,last_key,last_batch_id,rows_written,updated_at
            ) VALUES ('build-1','qdrant','professors','entity-1',NULL,1,'now')
            """
        )
        connection.execute(
            """
            INSERT INTO validation_runs(
              id,build_id,validation_version,status,manifest_json,manifest_hash
            ) VALUES ('build-1:validation','build-1','v','PASSED','{}','hash')
            """
        )
        connection.execute(
            """
            INSERT INTO promotion_runs(
              build_id,validation_manifest_hash,status,neo4j_done,qdrant_done,
              readback_done,started_at,updated_at
            ) VALUES ('build-1','hash','COMPLETED',1,1,1,'now','now')
            """
        )
        connection.execute(
            "UPDATE graph_builds SET status='FAILED_VALIDATION' WHERE id='build-1'"
        )
        connection.commit()
    finally:
        connection.close()

    result = await repair_incompatible_topic_links(
        "build-1", GraphSettings(catalog_path=path, build_neo4j_batch=1)
    )

    assert result["build"]["status"] == "WRITING_VECTOR"
    assert result["topic_link_repair"]["downgraded_links"] == 1
    assert result["topic_link_repair"]["rebuilt_partitions"] == ["rel:PRIMARY_TOPIC"]
    assert result["topic_link_repair"]["vector_reset"] is True
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT review_status FROM statement_topic_links WHERE build_id='build-1'"
        ).fetchone()[0] == "review"
        assert connection.execute(
            "SELECT status FROM vector_runs WHERE build_id='build-1'"
        ).fetchone()[0] == "PENDING"
        assert connection.execute(
            "SELECT COUNT(*) FROM validation_runs WHERE build_id='build-1'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM promotion_runs WHERE build_id='build-1'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sink_checkpoints WHERE build_id='build-1' "
            "AND sink='qdrant'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT row_count FROM graph_export_partitions "
            "WHERE build_id='build-1' AND partition_key='rel:PRIMARY_TOPIC'"
        ).fetchone()[0] == 0
        topic_summary = json.loads(
            connection.execute(
                "SELECT summary_json FROM topic_runs WHERE build_id='build-1'"
            ).fetchone()[0]
        )
        assert topic_summary["approved_links"] == 0
        assert topic_summary["review_links"] == 1


def test_complete_link_clustering_does_not_chain_similar_topics(tmp_path):
    neighbors = {
        "a": {"b": 0.96},
        "b": {"a": 0.95, "c": 0.96},
        "c": {"b": 0.95},
    }
    assert complete_link_mutual_clusters(neighbors, minimum_score=0.90) == [
        (("a", "b"), 0.95)
    ]

    connection, manifest = _catalog(tmp_path)
    try:
        before = connection.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
        written = persist_merge_suggestions(
            connection,
            taxonomy_version=manifest.version,
            neighbors_by_kind={"method": neighbors},
            minimum_score=0.90,
        )
        assert written == 1
        assert connection.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == before
        assert connection.execute(
            "SELECT status FROM topic_merge_suggestions"
        ).fetchone()[0] == "review"
    finally:
        connection.close()


def test_topic_link_job_retry_preserves_candidates_and_attempt_count(tmp_path):
    connection, _manifest = _catalog(tmp_path)
    try:
        _mark_job(
            connection,
            build_id="build-1",
            statement_id="statement-1",
            status="running",
        )
        _mark_job(
            connection,
            build_id="build-1",
            statement_id="statement-1",
            status="retry",
            candidates=[{"id": "candidate-1", "score": 0.8}],
            error="timeout",
        )
        _mark_job(
            connection,
            build_id="build-1",
            statement_id="statement-1",
            status="running",
        )
        row = connection.execute(
            "SELECT * FROM topic_link_jobs WHERE build_id='build-1'"
        ).fetchone()
        assert row["status"] == "running"
        assert row["attempt_count"] == 2
        assert json.loads(row["candidate_ids_json"])[0]["id"] == "candidate-1"
    finally:
        connection.close()
