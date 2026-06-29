"""Stage-3 evidence/export/Neo4j orchestration and recovery."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    backup_existing_catalog,
    catalog_write_lock,
    initialize_catalog,
    json_dumps,
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.evidence import (
    EVIDENCE_VERSION,
    EXPORT_VERSION,
    freeze_graph_exports,
    materialize_evidence,
)
from dext_graph.catalog.ids import hash_parts
from dext_graph.catalog.neo4j_sink import write_neo4j_exports
from dext_graph.config import GraphSettings


def _safe_error(exc: BaseException) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (value or type(exc).__name__)[:2000]


def _prepare_run(connection: sqlite3.Connection, build_id: str) -> str:
    build = connection.execute(
        "SELECT status FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    if build["status"] not in {"EMBEDDING", "WRITING_GRAPH", "FAILED"}:
        raise CatalogError(
            f"build {build_id} cannot enter graph stage from {build['status']}"
        )
    run_id = hash_parts(build_id, EVIDENCE_VERSION, EXPORT_VERSION)
    existing = connection.execute(
        "SELECT * FROM graph_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO graph_runs(
              id, build_id, status, evidence_version, export_version,
              summary_json, started_at
            ) VALUES (?, ?, 'RUNNING', ?, ?, '{}', ?)
            """,
            (run_id, build_id, EVIDENCE_VERSION, EXPORT_VERSION, utcnow_iso()),
        )
    else:
        if (
            existing["evidence_version"] != EVIDENCE_VERSION
            or existing["export_version"] != EXPORT_VERSION
        ):
            raise CatalogError("graph evidence/export version changed; create a new build")
        run_id = str(existing["id"])
        if existing["status"] != "COMPLETED":
            connection.execute(
                "UPDATE graph_runs SET status='RUNNING', finished_at=NULL, last_error=NULL WHERE id=?",
                (run_id,),
            )
    return run_id


def _start_graph_write(connection: sqlite3.Connection, build_id: str) -> None:
    connection.execute(
        "UPDATE graph_builds SET status='WRITING_GRAPH', last_error=NULL WHERE id=?",
        (build_id,),
    )


def _finish(
    connection: sqlite3.Connection, build_id: str, run_id: str
) -> None:
    evidence = {
        "research_statements": int(
            connection.execute(
                "SELECT COUNT(*) FROM research_statements WHERE build_id=?", (build_id,)
            ).fetchone()[0]
        ),
        "publication_mention_evidence": int(
            connection.execute(
                "SELECT COUNT(*) FROM publication_mentions WHERE build_id=?", (build_id,)
            ).fetchone()[0]
        ),
        "publication_mentions": int(
            connection.execute(
                "SELECT COUNT(DISTINCT id) FROM publication_mentions WHERE build_id=?",
                (build_id,),
            ).fetchone()[0]
        ),
    }
    partitions = {
        row["partition_key"]: {
            "row_count": int(row["row_count"]),
            "checksum": row["checksum"],
            "min_key": row["min_key"],
            "max_key": row["max_key"],
        }
        for row in connection.execute(
            "SELECT * FROM graph_export_partitions WHERE build_id=? ORDER BY partition_key",
            (build_id,),
        )
    }
    summary = {
        "evidence_version": EVIDENCE_VERSION,
        "export_version": EXPORT_VERSION,
        "evidence": evidence,
        "partitions": partitions,
        "graph_gold_status": "not_evaluated",
    }
    now = utcnow_iso()
    connection.execute(
        "UPDATE graph_runs SET status='COMPLETED', summary_json=?, finished_at=?, last_error=NULL WHERE id=?",
        (json_dumps(summary), now, run_id),
    )
    build_summary = json_loads(
        connection.execute(
            "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()[0],
        {},
    )
    build_summary["graph"] = summary
    connection.execute(
        "UPDATE graph_builds SET status='WRITING_VECTOR', summary_json=?, last_error=NULL WHERE id=?",
        (json_dumps(build_summary), build_id),
    )


def _fail(
    connection: sqlite3.Connection, build_id: str, run_id: str, error: str
) -> None:
    now = utcnow_iso()
    connection.execute(
        "UPDATE graph_runs SET status='FAILED', last_error=?, finished_at=? WHERE id=?",
        (error, now, run_id),
    )
    connection.execute(
        "UPDATE graph_builds SET status='FAILED', last_error=? WHERE id=?",
        (error, build_id),
    )


async def run_graph_stage(
    writer: CatalogWriter, build_id: str, settings: GraphSettings
) -> dict[str, Any]:
    run_id = await writer.execute(lambda connection: _prepare_run(connection, build_id))
    run = await writer.execute(
        lambda connection: dict(
            connection.execute("SELECT * FROM graph_runs WHERE id=?", (run_id,)).fetchone()
        ),
        transactional=False,
    )
    if run["status"] == "COMPLETED":
        from dext_graph.catalog.workflow import build_status

        return build_status(writer.path, build_id)
    try:
        await materialize_evidence(writer, build_id, settings)
        await freeze_graph_exports(writer, build_id, settings)
        await writer.execute(lambda connection: _start_graph_write(connection, build_id))
        if os.getenv("DEXT_TEST_SKIP_NEO4J") != "1":
            await write_neo4j_exports(writer, build_id, settings)
        await writer.execute(lambda connection: _finish(connection, build_id, run_id))
    except Exception as exc:  # noqa: BLE001 - persist resumable graph failure
        await writer.execute(
            lambda connection: _fail(connection, build_id, run_id, _safe_error(exc))
        )
    from dext_graph.catalog.workflow import build_status

    return build_status(writer.path, build_id)


async def graph_build(
    build_id: str, settings: GraphSettings | None = None
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            return await run_graph_stage(writer, build_id, settings)


__all__ = ["graph_build", "run_graph_stage"]
