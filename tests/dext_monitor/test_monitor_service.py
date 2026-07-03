from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer
from click.testing import CliRunner

from dext_graph.cli import main
from dext_monitor.catalog_reader import MonitorCatalogError
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


def _write_tree_catalog(path: Path) -> str:
    """Same schema as ``_write_catalog`` but with a richer topology:
    2 universities, 2 orgunits, and ``AFFILIATED_WITH`` rows so professor
    counts are non-zero. Used by the ``graph_tree`` service test."""
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
        connection.execute("PRAGMA user_version=3")
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
        rows = [
            ("node:Build", "build", "node", "Build", None, None, '{"id":"build-1","graph_key":"build-1"}'),
            ("node:University", "row-u", "node", "University", None, None, '{"graph_key":"u","name":"大学A","logical_id":"univ:a"}'),
            ("node:University", "row-u2", "node", "University", None, None, '{"graph_key":"u2","name":"大学B","logical_id":"univ:b"}'),
            ("node:OrgUnit", "row-org", "node", "OrgUnit", None, None, '{"graph_key":"org","name":"学院A1","kind":"college"}'),
            ("node:OrgUnit", "row-org2", "node", "OrgUnit", None, None, '{"graph_key":"org2","name":"研究所B1","kind":"institute"}'),
            ("rel:PART_OF", "org|u", "relationship", "PART_OF", "org", "u", '{"graph_key":"r1"}'),
            ("rel:PART_OF", "org2|u2", "relationship", "PART_OF", "org2", "u2", '{"graph_key":"r1b"}'),
            ("rel:AFFILIATED_WITH", "p1|org", "relationship", "AFFILIATED_WITH", "p1", "org", '{"graph_key":"a1"}'),
            ("rel:AFFILIATED_WITH", "p2|org", "relationship", "AFFILIATED_WITH", "p2", "org", '{"graph_key":"a2"}'),
            ("rel:AFFILIATED_WITH", "p3|org2", "relationship", "AFFILIATED_WITH", "p3", "org2", '{"graph_key":"a3"}'),
            ("rel:FROM_UNIVERSITY", "x|u", "relationship", "FROM_UNIVERSITY", "x", "u", '{"graph_key":"r2"}'),
        ]
        for row in rows:
            connection.execute(
                "INSERT INTO graph_export_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'p', 'c')",
                (build_id, *row),
            )
        for partition, kind, label, count in [
            ("node:Build", "node", "Build", 1),
            ("node:University", "node", "University", 2),
            ("node:OrgUnit", "node", "OrgUnit", 2),
            ("rel:PART_OF", "relationship", "PART_OF", 2),
            ("rel:AFFILIATED_WITH", "relationship", "AFFILIATED_WITH", 3),
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
    # P2-15: role_counts / title_family_counts are grouped over
    # canonical_professors WHERE build_id=? AND active=1. The fixture inserts
    # e1/included/professor and e2/review/lecturer, both active=1.
    assert metrics["role_counts"] == {"included": 1, "review": 1}
    assert metrics["title_family_counts"] == {"professor": 1, "lecturer": 1}

    preview = service.graph_preview(build_id, limit=4)
    assert len(preview["nodes"]) == 2
    assert preview["total_nodes"] == 3
    assert preview["truncated"] is True

    findings = service.findings(build_id=build_id, severity="warning")
    assert [row["id"] for row in findings["findings"]] == ["f1"]


def _write_extra_build(
    path: Path,
    build_id: str,
    *,
    started_at: str,
    status: str = "READY",
    source_count: int,
    export_rows: int,
    canonical_active: int,
    unresolved: int,
) -> None:
    """Add a second/third build to an existing catalog (schema already created).

    Each count maps directly to one of the four per-build aggregates that
    ``MonitorService._summarize_build`` computes, so list_builds correctness
    can be asserted per build.
    """
    import sqlite3 as _sqlite3

    connection = _sqlite3.connect(path)
    try:
        connection.execute(
            """
            INSERT INTO graph_builds(
                id,status,curation_version,graph_schema_version,vector_schema_version,
                settings_json,summary_json,started_at,last_error
            ) VALUES (?, ?, 'curation-v1', 1, 1, '{}', '{}', ?, NULL)
            """,
            (build_id, status, started_at),
        )
        for ordinal in range(source_count):
            connection.execute(
                """
                INSERT INTO build_source_tasks(
                    build_id, university_id, university_name, abbr, source_path, ordinal, status,
                    rows_read, observations_written, documents_seen, findings, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'COMPLETED', 0, 0, 0, 0, ?)
                """,
                (build_id, f"u-{build_id}-{ordinal}", f"U{ordinal}", f"u{ordinal}",
                 f"u-{build_id}-{ordinal}.db", ordinal, started_at),
            )
        # one export partition whose row_count == export_rows
        connection.execute(
            "INSERT INTO graph_export_partitions VALUES (?, ?, 'node', 'Build', ?, NULL, NULL, 'c', 'now')",
            (build_id, f"node:{build_id}", export_rows),
        )
        for i in range(canonical_active):
            connection.execute(
                """
                INSERT INTO canonical_professors(
                    entity_id,build_id,name,title_family,role_status,role_reason_codes,
                    master_eligibility,phd_eligibility,active,completeness
                ) VALUES (?, ?, ?, 'professor', 'included', '[]', 'unknown', 'unknown', 1, 0.5)
                """,
                (f"{build_id}-e{i}", build_id, f"{build_id}-e{i}"),
            )
        for i in range(unresolved):
            connection.execute(
                "INSERT INTO quality_findings VALUES (?, ?, 'warning', 'identity_conflict', NULL, NULL, '{}', 0)",
                (f"{build_id}-f{i}", build_id),
            )
        connection.commit()
    finally:
        connection.close()


def test_list_builds_aggregates_correctly_across_multiple_builds(tmp_path: Path) -> None:
    """P3-9: list_builds must return correct per-build aggregates
    (source_count, graph_export_rows, canonical_active, unresolved_findings)
    for every build, computed in a single aggregated SQL round-trip rather
    than 1 + N*4 queries."""
    catalog = tmp_path / "catalog.db"
    # build-1 (from _write_catalog): 2 sources, 5 export rows (1+1+1+1+1),
    # 2 canonical active, 1 unresolved warning.
    _write_catalog(catalog)
    # build-2: 3 sources, 7 export rows, 4 canonical active, 2 unresolved.
    _write_extra_build(catalog, "build-2", started_at="2026-06-30T00:00:00+00:00",
                      source_count=3, export_rows=7, canonical_active=4, unresolved=2)
    # build-3: 1 source, 0 export rows, 0 canonical active, 0 unresolved.
    _write_extra_build(catalog, "build-3", started_at="2026-06-30T01:00:00+00:00",
                      source_count=1, export_rows=0, canonical_active=0, unresolved=0)
    service = MonitorService(_settings(catalog))

    builds = service.list_builds(limit=100)["builds"]
    by_id = {b["id"]: b for b in builds}
    # ordered by started_at DESC: build-3, build-2, build-1
    assert [b["id"] for b in builds] == ["build-3", "build-2", "build-1"]

    assert by_id["build-1"]["summary"]["source_count"] == 2
    assert by_id["build-1"]["summary"]["graph_export_rows"] == 5
    assert by_id["build-1"]["summary"]["canonical_active"] == 2
    assert by_id["build-1"]["summary"]["unresolved_findings"] == 1

    assert by_id["build-2"]["summary"]["source_count"] == 3
    assert by_id["build-2"]["summary"]["graph_export_rows"] == 7
    assert by_id["build-2"]["summary"]["canonical_active"] == 4
    assert by_id["build-2"]["summary"]["unresolved_findings"] == 2

    assert by_id["build-3"]["summary"]["source_count"] == 1
    assert by_id["build-3"]["summary"]["graph_export_rows"] == 0
    assert by_id["build-3"]["summary"]["canonical_active"] == 0
    assert by_id["build-3"]["summary"]["unresolved_findings"] == 0


def test_list_builds_uses_constant_query_count_not_n_plus_1(tmp_path: Path) -> None:
    """P3-9: list_builds must issue the per-build aggregates in ONE query, not
    1 + N*4. We count SELECT statements against the four aggregate tables via
    a sqlite trace callback; the count must not grow with the number of builds."""
    import sqlite3 as _sqlite3
    from dext_monitor import catalog_reader as cr_mod

    catalog = tmp_path / "catalog.db"
    _write_catalog(catalog)
    _write_extra_build(catalog, "build-2", started_at="2026-06-30T00:00:00+00:00",
                      source_count=2, export_rows=3, canonical_active=2, unresolved=1)
    _write_extra_build(catalog, "build-3", started_at="2026-06-30T01:00:00+00:00",
                      source_count=1, export_rows=1, canonical_active=1, unresolved=0)

    service = MonitorService(_settings(catalog))
    agg_signatures = (
        "FROM BUILD_SOURCE_TASKS",
        "FROM GRAPH_EXPORT_PARTITIONS",
        "FROM CANONICAL_PROFESSORS",
        "FROM QUALITY_FINDINGS",
    )
    counts: list[int] = []

    real_connect = cr_mod.CatalogReader.connect

    from contextlib import contextmanager

    @contextmanager
    def counting_connect(self_reader):
        # open the real read-only connection, then attach a trace callback
        with real_connect(self_reader) as connection:
            seen: list[str] = []

            def trace(stmt: str) -> None:
                upper = stmt.upper().lstrip()
                if upper.startswith("SELECT") and any(s in upper for s in agg_signatures):
                    seen.append(stmt)

            connection.set_trace_callback(trace)
            yield connection
            counts.append(len(seen))

    cr_mod.CatalogReader.connect = counting_connect  # type: ignore[assignment]
    try:
        builds = service.list_builds(limit=100)["builds"]
    finally:
        cr_mod.CatalogReader.connect = real_connect  # type: ignore[assignment]

    assert len(builds) == 3
    # A single aggregated SELECT (subqueries/LEFT JOIN over graph_builds) is
    # ONE trace event touching all four aggregate tables in its text. The
    # previous N+1 impl issued 1 + 3*4 = 13 such SELECTs; the collapsed impl
    # must issue exactly 1. Tolerate <= 4 in case the impl uses a few separate
    # aggregate statements, but it must NOT scale with build count.
    assert counts and counts[0] <= 4, f"expected <=4 aggregate SELECTs, got {counts}"
    assert counts[0] == 1, f"expected a single aggregated SELECT, got {counts[0]}"


def test_graph_preview_filters_relationships_by_node_set_in_sql(tmp_path: Path) -> None:
    """P3-11: graph_preview must push the relationship endpoint filter into SQL
    (WHERE start_graph_key IN (...) AND end_graph_key IN (...)) rather than
    over-fetching rel_limit*4 rows and filtering in Python.

    The fixture has 3 nodes (build-1, u, org) and 2 relationships:
      - PART_OF  org -> u      (both endpoints in the full node set)
      - FROM_UNIVERSITY x -> u  (start 'x' is NOT a node → out-of-set)

    With a limit large enough to load all 3 nodes, only PART_OF survives the
    filter; FROM_UNIVERSITY is dropped because its start endpoint is not a
    known node. total_relationships still counts both.
    """
    catalog = tmp_path / "catalog.db"
    build_id = _write_catalog(catalog)
    assert build_id is not None
    service = MonitorService(_settings(catalog))

    # limit=10 -> node_limit=5 (all 3 nodes), rel_limit=5.
    preview = service.graph_preview(build_id, limit=10)
    node_ids = {n["id"] for n in preview["nodes"]}
    assert node_ids == {"build-1", "u", "org"}
    # only PART_OF (org->u) has both endpoints in the node set.
    assert len(preview["links"]) == 1
    assert preview["links"][0]["source"] == "org"
    assert preview["links"][0]["target"] == "u"
    assert preview["links"][0]["label"] == "PART_OF"
    # total_relationships counts ALL relationship rows (not just in-set).
    assert preview["total_nodes"] == 3
    assert preview["total_relationships"] == 2
    # truncated reflects totals vs shown: total_relationships (2) > links (1),
    # so truncated is True even though the drop was a filter, not a limit.
    assert preview["truncated"] is True
    # response shape unchanged
    assert set(preview.keys()) == {
        "build_id", "limit", "nodes", "links",
        "total_nodes", "total_relationships", "truncated",
    }


def test_graph_preview_respects_rel_limit_after_sql_filter(tmp_path: Path) -> None:
    """When more in-set relationships exist than rel_limit, the SQL LIMIT
    truncates the links list (equivalent to the old Python break)."""
    import sqlite3 as _sqlite3
    catalog = tmp_path / "catalog.db"
    _write_catalog(catalog)
    # add 3 extra in-set relationships org->u so rel_limit=2 truncates.
    conn = _sqlite3.connect(catalog)
    try:
        for i in range(3):
            # columns: build_id, partition_key, row_key, row_kind, label_or_type,
            #          start_graph_key, end_graph_key, payload_json, provenance_ref, row_checksum
            conn.execute(
                "INSERT INTO graph_export_rows VALUES (?, ?, ?, ?, ?, ?, ?, '{}', 'p', 'c')",
                ("build-1", f"rel:EXTRA{i}", f"rel:EXTRA{i}", "relationship", "EXTRA", "org", "u"),
            )
        conn.commit()
    finally:
        conn.close()
    service = MonitorService(_settings(catalog))
    # limit=6 -> node_limit=3 (all nodes), rel_limit=3.
    preview = service.graph_preview("build-1", limit=6)
    assert len(preview["nodes"]) == 3
    # PART_OF + 3 EXTRA = 4 in-set relationships, rel_limit=3 -> 3 links.
    assert len(preview["links"]) == 3
    assert preview["total_relationships"] == 5  # 2 original + 3 extra
    assert preview["truncated"] is True


def test_graph_tree_returns_complete_university_orgunit_tree(tmp_path: Path) -> None:
    """graph_tree returns the full University→OrgUnit tree with professor
    counts — no truncation. Every PART_OF link endpoint is present in the node
    set, so the frontend can render connected edges (the bug the topology view
    fixes: graph_preview's truncated sample dropped endpoints)."""
    catalog = tmp_path / "catalog.db"
    build_id = _write_tree_catalog(catalog)
    assert build_id is not None
    service = MonitorService(_settings(catalog))

    tree = service.graph_tree(build_id)
    assert set(tree.keys()) == {"build_id", "universities", "nodes", "links"}
    assert tree["build_id"] == build_id

    # 2 universities + 2 orgunits -> 4 nodes total (Build node excluded:
    # it is the graph-build root, not part of the University→学院 structure,
    # and would render as an isolated point with no PART_OF edges).
    assert len(tree["nodes"]) == 4
    universities = [n for n in tree["nodes"] if n["category"] == "University"]
    orgunits = [n for n in tree["nodes"] if n["category"] == "OrgUnit"]
    assert len(universities) == 2
    assert len(orgunits) == 2

    # University summary list mirrors the node set.
    by_key = {u["graph_key"]: u for u in tree["universities"]}
    assert by_key["u"]["name"] == "大学A"
    assert by_key["u"]["logical_id"] == "univ:a"
    assert by_key["u"]["orgunit_count"] == 1
    assert by_key["u"]["professor_count"] == 2  # 2 AFFILIATED_WITH -> org
    assert by_key["u2"]["name"] == "大学B"
    assert by_key["u2"]["orgunit_count"] == 1
    assert by_key["u2"]["professor_count"] == 1  # 1 AFFILIATED_WITH -> org2

    # OrgUnit nodes carry kind + parent university + professor_count.
    org_node = next(n for n in orgunits if n["label"] == "学院A1")
    assert org_node["kind"] == "college"
    assert org_node["university"] == "u"
    assert org_node["professor_count"] == 2
    org2_node = next(n for n in orgunits if n["label"] == "研究所B1")
    assert org2_node["kind"] == "institute"
    assert org2_node["university"] == "u2"
    assert org2_node["professor_count"] == 1

    # Every PART_OF link endpoint is a node id in the set (no drops).
    node_ids = {n["id"] for n in tree["nodes"]}
    assert len(tree["links"]) == 2
    for link in tree["links"]:
        assert link["label"] == "PART_OF"
        assert link["source"] in node_ids
        assert link["target"] in node_ids
    targets = {link["target"] for link in tree["links"]}
    assert targets == {"u", "u2"}


def _mark_export_pruned(path: Path, build_id: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM graph_export_rows WHERE build_id=?", (build_id,))
        connection.execute("DELETE FROM graph_export_partitions WHERE build_id=?", (build_id,))
        connection.execute(
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink IN ('graph_export','neo4j')",
            (build_id,),
        )
        connection.execute(
            """
            INSERT INTO graph_runs(
              id,build_id,status,evidence_version,export_version,summary_json,started_at,finished_at,last_error
            ) VALUES (
              'graph-pruned', ?, 'COMPLETED', 'evidence-v1', 'export-v1',
              '{"export_pruned":true,"partitions":{}}', 'now', NULL, NULL
            )
            ON CONFLICT(id) DO UPDATE SET summary_json=excluded.summary_json
            """,
            (build_id,),
        )
        connection.commit()
    finally:
        connection.close()


def test_pruned_graph_preview_and_tree_return_empty_graph(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_tree_catalog(catalog)
    _mark_export_pruned(catalog, build_id)
    service = MonitorService(_settings(catalog))

    preview = service.graph_preview(build_id, limit=10)
    tree = service.graph_tree(build_id)

    assert preview == {
        "build_id": build_id,
        "limit": 10,
        "nodes": [],
        "links": [],
        "total_nodes": 0,
        "total_relationships": 0,
        "truncated": False,
        "export_pruned": True,
    }
    assert tree == {
        "build_id": build_id,
        "universities": [],
        "nodes": [],
        "links": [],
        "export_pruned": True,
    }


def test_pruned_build_summary_and_metrics_report_zero_export_rows(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_tree_catalog(catalog)
    _mark_export_pruned(catalog, build_id)
    service = MonitorService(_settings(catalog))

    listing = service.list_builds()
    detail = service.build_detail(build_id)
    metrics = service.metrics(build_id)

    assert listing["builds"][0]["summary"]["graph_export_rows"] == 0
    assert detail["build"]["summary"]["graph_export_rows"] == 0
    assert detail["export_partitions"] == []
    assert metrics["export_partitions"] == []


def _write_professor_catalog(path: Path) -> str:
    """Like ``_write_tree_catalog`` but also seeds ``node:Professor`` rows so
    ``orgunit_professors`` has real professor payloads to return. Professors
    p1, p2 are affiliated with org (college under 大学A); p3 with org2."""
    build_id = _write_tree_catalog(path)
    connection = sqlite3.connect(path)
    try:
        prof_rows = [
            ("node:Professor", "row-p1", "node", "Professor", None, None,
             '{"graph_key":"p1","name":"张三","title":"教授","title_family":"教授","role_status":"active"}'),
            ("node:Professor", "row-p2", "node", "Professor", None, None,
             '{"graph_key":"p2","name":"李四","title":"副教授","title_family":"副教授","role_status":"active"}'),
            ("node:Professor", "row-p3", "node", "Professor", None, None,
             '{"graph_key":"p3","name":"王五","title":"讲师","title_family":"讲师","role_status":"active"}'),
        ]
        for row in prof_rows:
            connection.execute(
                "INSERT INTO graph_export_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'p', 'c')",
                (build_id, *row),
            )
        topic_rows = [
            ("node:ResearchStatement", "row-s1", "node", "ResearchStatement", None, None,
             '{"graph_key":"s1","raw_text":"机器学习方向","language":"zh"}'),
            ("node:ResearchStatement", "row-s2", "node", "ResearchStatement", None, None,
             '{"graph_key":"s2","raw_text":"机器人感知","language":"zh"}'),
            ("node:ResearchStatement", "row-s3", "node", "ResearchStatement", None, None,
             '{"graph_key":"s3","raw_text":"暂无主题","language":"zh"}'),
            ("node:Topic", "row-t1", "node", "Topic", None, None,
             '{"graph_key":"t1","logical_id":"topic-1","canonical_name":"机器学习","normalized_name":"机器学习","kind":"method","status":"active","taxonomy_version":"tax-v1"}'),
            ("node:Topic", "row-t2", "node", "Topic", None, None,
             '{"graph_key":"t2","logical_id":"topic-2","canonical_name":"机器人","normalized_name":"机器人","kind":"application_domain","status":"active","taxonomy_version":"tax-v1"}'),
            ("rel:HAS_RESEARCH_STATEMENT", "p1|s1", "relationship", "HAS_RESEARCH_STATEMENT", "p1", "s1",
             '{"confidence":1.0,"evidence_count":1}'),
            ("rel:HAS_RESEARCH_STATEMENT", "p1|s2", "relationship", "HAS_RESEARCH_STATEMENT", "p1", "s2",
             '{"confidence":1.0,"evidence_count":1}'),
            ("rel:HAS_RESEARCH_STATEMENT", "p2|s3", "relationship", "HAS_RESEARCH_STATEMENT", "p2", "s3",
             '{"confidence":1.0,"evidence_count":1}'),
            ("rel:PRIMARY_TOPIC", "s1|t1", "relationship", "PRIMARY_TOPIC", "s1", "t1",
             '{"confidence":0.9,"evidence_count":1}'),
            ("rel:PRIMARY_TOPIC", "s2|t1", "relationship", "PRIMARY_TOPIC", "s2", "t1",
             '{"confidence":0.7,"evidence_count":2}'),
            ("rel:USES_METHOD", "s2|t2", "relationship", "USES_METHOD", "s2", "t2",
             '{"confidence":0.8,"evidence_count":1}'),
        ]
        for row in topic_rows:
            connection.execute(
                "INSERT INTO graph_export_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'p', 'c')",
                (build_id, *row),
            )
        connection.execute(
            "INSERT INTO graph_export_partitions VALUES (?, ?, 'node', 'Professor', ?, NULL, NULL, 'checksum', 'now')",
            (build_id, "node:Professor", 3),
        )
        for partition, kind, label, count in [
            ("node:ResearchStatement", "node", "ResearchStatement", 3),
            ("node:Topic", "node", "Topic", 2),
            ("rel:HAS_RESEARCH_STATEMENT", "relationship", "HAS_RESEARCH_STATEMENT", 3),
            ("rel:PRIMARY_TOPIC", "relationship", "PRIMARY_TOPIC", 2),
            ("rel:USES_METHOD", "relationship", "USES_METHOD", 1),
        ]:
            connection.execute(
                "INSERT INTO graph_export_partitions VALUES (?, ?, ?, ?, ?, NULL, NULL, 'checksum', 'now')",
                (build_id, partition, kind, label, count),
            )
        connection.commit()
        return build_id
    finally:
        connection.close()


def test_orgunit_professors_returns_professors_and_edges(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.orgunit_professors(build_id, "org")
    assert set(result.keys()) == {"build_id", "orgunit", "professors", "links"}
    assert result["build_id"] == build_id
    assert result["orgunit"]["graph_key"] == "org"
    assert result["orgunit"]["label"] == "学院A1"
    assert result["orgunit"]["kind"] == "college"

    profs = {p["graph_key"]: p for p in result["professors"]}
    assert set(profs) == {"p1", "p2"}  # p3 is affiliated with org2, excluded
    assert profs["p1"]["name"] == "张三"
    assert profs["p1"]["title"] == "教授"

    assert len(result["links"]) == 2
    for link in result["links"]:
        assert link["label"] == "AFFILIATED_WITH"
        assert link["target"] == "org"
        assert link["source"] in profs  # endpoint resolves to a returned professor


def test_orgunit_professors_unknown_org_returns_empty(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.orgunit_professors(build_id, "no-such-org")
    assert result["professors"] == []
    assert result["links"] == []
    assert result["orgunit"]["graph_key"] == "no-such-org"


def test_orgunit_professors_cross_org_professor_excluded(tmp_path: Path) -> None:
    """A professor affiliated with org1 must NOT appear under org2."""
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.orgunit_professors(build_id, "org2")
    profs = {p["graph_key"] for p in result["professors"]}
    assert profs == {"p3"}  # only p3 is affiliated with org2


def test_professor_topics_returns_professor_topic_subgraph(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.professor_topics(build_id, "p1")
    assert set(result.keys()) == {"build_id", "professor", "topics", "links"}
    assert result["build_id"] == build_id
    assert result["professor"]["graph_key"] == "p1"
    assert result["professor"]["name"] == "张三"

    topics = {topic["graph_key"]: topic for topic in result["topics"]}
    assert set(topics) == {"t1", "t2"}
    assert topics["t1"]["canonical_name"] == "机器学习"
    assert topics["t1"]["kind"] == "method"
    assert topics["t2"]["canonical_name"] == "机器人"

    links = {(link["target"], link["label"]): link for link in result["links"]}
    assert set(links) == {("t1", "PRIMARY_TOPIC"), ("t2", "USES_METHOD")}
    primary = links[("t1", "PRIMARY_TOPIC")]
    assert primary["source"] == "p1"
    assert primary["evidence_count"] == 3
    assert primary["confidence"] == pytest.approx(0.7)
    method = links[("t2", "USES_METHOD")]
    assert method["source"] == "p1"
    assert method["evidence_count"] == 1
    assert method["confidence"] == pytest.approx(0.8)


def test_professor_topics_professor_without_topics_returns_empty(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))

    result = service.professor_topics(build_id, "p2")
    assert result["professor"]["graph_key"] == "p2"
    assert result["topics"] == []
    assert result["links"] == []


def test_orgunit_professors_unknown_build_raises(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    _write_professor_catalog(catalog)
    service = MonitorService(_settings(catalog))
    with pytest.raises(MonitorCatalogError, match="unknown build ID"):
        service.orgunit_professors("does-not-exist", "org")


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

        resp = await client.get(f"/api/monitor/builds/{build_id}/graph-tree")
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["build_id"] == build_id
        # _write_catalog: 1 University + 1 OrgUnit -> 2 nodes; 1 PART_OF link.
        assert len(body["data"]["nodes"]) == 2
        assert len(body["data"]["links"]) == 1
        assert len(body["data"]["universities"]) == 1
        # ETag present (read-only endpoint, cacheable like /builds).
        etag = resp.headers.get("ETag")
        assert etag and etag.startswith("W/")

        resp = await client.get("/api/monitor/builds/nope")
        assert resp.status == 404
        body = await resp.json()
        assert body["error"]["type"] == "MonitorCatalogError"

        # Unknown /api/monitor paths must return a JSON error envelope, not an
        # HTML 404, so the frontend's response parser never breaks.
        resp = await client.get("/api/monitor/builds/build-1/no-such-subresource")
        assert resp.status == 404
        assert resp.content_type == "application/json"
        body = await resp.json()
        assert body["error"]["type"] == "MonitorNotFoundError"

        # P1-5: read-only endpoints emit an ETag and honor If-None-Match (304).
        # Use /builds (stable payload) — /health carries server_time and so is
        # intentionally never cacheable across calls.
        resp = await client.get("/api/monitor/builds")
        etag = resp.headers.get("ETag")
        assert etag and etag.startswith("W/")
        assert resp.headers.get("Cache-Control") == "no-cache"

        # Second request with the same ETag returns 304 with no body.
        resp2 = await client.get(
            "/api/monitor/builds", headers={"If-None-Match": etag}
        )
        assert resp2.status == 304
        assert await resp2.text() == ""


@pytest.mark.asyncio
async def test_monitor_orgunit_professors_endpoint(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    app = create_app(_settings(catalog))
    async with TestClient(TestServer(app)) as client:
        url = f"/api/monitor/builds/{build_id}/orgunit-professors?org_graph_key=org"
        resp = await client.get(url)
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["orgunit"]["graph_key"] == "org"
        assert {p["graph_key"] for p in body["data"]["professors"]} == {"p1", "p2"}

        etag = resp.headers["ETag"]
        resp2 = await client.get(url, headers={"If-None-Match": etag})
        assert resp2.status == 304

        legacy_url = f"/api/monitor/builds/{build_id}/orgunit/org/professors"
        legacy_resp = await client.get(legacy_url)
        assert legacy_resp.status == 200
        legacy_body = await legacy_resp.json()
        assert legacy_body["data"]["orgunit"]["graph_key"] == "org"
        assert {p["graph_key"] for p in legacy_body["data"]["professors"]} == {"p1", "p2"}

        missing_resp = await client.get(f"/api/monitor/builds/{build_id}/orgunit-professors")
        assert missing_resp.status == 400
        missing_body = await missing_resp.json()
        assert missing_body["error"]["type"] == "ValueError"
        assert "org_graph_key" in missing_body["error"]["message"]


@pytest.mark.asyncio
async def test_monitor_professor_topics_endpoint(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    build_id = _write_professor_catalog(catalog)
    app = create_app(_settings(catalog))
    async with TestClient(TestServer(app)) as client:
        url = f"/api/monitor/builds/{build_id}/professor-topics?professor_graph_key=p1"
        resp = await client.get(url)
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["professor"]["graph_key"] == "p1"
        assert {topic["graph_key"] for topic in body["data"]["topics"]} == {"t1", "t2"}
        assert {
            (link["target"], link["label"]) for link in body["data"]["links"]
        } == {("t1", "PRIMARY_TOPIC"), ("t2", "USES_METHOD")}

        etag = resp.headers["ETag"]
        resp2 = await client.get(url, headers={"If-None-Match": etag})
        assert resp2.status == 304

        missing_resp = await client.get(f"/api/monitor/builds/{build_id}/professor-topics")
        assert missing_resp.status == 400
        missing_body = await missing_resp.json()
        assert missing_body["error"]["type"] == "ValueError"
        assert "professor_graph_key" in missing_body["error"]["message"]


@pytest.mark.asyncio
async def test_monitor_static_dir_resolves_relative_to_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative monitor_static_dir (default 'webui/dist') must resolve against
    the repository root, not the process CWD — otherwise `dext monitor serve`
    run from outside the repo can't find the built UI."""
    catalog = tmp_path / "catalog.db"
    _write_catalog(catalog)
    repo_root = Path(__file__).resolve()
    while repo_root != repo_root.parent and not (repo_root / "pyproject.toml").exists():
        repo_root = repo_root.parent
    dist = repo_root / "webui" / "dist"
    created = False
    if not (dist / "index.html").is_file():
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<!doctype html><title>x</title>", encoding="utf-8")
        (dist / "assets").mkdir(exist_ok=True)
        (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
        created = True
    try:
        # Run from a CWD that has no 'webui/dist' — resolution must still find the
        # repo-rooted build via the relative default.
        monkeypatch.chdir(tmp_path)
        settings = MonitorSettings(catalog_path=catalog)  # default static_dir = webui/dist
        app = create_app(settings)
        named = app.router.named_resources()
        assert "assets" in named, "static /assets route should register when dist exists"
    finally:
        if created:
            import shutil
            shutil.rmtree(dist, ignore_errors=True)


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
