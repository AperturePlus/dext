import sys
import importlib

import sqlite3
from contextlib import closing

from tests.dext_recommend._factfixtures import build_catalog_db


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
