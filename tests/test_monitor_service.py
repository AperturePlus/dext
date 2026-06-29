from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from click.testing import CliRunner

from dext_graph.cli import main
from dext_monitor.server import create_app
from dext_monitor.service import MonitorService
from dext_monitor.settings import MonitorSettings


def _write_catalog(path: Path, *, version: int = 3, with_build: bool = True) -> str | None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE graph_builds(
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                source_manifest_hash TEXT,
                curation_version TEXT NOT NULL,
                taxonomy_version TEXT,
                graph_schema_version INTEGER NOT NULL,
                vector_schema_version INTEGER NOT NULL,
                embedding_provider TEXT,
                embedding_base_url TEXT,
                embedding_model TEXT,
                embedding_fingerprint TEXT,
                embedding_dimension INTEGER,
                settings_json TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT,
                finished_at TEXT,
                last_error TEXT
            );
            CREATE TABLE build_source_tasks(
                build_id TEXT NOT NULL,
                university_id TEXT NOT NULL,
                university_name TEXT NOT NULL,
                abbr TEXT NOT NULL,
                source_path TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                status TEXT NOT NULL,
                source_snapshot_id TEXT,
                snapshot_reused INTEGER NOT NULL DEFAULT 0,
                rows_read INTEGER NOT NULL DEFAULT 0,
                observations_written INTEGER NOT NULL DEFAULT 0,
                observations_inserted INTEGER NOT NULL DEFAULT 0,
                observations_reused INTEGER NOT NULL DEFAULT 0,
                documents_seen INTEGER NOT NULL DEFAULT 0,
                documents_inserted INTEGER NOT NULL DEFAULT 0,
                findings INTEGER NOT NULL DEFAULT 0,
                rejected_rows INTEGER NOT NULL DEFAULT 0,
                deactivation_eligible INTEGER NOT NULL DEFAULT 0,
                observations_inactivated INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE sink_checkpoints(
                build_id TEXT NOT NULL,
                sink TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                last_key TEXT,
                last_batch_id TEXT,
                rows_written INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE quality_findings(
                id TEXT PRIMARY KEY,
                build_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                code TEXT NOT NULL,
                entity_id TEXT,
                observation_id TEXT,
                details_json TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE curation_runs(
                id TEXT PRIMARY KEY,
                build_id TEXT NOT NULL,
                status TEXT NOT NULL,
                curation_version TEXT NOT NULL,
                normalization_version TEXT NOT NULL,
                rules_hash TEXT NOT NULL,
                override_manifest_hash TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT,
                finished_at TEXT,
                last_error TEXT
            );
            CREATE TABLE canonical_professors(
                entity_id TEXT NOT NULL,
                build_id TEXT NOT NULL,
                name TEXT NOT NULL,
                title_raw TEXT,
                title_family TEXT NOT NULL,
                role_status TEXT NOT NULL,
                role_reason_codes TEXT NOT NULL,
                master_eligibility TEXT NOT NULL,
                phd_eligibility TEXT NOT NULL,
                research_areas_text TEXT,
                bio TEXT,
                email TEXT,
                phone TEXT,
                profile_url TEXT,
                external_url TEXT,
                active INTEGER NOT NULL,
                completeness REAL NOT NULL
            );
            CREATE TABLE graph_runs(
                id TEXT PRIMARY KEY,
                build_id TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_version TEXT NOT NULL,
                export_version TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}',
                started_at TEXT,
                finished_at TEXT,
                last_error TEXT
            );
            CREATE TABLE graph_export_rows(
                build_id TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                row_key TEXT NOT NULL,
                row_kind TEXT NOT NULL,
                label_or_type TEXT NOT NULL,
                start_graph_key TEXT,
                end_graph_key TEXT,
                payload_json TEXT NOT NULL,
                provenance_ref TEXT NOT NULL,
                row_checksum TEXT NOT NULL
            );
            CREATE TABLE graph_export_partitions(
                build_id TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                row_kind TEXT NOT NULL,
                label_or_type TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                min_key TEXT,
                max_key TEXT,
                checksum TEXT NOT NULL,
                generated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(f"PRAGMA user_version={version}")
        if not with_build:
            connection.commit()
            return None
        build_id = "build-1"
        connection.execute(
            """
            INSERT INTO graph_builds(
                id,status,curation_version,graph_schema_version,vector_schema_version,
                embedding_provider,embedding_base_url,embedding_model,embedding_dimension,
                settings_json,summary_json,started_at,last_error
            ) VALUES (
                ?, 'WRITING_VECTOR', 'curation-v1', 1, 1,
                'siliconflow', 'https://example/v1', 'bge-m3', 1024,
                '{}', '{"rows_read":15,"observations_written":12,"documents_seen":7}',
                '2026-06-29T00:00:00+00:00', NULL
            )
            """,
            (build_id,),
        )
        for ordinal, (university_id, status) in enumerate(
            [("univ:a", "COMPLETED"), ("univ:b", "FAILED")]
        ):
            connection.execute(
                """
                INSERT INTO build_source_tasks(
                    build_id, university_id, university_name, abbr, source_path, ordinal, status,
                    rows_read, observations_written, documents_seen, findings, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    build_id,
                    university_id,
                    f"大学{ordinal}",
                    f"u{ordinal}",
                    f"{university_id}.db",
                    ordinal,
                    status,
                    10 + ordinal,
                    8 + ordinal,
                    4 + ordinal,
                    ordinal,
                    "2026-06-29T00:00:00+00:00",
                ),
            )
        connection.execute(
            "INSERT INTO quality_findings VALUES ('f1', ?, 'warning', 'identity_conflict', 'e1', 'o1', '{\"x\":1}', 0)",
            (build_id,),
        )
        connection.execute(
            "INSERT INTO quality_findings VALUES ('f2', ?, 'error', 'bad_row', NULL, NULL, '{}', 1)",
            (build_id,),
        )
        connection.execute(
            """
            INSERT INTO curation_runs VALUES (
                'cur-1', ?, 'COMPLETED', 'curation-v1', 'norm-v1', 'rules', 'overrides',
                '{"canonical_active":2}', '2026-06-29T00:00:00+00:00', NULL, NULL
            )
            """,
            (build_id,),
        )
        connection.execute(
            """
            INSERT INTO graph_runs VALUES (
                'graph-1', ?, 'COMPLETED', 'evidence-v1', 'export-v1',
                '{"evidence":{"research_statements":1}}', '2026-06-29T00:00:00+00:00', NULL, NULL
            )
            """,
            (build_id,),
        )
        for entity_id, role, family in [
            ("e1", "included", "professor"),
            ("e2", "review", "lecturer"),
        ]:
            connection.execute(
                """
                INSERT INTO canonical_professors(
                    entity_id,build_id,name,title_family,role_status,role_reason_codes,
                    master_eligibility,phd_eligibility,active,completeness
                ) VALUES (?, ?, ?, ?, ?, '[]', 'unknown', 'unknown', 1, 0.5)
                """,
                (entity_id, build_id, entity_id, family, role),
            )
        rows = [
            ("node:Build", "build", "node", "Build", None, None, '{"id":"build-1","graph_key":"build-1"}'),
            ("node:University", "u", "node", "University", None, None, '{"graph_key":"u","name":"大学"}'),
            ("node:OrgUnit", "org", "node", "OrgUnit", None, None, '{"graph_key":"org","name":"学院"}'),
            ("rel:PART_OF", "org|u", "relationship", "PART_OF", "org", "u", '{"graph_key":"r1"}'),
            ("rel:FROM_UNIVERSITY", "x|u", "relationship", "FROM_UNIVERSITY", "x", "u", '{"graph_key":"r2"}'),
        ]
        for row in rows:
            connection.execute(
                "INSERT INTO graph_export_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'p', 'c')",
                (build_id, *row),
            )
        for partition, kind, label, count in [
            ("node:Build", "node", "Build", 1),
            ("node:University", "node", "University", 1),
            ("node:OrgUnit", "node", "OrgUnit", 1),
            ("rel:PART_OF", "relationship", "PART_OF", 1),
            ("rel:FROM_UNIVERSITY", "relationship", "FROM_UNIVERSITY", 1),
        ]:
            connection.execute(
                "INSERT INTO graph_export_partitions VALUES (?, ?, ?, ?, ?, NULL, NULL, 'checksum', 'now')",
                (build_id, partition, kind, label, count),
            )
        connection.commit()
        return build_id
    finally:
        connection.close()


def _settings(path: Path) -> MonitorSettings:
    return MonitorSettings(catalog_path=path, monitor_static_dir=path.parent / "dist")


def test_monitor_service_lists_build_detail_metrics_and_preview(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_catalog(catalog)
    assert build_id is not None
    service = MonitorService(_settings(catalog))

    health = service.health()
    assert health["readable"] is True
    assert health["schema_version"] == 3

    builds = service.list_builds()
    assert builds["latest_build_id"] == build_id
    assert builds["builds"][0]["summary"]["canonical_active"] == 2

    detail = service.build_detail(build_id)
    assert len(detail["sources"]) == 2
    assert detail["unresolved_findings"] == {"warning": 1}

    metrics = service.metrics(build_id)
    assert metrics["source_status_counts"] == {"COMPLETED": 1, "FAILED": 1}
    assert metrics["export_partitions"][0]["row_count"] == 1
    # role_counts/title_family_counts were computed every poll but never rendered;
    # they were dropped to avoid per-poll waste. Re-adding them requires a consumer.
    assert "role_counts" not in metrics
    assert "title_family_counts" not in metrics

    preview = service.graph_preview(build_id, limit=4)
    assert len(preview["nodes"]) == 2
    assert preview["total_nodes"] == 3
    assert preview["truncated"] is True

    findings = service.findings(build_id=build_id, severity="warning")
    assert [row["id"] for row in findings["findings"]] == ["f1"]


def test_monitor_handles_empty_and_incompatible_catalog(tmp_path: Path) -> None:
    empty_catalog = tmp_path / "empty.db"
    _write_catalog(empty_catalog, with_build=False)
    service = MonitorService(_settings(empty_catalog))
    builds = service.list_builds()
    assert builds["builds"] == []
    assert builds["latest_build_id"] is None

    old_catalog = tmp_path / "old.db"
    _write_catalog(old_catalog, version=2)
    old_service = MonitorService(_settings(old_catalog))
    assert old_service.health()["readable"] is False
    with pytest.raises(Exception, match="schema version 2"):
        old_service.list_builds()

    missing = MonitorService(_settings(tmp_path / "missing.db"))
    assert missing.health()["readable"] is False


@pytest.mark.asyncio
async def test_monitor_aiohttp_api(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_catalog(catalog)
    assert build_id is not None
    app = create_app(_settings(catalog))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/monitor/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["readable"] is True

        resp = await client.get(f"/api/monitor/builds/{build_id}/metrics")
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["build_id"] == build_id

        resp = await client.get("/api/monitor/builds/nope")
        assert resp.status == 404
        body = await resp.json()
        assert body["error"]["type"] == "MonitorCatalogError"


def test_monitor_cli_is_top_level_and_lazy_imports_monitor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure importing the CLI itself does not import the monitor package or server.
    sys.modules.pop("dext_monitor.server", None)
    sys.modules.pop("dext_graph.catalog.workflow", None)
    importlib.reload(sys.modules["dext_graph.cli"])
    assert "dext_monitor.server" not in sys.modules
    assert "dext_graph.catalog.workflow" not in sys.modules

    runner = CliRunner()
    result = runner.invoke(main, ["monitor", "serve", "--help"])
    assert result.exit_code == 0
    assert "Start the decoupled read-only monitor service" in result.output
    assert "Defaults to localhost" in result.output
    assert "Defaults to 21530" in result.output
