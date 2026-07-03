import sqlite3
from pathlib import Path

import pytest

from dext_graph.catalog.db import CatalogError, file_sha256
from dext_graph.catalog.source import (
    create_source_snapshot,
    inspect_snapshot,
    iter_legacy_batches,
)


def _wal_source(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.executescript(
        """
        CREATE TABLE university_meta(
          id INTEGER PRIMARY KEY, name TEXT, abbr TEXT, crawl_status TEXT,
          schema_version INTEGER
        );
        CREATE TABLE professors(
          id INTEGER PRIMARY KEY, name TEXT, org_unit_name TEXT, title TEXT,
          research_areas TEXT, email TEXT, phone TEXT, homepage TEXT,
          external_link TEXT, bio TEXT, enrollment_pref TEXT, publications TEXT,
          created_at TEXT, updated_at TEXT
        );
        CREATE TABLE org_units(id INTEGER PRIMARY KEY, name TEXT, status TEXT);
        CREATE TABLE professor_affiliations(
          id INTEGER PRIMARY KEY, professor_id INTEGER, org_unit_id INTEGER
        );
        CREATE TABLE crawl_page_cache(
          url TEXT PRIMARY KEY, content_hash TEXT, title TEXT,
          text_snapshot TEXT, updated_at TEXT
        );
        CREATE TABLE crawl_graph_nodes(id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE crawl_extraction_attempts(id INTEGER PRIMARY KEY, status TEXT);
        """
    )
    connection.execute(
        "INSERT INTO university_meta VALUES (1, '测试大学', 'test', 'completed', 1)"
    )
    connection.execute("INSERT INTO org_units VALUES (1, '计算机学院', 'completed')")
    connection.execute("INSERT INTO crawl_graph_nodes VALUES (1, 'done')")
    connection.execute(
        "INSERT INTO professors VALUES "
        "(1, '张三', '计算机学院', '教授', '图学习', 'z@example.cn', NULL, "
        " 'https://cs.example.edu.cn/p/1', NULL, '简介', '博导', '论文', 't0', 't1')"
    )
    connection.execute("INSERT INTO professor_affiliations VALUES (1, 1, 1)")
    connection.execute(
        "INSERT INTO crawl_page_cache VALUES "
        "('https://cs.example.edu.cn/p/1', 'hash-1', '张三', '第一行\n第二行', '2026-01-01T00:00:00+00:00')"
    )
    connection.commit()
    return connection


async def test_online_snapshot_reads_live_wal_without_mutating_source(tmp_path):
    source = tmp_path / "school.db"
    connection = _wal_source(source)
    wal = Path(str(source) + "-wal")
    assert wal.stat().st_size > 0
    source_before = (file_sha256(source), source.stat().st_mtime_ns)
    wal_before = (file_sha256(wal), wal.stat().st_mtime_ns)
    result = await create_source_snapshot(
        source,
        tmp_path / "snapshots",
        university_id="univ:test",
        abbr="test",
        build_id="build-1",
    )
    assert result.inspection.row_counts["professors"] == 1
    assert result.inspection.deactivation_state_is_complete()
    assert source_before == (file_sha256(source), source.stat().st_mtime_ns)
    assert wal_before == (file_sha256(wal), wal.stat().st_mtime_ns)
    rows = list(iter_legacy_batches(result.path, result.inspection, batch_size=1))
    assert len(rows) == 1
    professor = rows[0][0]
    assert professor.payload["name"] == "张三"
    assert professor.primary_org_unit_id == 1
    assert professor.page_content_hash == "hash-1"
    assert not Path(str(result.path) + "-wal").exists()
    assert not Path(str(result.path) + "-shm").exists()
    with sqlite3.connect(
        f"{result.path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    ) as snapshot:
        assert snapshot.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    connection.close()


async def test_snapshot_reuses_identical_content_file(tmp_path):
    source = tmp_path / "school.db"
    connection = _wal_source(source)
    first = await create_source_snapshot(
        source,
        tmp_path / "snapshots",
        university_id="univ:test",
        abbr="test",
        build_id="build-1",
    )
    second = await create_source_snapshot(
        source,
        tmp_path / "snapshots",
        university_id="univ:test",
        abbr="test",
        build_id="build-2",
    )
    assert first.file_hash == second.file_hash
    assert first.path == second.path
    assert second.reused_file
    connection.close()


async def test_snapshot_backup_failure_removes_owned_temporary_files(tmp_path):
    source = tmp_path / "school.db"
    connection = _wal_source(source)

    def stop_backup(_status, _remaining, _total):
        raise RuntimeError("stop backup")

    with pytest.raises(RuntimeError, match="stop backup"):
        await create_source_snapshot(
            source,
            tmp_path / "snapshots",
            university_id="univ:test",
            abbr="test",
            build_id="build-failed",
            progress_hook=stop_backup,
        )
    assert not list((tmp_path / "snapshots").rglob(".build-failed.tmp.db*"))
    connection.close()


def test_immutable_snapshot_rejects_nonempty_wal(tmp_path):
    source = tmp_path / "not-self-contained.db"
    connection = _wal_source(source)
    assert Path(str(source) + "-wal").stat().st_size > 0
    with pytest.raises(CatalogError, match="non-empty sidecar"):
        inspect_snapshot(source)
    connection.close()


def test_minimal_legacy_schema_degrades_without_optional_tables(tmp_path):
    source = tmp_path / "minimal.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE university_meta(name TEXT, crawl_status TEXT);
            CREATE TABLE professors(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO university_meta VALUES ('最小大学', 'failed');
            INSERT INTO professors VALUES (1, '李四');
            """
        )
    inspection = inspect_snapshot(source)
    assert inspection.row_counts["professors"] == 1
    assert "crawl_page_cache" in inspection.missing_optional_tables
    row = list(iter_legacy_batches(source, inspection))[0][0]
    assert row.page_url is None
    assert row.affiliations == ()
