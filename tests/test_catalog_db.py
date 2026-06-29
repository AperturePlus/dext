import asyncio
import sqlite3

import pytest

from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    catalog_write_lock,
    connect_catalog_read_only,
    initialize_catalog,
)
from dext_graph.catalog.models import CATALOG_SCHEMA_VERSION


def test_catalog_schema_is_versioned_and_complete(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    with connect_catalog_read_only(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "graph_builds",
        "build_source_tasks",
        "source_snapshots",
        "build_source_snapshots",
        "source_documents",
        "professor_observations",
        "sink_checkpoints",
        "quality_findings",
        "source_snapshot_protections",
        "curation_runs",
        "entities",
        "identity_claims",
        "entity_observations",
        "field_claims",
        "curation_overrides",
        "canonical_professors",
    }.issubset(tables)


def test_catalog_refuses_unversioned_existing_schema(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE old_catalog(id INTEGER)")
    with pytest.raises(CatalogError, match="no recognized schema version"):
        initialize_catalog(path)


async def test_catalog_writer_commits_and_rolls_back(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    async with CatalogWriter(path, max_queue=1) as writer:
        await writer.execute(
            lambda connection: connection.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('committed', 'yes')"
            )
        )

        def fail(connection):
            connection.execute(
                "INSERT INTO catalog_meta(key, value) VALUES ('rolled_back', 'no')"
            )
            raise RuntimeError("stop")

        with pytest.raises(RuntimeError, match="stop"):
            await writer.execute(fail)
    with connect_catalog_read_only(path) as connection:
        assert connection.execute(
            "SELECT value FROM catalog_meta WHERE key='committed'"
        ).fetchone()[0] == "yes"
        assert connection.execute(
            "SELECT value FROM catalog_meta WHERE key='rolled_back'"
        ).fetchone() is None


def test_catalog_write_lock_rejects_a_second_writer(tmp_path):
    path = tmp_path / "catalog.db"
    with catalog_write_lock(path):
        with pytest.raises(CatalogError, match="another process"):
            with catalog_write_lock(path):
                raise AssertionError("second writer unexpectedly acquired the lock")
