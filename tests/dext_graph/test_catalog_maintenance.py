import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from dext_graph.cli import main
from dext_graph.catalog.db import (
    CatalogError,
    backup_existing_catalog,
    connect_catalog,
    initialize_catalog,
    json_dumps,
)
from dext_graph.catalog.maintenance import (
    catalog_size,
    compact_catalog,
    plan_catalog_compaction,
)
from dext_graph.config import GraphSettings


def _settings(path: Path, *, retention: int = 2) -> GraphSettings:
    return GraphSettings(catalog_path=path, catalog_backup_retention=retention)


def _insert_build(connection: sqlite3.Connection, build_id: str, status: str) -> None:
    connection.execute(
        """
        INSERT INTO graph_builds(
          id,status,curation_version,graph_schema_version,vector_schema_version,
          settings_json,summary_json,started_at
        ) VALUES (?, ?, 'curation-v1', 1, 1, '{}', '{}', '2026-01-01T00:00:00+00:00')
        """,
        (build_id, status),
    )
    connection.execute(
        """
        INSERT INTO graph_runs(
          id,build_id,status,evidence_version,export_version,summary_json,started_at
        ) VALUES (?, ?, 'COMPLETED', 'evidence-v1', 'neo4j-export-v1', ?, '2026-01-01T00:00:00+00:00')
        """,
        (
            f"graph-{build_id}",
            build_id,
            json_dumps({"partitions": {"node:Professor": {"row_count": 1}}}),
        ),
    )


def _insert_export(
    connection: sqlite3.Connection,
    build_id: str,
    *,
    rows: int = 2,
    payload_size: int = 16,
) -> None:
    payload = "x" * payload_size
    for index in range(rows):
        connection.execute(
            """
            INSERT INTO graph_export_rows(
              build_id,partition_key,row_key,row_kind,label_or_type,
              start_graph_key,end_graph_key,payload_json,provenance_ref,row_checksum
            ) VALUES (?, 'node:Professor', ?, 'node', 'Professor', NULL, NULL, ?, 'p', ?)
            """,
            (
                build_id,
                f"prof-{index:04d}",
                json.dumps({"graph_key": f"{build_id}:p{index}", "payload": payload}),
                f"checksum-{index}",
            ),
        )
    connection.execute(
        """
        INSERT INTO graph_export_partitions(
          build_id,partition_key,row_kind,label_or_type,row_count,min_key,max_key,checksum,generated_at
        ) VALUES (?, 'node:Professor', 'node', 'Professor', ?, 'prof-0000', ?, 'checksum', 'now')
        """,
        (build_id, rows, f"prof-{rows - 1:04d}"),
    )
    for sink in ("graph_export", "neo4j"):
        connection.execute(
            """
            INSERT INTO sink_checkpoints(
              build_id,sink,partition_key,last_key,last_batch_id,rows_written,updated_at
            ) VALUES (?, ?, 'node:Professor', 'prof-9999', NULL, ?, 'now')
            """,
            (build_id, sink, rows),
        )


def _insert_preserved_domain_rows(connection: sqlite3.Connection, build_id: str, snapshot_path: Path) -> None:
    connection.execute(
        """
        INSERT INTO source_snapshots(
          id,university_id,source_path,snapshot_path,file_hash,schema_version,row_counts_json,created_at
        ) VALUES ('snap-1','univ','source.db',?,'hash',1,'{}','now')
        """,
        (str(snapshot_path),),
    )
    connection.execute(
        """
        INSERT INTO professor_observations(
          id,university_id,source_snapshot_id,source_professor_id,source_url,
          source_page_kind,source_content_hash,source_document_id,org_unit_source_id,name_raw,name_key,
          payload_json,row_hash,provenance_grade,first_seen_build,last_seen_build,active
        ) VALUES (
          'obs-1','univ','snap-1',1,'https://example','unknown','content',NULL,NULL,
          '张三','zhangsan','{}','rowhash','direct',?,?,1
        )
        """,
        (build_id, build_id),
    )
    connection.execute(
        """
        INSERT INTO entities(id,kind,status,merged_into_id,created_at,updated_at)
        VALUES ('entity-1','professor','active',NULL,'now','now')
        """
    )
    connection.execute(
        """
        INSERT INTO field_claims(
          entity_id,field_name,normalized_value,observation_id,confidence,selected,selection_reason,build_id
        ) VALUES ('entity-1','email','a@example.com','obs-1',1.0,1,'test',?)
        """,
        (build_id,),
    )
    connection.execute(
        """
        INSERT INTO canonical_professors(
          entity_id,build_id,name,title_family,role_status,role_reason_codes,
          master_eligibility,phd_eligibility,active,completeness
        ) VALUES ('entity-1',?,'张三','professor','included','[]','unknown','unknown',1,1.0)
        """,
        (build_id,),
    )
    connection.execute(
        """
        INSERT INTO research_statements(
          id,build_id,entity_id,observation_id,raw_text,normalized_text,language,statement_hash
        ) VALUES ('stmt-1',?,'entity-1','obs-1','AI','AI','en','stmt-hash')
        """,
        (build_id,),
    )


def _seed_catalog(path: Path, *, payload_size: int = 16) -> None:
    initialize_catalog(path)
    snapshot_path = path.parent / "snapshots" / "source.db"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(b"snapshot")
    with closing(connect_catalog(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for build_id, status in [
            ("failed-old", "FAILED"),
            ("failed-keep", "FAILED"),
            ("ready-build", "READY"),
            ("active-build", "ACTIVE"),
            ("validating-build", "VALIDATING"),
            ("stale-build", "WRITING_VECTOR"),
        ]:
            _insert_build(connection, build_id, status)
            _insert_export(connection, build_id, rows=2, payload_size=payload_size)
        _insert_preserved_domain_rows(connection, "failed-old", snapshot_path)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _count(path: Path, sql: str, params: tuple[object, ...] = ()) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(sql, params).fetchone()[0])


def test_catalog_size_reports_dbstat_backups_snapshots_and_exports(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog)
    backup_existing_catalog(catalog, retention=2)

    result = catalog_size(_settings(catalog))

    assert result["files"]["catalog"]["bytes"] > 0
    assert result["backups"]["count"] == 1
    assert result["source_snapshots"]["count"] == 1
    assert result["source_snapshots"]["total_bytes"] == len(b"snapshot")
    assert "available" in result["dbstat"]
    assert result["graph_export_rows"]["total_rows"] == 12
    assert {row["build_id"] for row in result["graph_export_rows"]["by_build"]} >= {
        "failed-old",
        "failed-keep",
    }


def test_compact_dry_run_does_not_change_db(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog)
    before = _count(catalog, "SELECT COUNT(*) FROM graph_export_rows")

    result = compact_catalog(_settings(catalog), keep_build="failed-keep")

    assert result["dry_run"] is True
    assert result["executed"] is False
    assert [item["build_id"] for item in result["target_builds"]] == ["failed-old"]
    assert _count(catalog, "SELECT COUNT(*) FROM graph_export_rows") == before
    with sqlite3.connect(catalog) as connection:
        summary = json.loads(
            connection.execute(
                "SELECT summary_json FROM graph_runs WHERE build_id='failed-old'"
            ).fetchone()[0]
        )
    assert summary.get("export_pruned") is None


def test_compact_execute_prunes_failed_only_and_preserves_domain_rows(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog)

    result = compact_catalog(_settings(catalog), keep_build="failed-keep", yes=True)

    assert result["executed"] is True
    assert result["vacuum"]["ran"] is True
    assert _count(catalog, "SELECT COUNT(*) FROM graph_export_rows WHERE build_id='failed-old'") == 0
    assert _count(catalog, "SELECT COUNT(*) FROM graph_export_partitions WHERE build_id='failed-old'") == 0
    assert _count(
        catalog,
        "SELECT COUNT(*) FROM sink_checkpoints WHERE build_id='failed-old' AND sink IN ('graph_export','neo4j')",
    ) == 0
    for protected in ("failed-keep", "ready-build", "active-build", "validating-build"):
        assert _count(catalog, "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=?", (protected,)) == 2
    assert _count(catalog, "SELECT COUNT(*) FROM source_snapshots") == 1
    assert _count(catalog, "SELECT COUNT(*) FROM professor_observations") == 1
    assert _count(catalog, "SELECT COUNT(*) FROM field_claims") == 1
    assert _count(catalog, "SELECT COUNT(*) FROM canonical_professors") == 1
    assert _count(catalog, "SELECT COUNT(*) FROM research_statements") == 1
    with sqlite3.connect(catalog) as connection:
        graph_summary = json.loads(
            connection.execute(
                "SELECT summary_json FROM graph_runs WHERE build_id='failed-old'"
            ).fetchone()[0]
        )
        build_summary = json.loads(
            connection.execute(
                "SELECT summary_json FROM graph_builds WHERE id='failed-old'"
            ).fetchone()[0]
        )
    assert graph_summary["export_pruned"] is True
    assert graph_summary["partitions"] == {}
    assert build_summary["graph"]["export_pruned"] is True


def test_explicit_prune_can_clean_non_failed_but_not_protected(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog)

    result = compact_catalog(
        _settings(catalog),
        keep_build="failed-keep",
        prune_builds=("stale-build",),
        yes=True,
    )

    assert {item["build_id"] for item in result["target_builds"]} == {
        "failed-old",
        "stale-build",
    }
    assert _count(catalog, "SELECT COUNT(*) FROM graph_export_rows WHERE build_id='stale-build'") == 0
    with pytest.raises(CatalogError, match="protected"):
        plan_catalog_compaction(
            _settings(catalog),
            keep_build="failed-keep",
            prune_builds=("ready-build",),
        )


def test_compact_vacuum_shrinks_large_payload_db(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog, payload_size=20_000)
    before = catalog.stat().st_size

    compact_catalog(_settings(catalog), keep_build="failed-keep", yes=True)

    assert catalog.stat().st_size < before


def test_backup_retention_keeps_recent_pairs_and_removes_orphan_checksum(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog)
    backup_dir = catalog.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    (backup_dir / "catalog-orphan.db.sha256").write_text("orphan\n", encoding="ascii")

    for _ in range(3):
        backup_existing_catalog(catalog, retention=2)

    backups = sorted(backup_dir.glob("catalog-*.db"))
    checksums = sorted(backup_dir.glob("catalog-*.db.sha256"))
    assert len(backups) == 2
    assert len(checksums) == 2
    assert not (backup_dir / "catalog-orphan.db.sha256").exists()
    assert {path.name + ".sha256" for path in backups} == {path.name for path in checksums}


def test_catalog_maintenance_cli_size_and_compact_dry_run(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _seed_catalog(catalog)
    runner = CliRunner()
    env = {
        "DEXT_CATALOG_PATH": str(catalog),
        "DEXT_CATALOG_BACKUP_RETENTION": "2",
    }

    size = runner.invoke(main, ["graph", "catalog", "size"], env=env)
    assert size.exit_code == 0, size.output
    size_payload = json.loads(size.output)
    assert size_payload["graph_export_rows"]["total_rows"] == 12

    compact = runner.invoke(
        main,
        ["graph", "catalog", "compact", "--keep-build", "failed-keep"],
        env=env,
    )
    assert compact.exit_code == 0, compact.output
    compact_payload = json.loads(compact.output)
    assert compact_payload["dry_run"] is True
    assert compact_payload["target_builds"][0]["build_id"] == "failed-old"
    assert _count(catalog, "SELECT COUNT(*) FROM graph_export_rows") == 12
