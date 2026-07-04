import sqlite3
import json
from contextlib import closing
from datetime import datetime, timezone

import pytest

from dext_recommend.adapters._catalog_reader import CatalogSqliteReader
from dext_recommend.adapters.catalog_release import CatalogReleaseAdapter


SCHEMA = """
CREATE TABLE graph_builds (
    id TEXT PRIMARY KEY, status TEXT NOT NULL,
    curation_version TEXT NOT NULL, taxonomy_version TEXT,
    graph_schema_version INTEGER NOT NULL, vector_schema_version INTEGER NOT NULL,
    embedding_provider TEXT, embedding_base_url TEXT, embedding_model TEXT,
    embedding_fingerprint TEXT, embedding_dimension INTEGER,
    settings_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT, finished_at TEXT, last_error TEXT
);
CREATE TABLE canonical_professors (
    entity_id TEXT, build_id TEXT, name TEXT, title_raw TEXT,
    title_family TEXT, role_status TEXT, role_reason_codes TEXT,
    master_eligibility TEXT, phd_eligibility TEXT, research_areas_text TEXT,
    bio TEXT, email TEXT, phone TEXT, profile_url TEXT, external_url TEXT,
    active INTEGER, completeness REAL, PRIMARY KEY (entity_id, build_id)
);
CREATE TABLE professor_profiles (
    build_id TEXT, entity_id TEXT, profile_hash TEXT, template_version TEXT,
    tokenizer_identity TEXT, normalized_profile TEXT, token_count INTEGER,
    payload_json TEXT, created_at TEXT, PRIMARY KEY (build_id, entity_id)
);
"""


def _build_db(tmp_path, *, active_rows, professors, profiles):
    path = tmp_path / "catalog.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,"
            "graph_schema_version,vector_schema_version,embedding_provider,"
            "embedding_model,embedding_fingerprint,embedding_dimension,settings_json)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            active_rows,
        )
        conn.executemany(
            "INSERT INTO canonical_professors(entity_id,build_id,name,title_family,"
            "role_status,role_reason_codes,master_eligibility,phd_eligibility,active,"
            "completeness) VALUES(?,?,?,?,?,?,?,?,1,0.5)",
            professors,
        )
        conn.executemany(
            "INSERT INTO professor_profiles(build_id,entity_id,profile_hash,"
            "template_version,tokenizer_identity,normalized_profile,token_count,"
            "payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            profiles,
        )
        conn.commit()
    return path


def _active_row(build_id="b1"):
    return (build_id, "ACTIVE", "cur-v1", "tax-v1", 6, 6, "openai",
            "text-embedding-3-small", "fp-1", 1536, "{}")


def test_read_active_returns_observation_with_sample_ids(tmp_path):
    profs = [(f"e{i}", "b1", f"name{i}", "professor", "included", "[]",
              "confirmed", "unknown") for i in range(60)]
    profiles = [("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10, "{}", "2026-01-01T00:00:00+00:00") for i in range(60)]
    path = _build_db(tmp_path, active_rows=[_active_row()], professors=profs, profiles=profiles)
    adapter = CatalogReleaseAdapter(CatalogSqliteReader(path), sample_size=10)
    import asyncio
    obs = asyncio.run(adapter.read_active())
    assert obs is not None
    assert obs.build_id == "b1"
    assert obs.embedding_fingerprint == "fp-1"
    assert len(obs.sample_entity_ids) == 10


async def test_read_active_returns_none_when_no_active(tmp_path):
    path = _build_db(tmp_path, active_rows=[], professors=[], profiles=[])
    adapter = CatalogReleaseAdapter(CatalogSqliteReader(path), sample_size=10)
    assert await adapter.read_active() is None


async def test_read_samples_returns_professor_samples(tmp_path):
    profs = [("e1", "b1", "n1", "professor", "included", "[]", "confirmed", "unknown")]
    profiles = [
        (
            "b1", "e1", "h1", "tv", "ti", "np", 10,
            json.dumps({"org_unit_ids": ["org-a", "org-b"]}),
            "2026-01-01T00:00:00+00:00",
        )
    ]
    path = _build_db(tmp_path, active_rows=[_active_row()], professors=profs, profiles=profiles)
    adapter = CatalogReleaseAdapter(CatalogSqliteReader(path), sample_size=10)
    samples = await adapter.read_samples("b1", ("e1",))
    assert len(samples) == 1
    assert samples[0].entity_id == "e1"
    assert samples[0].profile_hash == "h1"
    assert samples[0].org_unit_ids == ("org-a", "org-b")


def test_connect_ro_rejects_missing_file(tmp_path):
    from dext_recommend.adapters._catalog_reader import CatalogSqliteReader
    from dext_recommend.ports.release_readback import ReadinessSourceError
    reader = CatalogSqliteReader(tmp_path / "nonexistent.db")
    with pytest.raises(ReadinessSourceError):
        import asyncio
        asyncio.run(reader.read_active_row())


def test_connect_ro_query_only_blocks_writes(tmp_path):
    path = _build_db(tmp_path, active_rows=[_active_row()],
                     professors=[("e1","b1","n","professor","included","[]","confirmed","unknown")],
                     profiles=[("b1","e1","h","tv","ti","np",10,"{}","2026-01-01T00:00:00+00:00")])
    reader = CatalogSqliteReader(path)
    import asyncio
    async def _try_write():
        conn = reader._connect_ro(path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,graph_schema_version,vector_schema_version,settings_json) VALUES('x','ACTIVE','c','t',1,1,'{}')")
        finally:
            conn.close()
    asyncio.run(_try_write())
