import sys
import importlib

import sqlite3
from contextlib import closing

from tests.dext_recommend._factfixtures import build_catalog_db, dumps


def test_catalog_fact_schema_constants():
    from dext_recommend.adapters._catalog_fact_schema import (
        MIN_FACT_CATALOG_SCHEMA_VERSION, REQUIRED_FACT_TABLES,
        REQUIRED_FACT_COLUMNS, FACT_SQLITE_PARAM_LIMIT,
    )
    assert MIN_FACT_CATALOG_SCHEMA_VERSION == 1
    for t in (
        "canonical_professors", "professor_profiles", "professor_observations",
        "entity_observations", "research_statements", "publication_mentions",
        "statement_topic_links", "topics", "quality_findings",
        "source_documents", "build_source_tasks",
    ):
        assert t in REQUIRED_FACT_TABLES, f"missing required table {t}"
    # profile payload JSON keys are part of the contract
    assert "payload_json" in REQUIRED_FACT_COLUMNS["professor_profiles"]
    assert "payload_json" in REQUIRED_FACT_COLUMNS["professor_observations"]
    assert "review_status" in REQUIRED_FACT_COLUMNS["statement_topic_links"]
    assert FACT_SQLITE_PARAM_LIMIT == 999


def test_catalog_fact_schema_does_not_import_dext_graph():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        importlib.import_module("dext_recommend.adapters._catalog_fact_schema")
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_build_catalog_db_creates_real_schema(tmp_path):
    path = build_catalog_db(
        tmp_path,
        schema_version=6,
        graph_builds=[("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}")],
        canonical_professors=[
            ("e1", "b1", "Prof A", "Prof.", "professor", "included", "[]",
             "confirmed", "unknown", None, None, None, None, "https://x/p", None, 1, 0.9),
        ],
        professor_profiles=[
            ("b1", "e1", "h1", "tv", "ti", "np", 10,
             '{"university_id":"u1","org_unit_ids":["ou_cs"],"city":null,"topic_ids":[],"profile_hash":"h1"}',
             "2026-01-01T00:00:00+00:00"),
        ],
    )
    with closing(sqlite3.connect(path)) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )}
        for required in ("canonical_professors", "professor_profiles",
                         "research_statements", "publication_mentions",
                         "statement_topic_links", "quality_findings"):
            assert required in names
        ver = conn.execute("SELECT value FROM catalog_meta WHERE key='schema_version'").fetchone()
        assert ver is not None and ver[0] == "6"


import asyncio
import pytest

from dext_recommend.adapters._catalog_fact_reader import CatalogSqliteFactReader
from dext_recommend.ports.release_readback import ReadinessSourceError


def _active_build():
    return ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}")


def test_check_capability_returns_schema_version(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    version = asyncio.run(reader.check_capability())
    assert version == 6


def test_check_capability_missing_file_raises_safe(tmp_path):
    reader = CatalogSqliteFactReader(tmp_path / "missing.db", timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.check_capability())
    assert exc.value.source == "catalog"
    # no raw path leak of credentials; the reason is safe
    assert "not found" in exc.value.reason.lower() or "missing" in exc.value.reason.lower()


def test_check_capability_missing_required_table_raises_safe(tmp_path):
    # build a DB then drop a required table
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    import sqlite3 as _sqlite3
    with _sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE statement_topic_links")
        conn.commit()
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.check_capability())
    assert "statement_topic_links" in exc.value.reason


def _profile_payload(entity_id, *, university_id="u1", org_unit_ids=("ou_cs",),
                     city=None, topic_ids=(), profile_hash=None):
    return dumps({
        "university_id": university_id,
        "org_unit_ids": list(org_unit_ids),
        "city": city,
        "topic_ids": list(topic_ids),
        "profile_hash": profile_hash or f"h_{entity_id}",
    })


def test_read_fact_rows_returns_active_non_excluded(tmp_path):
    profs = [
        # e1 included, has profile
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e1", None, 1, 0.9),
        # e2 excluded -> must NOT appear
        ("e2", "b1", "B", "Prof.", "professor", "excluded", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e2", None, 1, 0.5),
        # e3 inactive -> must NOT appear
        ("e3", "b1", "C", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e3", None, 0, 0.5),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile_payload("e1"), "2026-01-01T00:00:00+00:00"),
        ("b1", "e2", "h2", "tv", "ti", "np", 10, _profile_payload("e2"), "2026-01-01T00:00:00+00:00"),
        ("b1", "e3", "h3", "tv", "ti", "np", 10, _profile_payload("e3"), "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_fact_rows("b1", ["e1", "e2", "e3"]))
    ids = [r["entity_id"] for r in rows]
    assert ids == ["e1"]  # e2 excluded, e3 inactive


def test_read_fact_rows_parses_payload_authority_fields(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10,
         _profile_payload("e1", university_id="u_tsinghua",
                          org_unit_ids=("ou_cs", "ou_ai"),
                          city="Beijing", topic_ids=("t1", "t2")),
         "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_fact_rows("b1", ["e1"]))
    assert len(rows) == 1
    r = rows[0]
    assert r["entity_id"] == "e1"
    assert r["display_name"] == "A"
    assert r["university_id"] == "u_tsinghua"
    assert tuple(r["org_unit_ids"]) == ("ou_cs", "ou_ai")
    assert r["city_name"] == "Beijing"
    assert tuple(r["topic_ids"]) == ("t1", "t2")
    assert r["profile_hash"] == "h1"
    assert r["master_eligibility"] == "confirmed"
    assert r["role_status"] == "included"


def test_read_fact_rows_empty_ids_returns_empty(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_fact_rows("b1", []))
    assert rows == ()


def test_read_fact_rows_chunked_over_param_limit(tmp_path):
    # 1200 entities, reader must chunk below FACT_SQLITE_PARAM_LIMIT
    profs = [
        (f"e{i}", "b1", f"N{i}", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5)
        for i in range(1200)
    ]
    profiles = [
        ("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10,
         _profile_payload(f"e{i}"), "2026-01-01T00:00:00+00:00")
        for i in range(1200)
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=10.0)
    ids = [f"e{i}" for i in range(1200)]
    rows = asyncio.run(reader.read_fact_rows("b1", ids))
    got = {r["entity_id"] for r in rows}
    assert len(got) == 1200
    assert got == set(ids)


def test_read_fact_rows_invalid_payload_raises_safe_error(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, "not-json{", "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.read_fact_rows("b1", ["e1"]))
    assert exc.value.source == "catalog"
    # raw payload must NOT leak into the reason
    assert "not-json{" not in exc.value.reason


def test_read_detail_rows_returns_full_evidence(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", "areas", "bio1", "e@x", "123", "https://x/p", "https://x/e", 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10,
         _profile_payload("e1", university_id="u1", org_unit_ids=("ou_cs",), city="Beijing"),
         "2026-01-01T00:00:00+00:00"),
    ]
    observations = [
        ("obs1", "u1", "snap1", "https://x/p", "single_profile",
         dumps({"affiliations": [{"org_unit_name": "Dept CS"}]}),
         "rh1", "direct", "b1", "b1", 1),
    ]
    entity_observations = [("e1", "obs1", "strong", "b1")]
    statements = [
        ("s1", "b1", "e1", "obs1", "raw works on NLP", "works on NLP", "en", "sh1"),
    ]
    mentions = [
        ("m1", "b1", "e1", "obs1", "raw paper", "Paper A", 2024, 0.9, 0),
    ]
    topics = [("tax-v1", "t1", "NLP", "nlp", "method", "active", "llm")]
    topic_links = [
        ("b1", "s1", "tax-v1", "t1", "PRIMARY_TOPIC", "span", "llm", 0.95, "approved",
         "catalog:research-statement:b1:s1"),
    ]
    findings = [
        ("f1", "b1", "warning", "incomplete_profile", "e1", None,
         dumps({"note": "missing phone"}), 0),
    ]
    source_docs = [
        ("doc1", "u1", "https://x/p", "ch1", "2026-01-01T00:00:00+00:00", "b1", "b1"),
    ]
    source_tasks = [
        ("b1", "u1", "Tsinghua", "tsinghua", "/src", 0, "COMPLETED", "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
        professor_observations=observations, entity_observations=entity_observations,
        research_statements=statements, publication_mentions=mentions,
        topics=topics, statement_topic_links=topic_links,
        quality_findings=findings, source_documents=source_docs,
        build_source_tasks=source_tasks, entities=[("e1", "professor", "active", None, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")],
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_detail_rows("b1", "e1"))
    assert rows is not None
    assert rows.canonical["display_name"] == "A"
    assert rows.profile_payload["university_id"] == "u1"
    assert rows.university_name == "Tsinghua"
    assert len(rows.statements) == 1
    assert rows.statements[0]["normalized_text"] == "works on NLP"
    assert len(rows.mentions) == 1
    assert len(rows.topic_links) == 1
    assert rows.topic_links[0]["canonical_name"] == "NLP"
    assert rows.topic_links[0]["review_status"] == "approved"
    assert len(rows.findings) == 1
    assert len(rows.source_urls) == 1
    assert "https://x/p" in rows.source_urls[0]["source_url"]


def test_read_detail_rows_excluded_returns_none(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "excluded", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    assert asyncio.run(reader.read_detail_rows("b1", "e1")) is None


def test_read_detail_rows_missing_returns_none(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    assert asyncio.run(reader.read_detail_rows("b1", "nope")) is None


def test_read_detail_rows_review_entity_returned_for_gating(tmp_path):
    # review entities ARE returned by the reader; the adapter gates on viewer perms
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "review", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs,
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    rows = asyncio.run(reader.read_detail_rows("b1", "e1"))
    assert rows is not None
    assert rows.canonical["role_status"] == "review"


# --- Task 6: read-only / thread-offload / timeout invariants ---

import time  # noqa: E402


def test_fact_reader_connect_ro_blocks_writes(tmp_path):
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=[
            ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
             "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ],
        professor_profiles=[
            ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile_payload("e1"),
             "2026-01-01T00:00:00+00:00"),
        ],
    )
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    conn = reader._connect_ro()
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO graph_builds(id,status,curation_version,graph_schema_version,vector_schema_version,settings_json) VALUES('x','ACTIVE','c',1,1,'{}')")
    finally:
        conn.close()


async def test_fact_reader_does_not_block_event_loop(tmp_path):
    # 800 entities; while read_fact_rows runs, a heartbeat task must keep ticking.
    profs = [
        (f"e{i}", "b1", f"N{i}", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5)
        for i in range(800)
    ]
    profiles = [
        ("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10, _profile_payload(f"e{i}"),
         "2026-01-01T00:00:00+00:00")
        for i in range(800)
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=10.0)
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        for _ in range(20):
            await asyncio.sleep(0.005)
            ticks += 1

    ids = [f"e{i}" for i in range(800)]
    hb = asyncio.create_task(heartbeat())
    await reader.read_fact_rows("b1", ids)
    await hb
    # if SQLite blocked the loop, ticks would be 0-1; thread offload => many ticks
    assert ticks >= 10


def test_fact_reader_timeout_raises_safe_error(tmp_path):
    # use a path that exists but force a tiny timeout while reading many rows
    profs = [
        (f"e{i}", "b1", f"N{i}", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5)
        for i in range(2000)
    ]
    profiles = [
        ("b1", f"e{i}", f"h{i}", "tv", "ti", "np", 10, _profile_payload(f"e{i}"),
         "2026-01-01T00:00:00+00:00")
        for i in range(2000)
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    reader = CatalogSqliteFactReader(path, timeout=0.001)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.read_fact_rows("b1", [f"e{i}" for i in range(2000)]))
    assert exc.value.source == "catalog"
    assert exc.value.retryable is True


def test_check_capability_corrupt_file_raises_safe_error(tmp_path):
    # a file that exists but is not a valid SQLite database
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a database")
    reader = CatalogSqliteFactReader(path, timeout=5.0)
    with pytest.raises(ReadinessSourceError) as exc:
        asyncio.run(reader.check_capability())
    assert exc.value.source == "catalog"
    # the raw sqlite3 message must NOT leak
    assert "not a database" not in exc.value.reason
    assert "database disk image is malformed" not in exc.value.reason.lower()
