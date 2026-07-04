"""Repair helpers for Topic links that predate stricter compatibility gates."""

from __future__ import annotations

import sqlite3
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
from dext_graph.catalog.evidence import rebuild_graph_partitions, set_graph_export_pruned
from dext_graph.config import GraphSettings

_TOPIC_RELATION_PARTITIONS = {
    "PRIMARY_TOPIC": "rel:PRIMARY_TOPIC",
    "USES_METHOD": "rel:USES_METHOD",
    "APPLIED_TO": "rel:APPLIED_TO",
    "TARGETS_TASK": "rel:TARGETS_TASK",
    "STUDIES": "rel:STUDIES",
}

_INCOMPATIBLE_LINK_PREDICATE = """
l.build_id=? AND l.review_status='approved' AND (
  t.status!='active' OR
  (l.relation_type='PRIMARY_TOPIC' AND t.kind!='discipline') OR
  (l.relation_type='USES_METHOD' AND t.kind!='method') OR
  (l.relation_type='APPLIED_TO' AND t.kind!='application_domain') OR
  (l.relation_type='TARGETS_TASK' AND t.kind!='task') OR
  (l.relation_type='STUDIES' AND t.kind!='research_object')
)
"""


def _incompatible_relation_types(
    connection: sqlite3.Connection, build_id: str
) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT l.relation_type
            FROM statement_topic_links l
            JOIN topics t ON t.taxonomy_version=l.taxonomy_version AND t.id=l.topic_id
            WHERE {_INCOMPATIBLE_LINK_PREDICATE}
            ORDER BY l.relation_type
            """,
            (build_id,),
        )
    ]


def _refresh_topic_summary(connection: sqlite3.Connection, build_id: str) -> None:
    row = connection.execute(
        "SELECT id,summary_json FROM topic_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if row is None:
        return
    summary = json_loads(row["summary_json"], {})
    summary["approved_links"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links "
            "WHERE build_id=? AND review_status='approved'",
            (build_id,),
        ).fetchone()[0]
    )
    summary["review_links"] = int(
        connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links "
            "WHERE build_id=? AND review_status='review'",
            (build_id,),
        ).fetchone()[0]
    )
    connection.execute(
        "UPDATE topic_runs SET summary_json=?,finished_at=? WHERE id=?",
        (json_dumps(summary), utcnow_iso(), row["id"]),
    )
    build_row = connection.execute(
        "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build_row is None:
        return
    build_summary = json_loads(build_row["summary_json"], {})
    build_summary["topics"] = summary
    build_summary.pop("validation", None)
    connection.execute(
        "UPDATE graph_builds SET summary_json=? WHERE id=?",
        (json_dumps(build_summary), build_id),
    )


def _downgrade_incompatible_links(
    connection: sqlite3.Connection, build_id: str
) -> tuple[int, list[str], bool]:
    build = connection.execute(
        "SELECT status FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    if str(build["status"]) == "ACTIVE":
        raise CatalogError("refusing to repair an ACTIVE build")

    relation_types = _incompatible_relation_types(connection, build_id)
    if not relation_types:
        return 0, [], False

    cursor = connection.execute(
        f"""
        UPDATE statement_topic_links
        SET review_status='review'
        WHERE rowid IN (
          SELECT l.rowid
          FROM statement_topic_links l
          JOIN topics t ON t.taxonomy_version=l.taxonomy_version AND t.id=l.topic_id
          WHERE {_INCOMPATIBLE_LINK_PREDICATE}
        )
        """,
        (build_id,),
    )
    _refresh_topic_summary(connection, build_id)
    vector_exists = (
        connection.execute(
            "SELECT 1 FROM vector_runs WHERE build_id=?", (build_id,)
        ).fetchone()
        is not None
    )
    if vector_exists:
        connection.execute(
            "UPDATE vector_runs SET status='PENDING',finished_at=NULL,last_error=NULL "
            "WHERE build_id=?",
            (build_id,),
        )
        connection.execute(
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink='qdrant' "
            "AND partition_key='professors'",
            (build_id,),
        )
    connection.execute("DELETE FROM validation_runs WHERE build_id=?", (build_id,))
    connection.execute("DELETE FROM promotion_runs WHERE build_id=?", (build_id,))
    connection.execute(
        "UPDATE graph_builds SET status='WRITING_VECTOR',last_error=NULL WHERE id=?",
        (build_id,),
    )
    return int(cursor.rowcount), relation_types, vector_exists


async def repair_incompatible_topic_links(
    build_id: str, settings: GraphSettings | None = None
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path, retention=settings.catalog_backup_retention)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            downgraded, relation_types, vector_reset = await writer.execute(
                lambda connection: _downgrade_incompatible_links(connection, build_id)
            )
            partitions = tuple(
                _TOPIC_RELATION_PARTITIONS[relation_type]
                for relation_type in relation_types
            )
            rebuilt: dict[str, int] = {}
            if partitions:
                rebuilt = await rebuild_graph_partitions(
                    writer, build_id, settings, partitions
                )
                await writer.execute(
                    lambda connection: set_graph_export_pruned(
                        connection, build_id, False
                    )
                )
            from dext_graph.catalog.workflow import build_status

            result = build_status(writer.path, build_id)
    result["topic_link_repair"] = {
        "downgraded_links": downgraded,
        "relation_types": relation_types,
        "rebuilt_partitions": list(rebuilt),
        "rebuilt_rows": rebuilt,
        "vector_reset": vector_reset,
    }
    return result


__all__ = ["repair_incompatible_topic_links"]
