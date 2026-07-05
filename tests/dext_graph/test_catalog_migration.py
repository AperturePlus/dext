import sqlite3
import json

import pytest

from dext_graph.catalog.db import initialize_catalog
from dext_graph.catalog.models import (
    CATALOG_SCHEMA_VERSION,
    CURATION_SCHEMA_SQL,
    EVIDENCE_GRAPH_SCHEMA_SQL,
    SEMANTIC_VECTOR_SCHEMA_SQL,
    SCHEMA_SQL,
    TOPIC_SCHEMA_SQL,
)
from dext_graph.catalog.workflow import create_build, resume_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


V6_RELEASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS validation_runs (
    id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL UNIQUE REFERENCES graph_builds(id),
    validation_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RUNNING','PASSED','FAILED')),
    manifest_json TEXT NOT NULL DEFAULT '{}',
    manifest_hash TEXT,
    started_at TEXT,
    finished_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS promotion_runs (
    build_id TEXT PRIMARY KEY REFERENCES graph_builds(id),
    validation_manifest_hash TEXT NOT NULL,
    previous_active_build_id TEXT REFERENCES graph_builds(id),
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    neo4j_done INTEGER NOT NULL DEFAULT 0 CHECK (neo4j_done IN (0,1)),
    qdrant_done INTEGER NOT NULL DEFAULT 0 CHECK (qdrant_done IN (0,1)),
    readback_done INTEGER NOT NULL DEFAULT 0 CHECK (readback_done IN (0,1)),
    started_at TEXT,
    updated_at TEXT,
    finished_at TEXT,
    last_error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_graph_builds_one_active
ON graph_builds(status) WHERE status='ACTIVE';

CREATE INDEX IF NOT EXISTS ix_validation_runs_status
ON validation_runs(status, build_id);

CREATE INDEX IF NOT EXISTS ix_promotion_runs_status
ON promotion_runs(status, build_id);
"""


def test_v1_catalog_migrates_in_place_to_v3(tmp_path):
    path = tmp_path / "catalog.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '1')")
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            """
            INSERT INTO graph_builds(
              id,status,source_manifest_hash,curation_version,taxonomy_version,
              graph_schema_version,vector_schema_version,embedding_provider,
              embedding_base_url,embedding_model,embedding_fingerprint,
              embedding_dimension,settings_json,summary_json,started_at,finished_at,last_error
            ) VALUES ('build-1','CURATING','hash','curation-v1',NULL,1,1,NULL,NULL,NULL,NULL,NULL,'{}','{}',NULL,NULL,NULL)
            """
        )
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        assert connection.execute(
            "SELECT value FROM catalog_meta WHERE key='schema_version'"
        ).fetchone()[0] == str(CATALOG_SCHEMA_VERSION)
        assert connection.execute("SELECT status FROM graph_builds WHERE id='build-1'").fetchone()[0] == "CURATING"
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='canonical_professors'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_export_rows'"
        ).fetchone() == (1,)


def test_v2_catalog_migrates_in_place_to_v3(tmp_path):
    path = tmp_path / "catalog-v2.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executescript(CURATION_SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '2')")
        connection.execute("PRAGMA user_version=2")
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_statements'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='embedding_jobs'"
        ).fetchone() == (1,)


def test_v3_catalog_migrates_in_place_to_v6(tmp_path):
    path = tmp_path / "catalog-v3.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executescript(CURATION_SCHEMA_SQL)
        connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '3')")
        connection.execute("PRAGMA user_version=3")
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_runs'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_runs'"
        ).fetchone() == (1,)


def test_v4_catalog_migrates_in_place_to_v6(tmp_path):
    path = tmp_path / "catalog-v4.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executescript(CURATION_SCHEMA_SQL)
        connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
        connection.executescript(SEMANTIC_VECTOR_SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '4')")
        connection.execute("PRAGMA user_version=4")
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_runs'"
        ).fetchone() == (1,)


def test_v5_catalog_migrates_in_place_to_v6(tmp_path):
    path = tmp_path / "catalog-v5.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executescript(CURATION_SCHEMA_SQL)
        connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
        connection.executescript(SEMANTIC_VECTOR_SCHEMA_SQL)
        connection.executescript(TOPIC_SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '5')")
        connection.execute("PRAGMA user_version=5")
        connection.execute(
            "INSERT INTO graph_builds(id,status,curation_version,graph_schema_version,"
            "vector_schema_version,settings_json,summary_json) "
            "VALUES ('ready-build','READY','v1',1,1,'{}','{}')"
        )
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id='ready-build'"
        ).fetchone()[0] == "READY"
        for table in ("validation_runs", "promotion_runs"):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() == (1,)


def test_v6_catalog_migrates_promotion_runs_to_rolled_back_status(tmp_path):
    path = tmp_path / "catalog-v6.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executescript(CURATION_SCHEMA_SQL)
        connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
        connection.executescript(SEMANTIC_VECTOR_SCHEMA_SQL)
        connection.executescript(TOPIC_SCHEMA_SQL)
        connection.executescript(V6_RELEASE_SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '6')")
        connection.execute("PRAGMA user_version=6")
        connection.execute(
            "INSERT INTO graph_builds(id,status,curation_version,graph_schema_version,"
            "vector_schema_version,settings_json,summary_json) "
            "VALUES ('ready-build','READY','v1',1,1,'{}','{}')"
        )
        connection.execute(
            "INSERT INTO promotion_runs(build_id,validation_manifest_hash,status) "
            "VALUES ('ready-build','hash','FAILED')"
        )
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        assert connection.execute(
            "SELECT status FROM promotion_runs WHERE build_id='ready-build'"
        ).fetchone()[0] == "FAILED"
        connection.execute(
            "UPDATE promotion_runs SET status='ROLLED_BACK' WHERE build_id='ready-build'"
        )
        assert connection.execute(
            "SELECT status FROM promotion_runs WHERE build_id='ready-build'"
        ).fetchone()[0] == "ROLLED_BACK"


def test_v3_writing_vector_build_keeps_resume_state_when_migrated_to_v5(tmp_path):
    path = tmp_path / "catalog-v3-writing-vector.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA_SQL)
        connection.executescript(CURATION_SCHEMA_SQL)
        connection.executescript(EVIDENCE_GRAPH_SCHEMA_SQL)
        connection.execute("INSERT INTO catalog_meta VALUES ('schema_version', '3')")
        connection.execute("PRAGMA user_version=3")
        connection.execute(
            """
            INSERT INTO graph_builds(
              id,status,curation_version,graph_schema_version,vector_schema_version,
              settings_json,summary_json
            ) VALUES ('build-resume','WRITING_VECTOR','v1',1,1,
              '{"catalog_schema_version":3}','{}')
            """
        )
    initialize_catalog(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT status FROM graph_builds WHERE id='build-resume'"
        ).fetchone()[0] == "WRITING_VECTOR"
        assert connection.execute(
            "SELECT COUNT(*) FROM topic_runs WHERE build_id='build-resume'"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_existing_v1_curating_build_resumes_through_stage2(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=2)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        for table in (
            "graph_export_partitions",
            "graph_export_rows",
            "publication_mentions",
            "research_statements",
            "graph_runs",
            "canonical_professors",
            "field_claims",
            "entity_observations",
            "identity_claims",
            "curation_overrides",
            "entities",
            "curation_runs",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink IN "
            "('curation_identity','curation_fields','curation_canonical','graph_evidence','graph_export','neo4j')",
            (build_id,),
        )
        frozen = json.loads(
            connection.execute(
                "SELECT settings_json FROM graph_builds WHERE id=?", (build_id,)
            ).fetchone()[0]
        )
        frozen["catalog_schema_version"] = 1
        frozen.pop("curation_queue", None)
        connection.execute(
            "UPDATE graph_builds SET status='CURATING', settings_json=?, last_error=NULL WHERE id=?",
            (json.dumps(frozen), build_id),
        )
        connection.execute("UPDATE catalog_meta SET value='1' WHERE key='schema_version'")
        connection.execute("PRAGMA user_version=1")
    backups_before = set((settings.catalog_path.parent / "backups").glob("catalog-*.db"))
    resumed = await resume_build(build_id, settings)
    assert resumed["build"]["status"] == "WRITING_VECTOR"
    assert resumed["curation"]["status"] == "COMPLETED"
    backups_after = set((settings.catalog_path.parent / "backups").glob("catalog-*.db"))
    new_backups = backups_after - backups_before
    assert len(new_backups) == 1
    backup = new_backups.pop()
    assert backup.with_suffix(backup.suffix + ".sha256").is_file()
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_professors WHERE build_id=? AND active=1",
            (build_id,),
        ).fetchone()[0] == 2
