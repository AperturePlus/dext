import sqlite3
import json

import pytest

from dext_graph.catalog.db import initialize_catalog
from dext_graph.catalog.models import CATALOG_SCHEMA_VERSION, SCHEMA_SQL
from dext_graph.catalog.workflow import create_build, resume_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


def test_v1_catalog_migrates_in_place_to_v2(tmp_path):
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
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink LIKE 'curation_%'",
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
    resumed = await resume_build(build_id, settings)
    assert resumed["build"]["status"] == "EMBEDDING"
    assert resumed["curation"]["status"] == "COMPLETED"
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM canonical_professors WHERE build_id=? AND active=1",
            (build_id,),
        ).fetchone()[0] == 2
