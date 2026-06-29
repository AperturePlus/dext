"""Read-only monitor service aggregations."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from dext_monitor.catalog_reader import (
    CatalogReader,
    MonitorCatalogError,
    json_loads,
    serialize_row,
)
from dext_monitor.settings import MonitorSettings

ACTIVE_STATUSES = {
    "CREATED",
    "SNAPSHOTTING",
    "INGESTING",
    "CURATING",
    "EMBEDDING",
    "WRITING_GRAPH",
    "WRITING_VECTOR",
    "VALIDATING",
}

STAGE_ORDER = (
    "CREATED",
    "SNAPSHOTTING",
    "INGESTING",
    "CURATING",
    "EMBEDDING",
    "WRITING_GRAPH",
    "WRITING_VECTOR",
    "VALIDATING",
    "READY",
    "ACTIVE",
)


class MonitorService:
    """Application service for monitor DTOs."""

    def __init__(self, settings: MonitorSettings):
        self.settings = settings
        self.reader = CatalogReader(settings.catalog_path)

    def health(self) -> dict[str, Any]:
        path = Path(self.settings.catalog_path).expanduser().resolve()
        payload: dict[str, Any] = {
            "catalog_path": str(path),
            "server_time": time.time(),
            "readable": False,
            "schema_version": None,
        }
        try:
            with self.reader.connect() as connection:
                payload["schema_version"] = self.reader.require_supported_schema(connection)
                payload["readable"] = True
                payload["build_count"] = int(
                    connection.execute("SELECT COUNT(*) FROM graph_builds").fetchone()[0]
                )
        except MonitorCatalogError as exc:
            payload["error"] = str(exc)
        return payload

    def list_builds(self, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        with self.reader.connect() as connection:
            schema_version = self.reader.require_supported_schema(connection)
            # P3-9: one aggregated query — graph_builds row + the four
            # per-build aggregates as correlated subqueries — instead of the
            # previous 1 + N*4 round-trips.
            rows = connection.execute(
                """
                SELECT b.*,
                       (SELECT COUNT(*) FROM build_source_tasks WHERE build_id=b.id) AS _source_count,
                       (SELECT COALESCE(SUM(row_count), 0) FROM graph_export_partitions WHERE build_id=b.id) AS _export_rows,
                       (SELECT COUNT(*) FROM canonical_professors WHERE build_id=b.id AND active=1) AS _canonical_active,
                       (SELECT COUNT(*) FROM quality_findings WHERE build_id=b.id AND resolved=0) AS _unresolved
                FROM graph_builds b
                ORDER BY b.started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            builds = [self._summarize_build_row(serialize_row(row)) for row in rows]
            return {
                "catalog_path": str(Path(self.settings.catalog_path).expanduser().resolve()),
                "schema_version": schema_version,
                "builds": builds,
                "latest_build_id": builds[0]["id"] if builds else None,
            }

    def build_detail(self, build_id: str) -> dict[str, Any]:
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            build = self._get_build(connection, build_id)
            sources = [
                serialize_row(row)
                for row in connection.execute(
                    "SELECT * FROM build_source_tasks WHERE build_id=? ORDER BY ordinal",
                    (build_id,),
                )
            ]
            checkpoints = [
                serialize_row(row)
                for row in connection.execute(
                    "SELECT * FROM sink_checkpoints WHERE build_id=? ORDER BY sink, partition_key",
                    (build_id,),
                )
            ]
            export_partitions = [
                serialize_row(row)
                for row in connection.execute(
                    "SELECT * FROM graph_export_partitions WHERE build_id=? ORDER BY partition_key",
                    (build_id,),
                )
            ]
            unresolved_findings = {
                row["severity"]: int(row["count"])
                for row in connection.execute(
                    "SELECT severity, COUNT(*) AS count FROM quality_findings "
                    "WHERE build_id=? AND resolved=0 GROUP BY severity",
                    (build_id,),
                )
            }
            curation = None
            if self.reader.table_exists(connection, "curation_runs"):
                row = connection.execute(
                    "SELECT * FROM curation_runs WHERE build_id=?",
                    (build_id,),
                ).fetchone()
                curation = serialize_row(row) if row is not None else None
            graph = None
            if self.reader.table_exists(connection, "graph_runs"):
                row = connection.execute(
                    "SELECT * FROM graph_runs WHERE build_id=?",
                    (build_id,),
                ).fetchone()
                graph = serialize_row(row) if row is not None else None
            return {
                "catalog_path": str(Path(self.settings.catalog_path).expanduser().resolve()),
                "build": self._summarize_build(connection, build),
                "sources": sources,
                "checkpoints": checkpoints,
                "export_partitions": export_partitions,
                "unresolved_findings": unresolved_findings,
                "curation": curation,
                "graph": graph,
            }

    def metrics(self, build_id: str) -> dict[str, Any]:
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            build = self._get_build(connection, build_id)
            source_status_counts = dict(
                Counter(
                    str(row["status"])
                    for row in connection.execute(
                        "SELECT status FROM build_source_tasks WHERE build_id=?",
                        (build_id,),
                    )
                )
            )
            finding_counts = {
                row["severity"]: int(row["count"])
                for row in connection.execute(
                    "SELECT severity, COUNT(*) AS count FROM quality_findings "
                    "WHERE build_id=? AND resolved=0 GROUP BY severity",
                    (build_id,),
                )
            }
            export_partitions = [
                {
                    "partition_key": row["partition_key"],
                    "label_or_type": row["label_or_type"],
                    "row_kind": row["row_kind"],
                    "row_count": int(row["row_count"]),
                }
                for row in connection.execute(
                    "SELECT partition_key, row_kind, label_or_type, row_count "
                    "FROM graph_export_partitions WHERE build_id=? ORDER BY partition_key",
                    (build_id,),
                )
            ]
            observations_by_source = [
                {
                    "university_id": row["university_id"],
                    "university_name": row["university_name"],
                    "abbr": row["abbr"],
                    "status": row["status"],
                    "rows_read": int(row["rows_read"]),
                    "observations_written": int(row["observations_written"]),
                    "documents_seen": int(row["documents_seen"]),
                    "findings": int(row["findings"]),
                }
                for row in connection.execute(
                    "SELECT university_id, university_name, abbr, status, rows_read, "
                    "observations_written, documents_seen, findings "
                    "FROM build_source_tasks WHERE build_id=? ORDER BY ordinal",
                    (build_id,),
                )
            ]
            role_counts = {
                row["role_status"]: int(row["count"])
                for row in connection.execute(
                    "SELECT role_status, COUNT(*) AS count FROM canonical_professors "
                    "WHERE build_id=? AND active=1 GROUP BY role_status",
                    (build_id,),
                )
            }
            title_family_counts = {
                row["title_family"]: int(row["count"])
                for row in connection.execute(
                    "SELECT title_family, COUNT(*) AS count FROM canonical_professors "
                    "WHERE build_id=? AND active=1 GROUP BY title_family",
                    (build_id,),
                )
            }
            return {
                "build_id": build_id,
                "stage": self._stage_state(str(build["status"])),
                "source_status_counts": source_status_counts,
                "finding_counts": finding_counts,
                "export_partitions": export_partitions,
                "observations_by_source": observations_by_source,
                "role_counts": role_counts,
                "title_family_counts": title_family_counts,
            }

    def graph_preview(self, build_id: str, limit: int | None = None) -> dict[str, Any]:
        effective_limit = limit or self.settings.monitor_graph_preview_limit
        effective_limit = max(1, min(int(effective_limit), 1000))
        node_limit = max(1, effective_limit // 2)
        rel_limit = effective_limit - node_limit
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            self._get_build(connection, build_id)
            node_rows = [
                serialize_row(row)
                for row in connection.execute(
                    """
                    SELECT partition_key, row_key, row_kind, label_or_type,
                           start_graph_key, end_graph_key, payload_json
                    FROM graph_export_rows
                    WHERE build_id=? AND row_kind='node'
                    ORDER BY
                      CASE label_or_type
                        WHEN 'Build' THEN 0
                        WHEN 'University' THEN 1
                        WHEN 'OrgUnit' THEN 2
                        WHEN 'Professor' THEN 3
                        ELSE 4
                      END,
                      row_key
                    LIMIT ?
                    """,
                    (build_id, node_limit),
                )
            ]
            known_keys: set[str] = set()
            nodes: list[dict[str, Any]] = []
            for row in node_rows:
                payload = json_loads(row.get("payload_json"), {})
                graph_key = str(payload.get("graph_key") or payload.get("id") or row["row_key"])
                known_keys.add(graph_key)
                nodes.append(
                    {
                        "id": graph_key,
                        "row_key": row["row_key"],
                        "label": payload.get("name") or payload.get("id") or row["row_key"],
                        "category": row["label_or_type"],
                        "partition_key": row["partition_key"],
                    }
                )
            rel_candidates = [
                serialize_row(row)
                for row in connection.execute(
                    """
                    SELECT partition_key, row_key, row_kind, label_or_type,
                           start_graph_key, end_graph_key, payload_json
                    FROM graph_export_rows
                    WHERE build_id=? AND row_kind='relationship'
                    ORDER BY partition_key, row_key
                    LIMIT ?
                    """,
                    (build_id, max(rel_limit * 4, 1)),
                )
            ]
            links: list[dict[str, Any]] = []
            if rel_limit > 0:
                for row in rel_candidates:
                    source = str(row["start_graph_key"] or "")
                    target = str(row["end_graph_key"] or "")
                    if source not in known_keys or target not in known_keys:
                        continue
                    links.append(
                        {
                            "id": f"{row['partition_key']}:{row['row_key']}",
                            "source": source,
                            "target": target,
                            "label": row["label_or_type"],
                        }
                    )
                    if len(links) >= rel_limit:
                        break
            total_nodes = int(
                connection.execute(
                    "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=? AND row_kind='node'",
                    (build_id,),
                ).fetchone()[0]
            )
            total_relationships = int(
                connection.execute(
                    "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=? AND row_kind='relationship'",
                    (build_id,),
                ).fetchone()[0]
            )
            return {
                "build_id": build_id,
                "limit": effective_limit,
                "nodes": nodes,
                "links": links,
                "total_nodes": total_nodes,
                "total_relationships": total_relationships,
                "truncated": total_nodes + total_relationships > len(nodes) + len(links),
            }

    def findings(
        self,
        *,
        build_id: str | None,
        severity: str | None,
        limit: int = 100,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        clauses = ["resolved=0"]
        params: list[Any] = []
        if build_id:
            clauses.append("build_id=?")
            params.append(build_id)
        if severity:
            clauses.append("severity=?")
            params.append(severity)
        where = " AND ".join(clauses)
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            rows = [
                serialize_row(row)
                for row in connection.execute(
                    f"""
                    SELECT id, build_id, severity, code, entity_id, observation_id,
                           details_json, resolved
                    FROM quality_findings
                    WHERE {where}
                    ORDER BY severity DESC, code, id
                    LIMIT ?
                    """,
                    (*params, limit),
                )
            ]
            return {"findings": rows, "limit": limit}

    def _get_build(self, connection, build_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM graph_builds WHERE id=?", (build_id,)).fetchone()
        if row is None:
            raise MonitorCatalogError(f"unknown build ID: {build_id}")
        return serialize_row(row)

    def _summarize_build(self, connection, build: dict[str, Any]) -> dict[str, Any]:
        """Summarize a single build with ONE aggregate query (P3-9)."""
        build_id = str(build["id"])
        row = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM build_source_tasks WHERE build_id=?) AS _source_count,
              (SELECT COALESCE(SUM(row_count), 0) FROM graph_export_partitions WHERE build_id=?) AS _export_rows,
              (SELECT COUNT(*) FROM canonical_professors WHERE build_id=? AND active=1) AS _canonical_active,
              (SELECT COUNT(*) FROM quality_findings WHERE build_id=? AND resolved=0) AS _unresolved
            """,
            (build_id, build_id, build_id, build_id),
        ).fetchone()
        merged = dict(build)
        merged["_source_count"] = row["_source_count"]
        merged["_export_rows"] = row["_export_rows"]
        merged["_canonical_active"] = row["_canonical_active"]
        merged["_unresolved"] = row["_unresolved"]
        return self._summarize_build_row(merged)

    def _summarize_build_row(self, build: dict[str, Any]) -> dict[str, Any]:
        """Build the summary dict from a row that already carries the four
        per-build aggregate columns (``_source_count``, ``_export_rows``,
        ``_canonical_active``, ``_unresolved``). Response shape is identical
        to the pre-P3-9 ``_summarize_build`` output."""
        summary = build.get("summary_json") or {}
        source_count = int(build.get("_source_count") or 0)
        export_rows = int(build.get("_export_rows") or 0)
        canonical_active = int(build.get("_canonical_active") or 0)
        unresolved = int(build.get("_unresolved") or 0)
        return {
            "id": build["id"],
            "status": build["status"],
            "source_manifest_hash": build.get("source_manifest_hash"),
            "curation_version": build.get("curation_version"),
            "taxonomy_version": build.get("taxonomy_version"),
            "graph_schema_version": build.get("graph_schema_version"),
            "vector_schema_version": build.get("vector_schema_version"),
            "embedding_provider": build.get("embedding_provider"),
            "embedding_base_url": build.get("embedding_base_url"),
            "embedding_model": build.get("embedding_model"),
            "embedding_fingerprint": build.get("embedding_fingerprint"),
            "embedding_dimension": build.get("embedding_dimension"),
            "started_at": build.get("started_at"),
            "finished_at": build.get("finished_at"),
            "last_error": build.get("last_error"),
            "summary": {
                "source_count": summary.get("source_count", source_count),
                "rows_read": summary.get("rows_read", 0),
                "observations_written": summary.get("observations_written", 0),
                "documents_seen": summary.get("documents_seen", 0),
                "canonical_active": canonical_active,
                "unresolved_findings": unresolved,
                "graph_export_rows": export_rows,
            },
            "is_active": str(build["status"]) in ACTIVE_STATUSES,
            "stage": self._stage_state(str(build["status"])),
        }

    def _stage_state(self, status: str) -> list[dict[str, Any]]:
        try:
            current_index = STAGE_ORDER.index(status)
        except ValueError:
            current_index = -1
        stages: list[dict[str, Any]] = []
        for index, name in enumerate(STAGE_ORDER):
            if status in {"FAILED", "FAILED_VALIDATION"}:
                state = "failed" if index == max(current_index, 0) else "pending"
            elif current_index < 0:
                state = "pending"
            elif index < current_index:
                state = "completed"
            elif index == current_index:
                state = "current"
            else:
                state = "pending"
            stages.append({"name": name, "state": state})
        return stages


__all__ = ["MonitorService"]
