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

TOPIC_RELATION_PARTITIONS = (
    "rel:PRIMARY_TOPIC",
    "rel:USES_METHOD",
    "rel:APPLIED_TO",
    "rel:TARGETS_TASK",
    "rel:STUDIES",
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
            # P3-11: build IN-list placeholders for the relationship endpoint filter.
            placeholders = ",".join("?" for _ in known_keys)
            rel_candidates = [
                serialize_row(row)
                for row in (
                    # P3-11: push the endpoint filter into SQL instead of
                    # over-fetching rel_limit*4 rows and filtering in Python.
                    # When known_keys is empty there is nothing to match.
                    connection.execute(
                        f"""
                        SELECT partition_key, row_key, row_kind, label_or_type,
                               start_graph_key, end_graph_key, payload_json
                        FROM graph_export_rows
                        WHERE build_id=? AND row_kind='relationship'
                          AND start_graph_key IN ({placeholders})
                          AND end_graph_key IN ({placeholders})
                        ORDER BY partition_key, row_key
                        LIMIT ?
                        """,
                        (build_id, *known_keys, *known_keys, rel_limit),
                    )
                    if rel_limit > 0 and known_keys
                    else []
                )
            ]
            links: list[dict[str, Any]] = []
            for row in rel_candidates:
                source = str(row["start_graph_key"] or "")
                target = str(row["end_graph_key"] or "")
                links.append(
                    {
                        "id": f"{row['partition_key']}:{row['row_key']}",
                        "source": source,
                        "target": target,
                        "label": row["label_or_type"],
                    }
                )
            # P3-11: merge the two trailing COUNT queries into one round-trip.
            totals = connection.execute(
                """
                SELECT
                  SUM(CASE WHEN row_kind='node' THEN 1 ELSE 0 END) AS total_nodes,
                  SUM(CASE WHEN row_kind='relationship' THEN 1 ELSE 0 END) AS total_relationships
                FROM graph_export_rows WHERE build_id=?
                """,
                (build_id,),
            ).fetchone()
            total_nodes = int(totals["total_nodes"] or 0)
            total_relationships = int(totals["total_relationships"] or 0)
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

    def graph_tree(self, build_id: str) -> dict[str, Any]:
        """Complete University→OrgUnit tree with professor counts (no truncation).

        Unlike ``graph_preview`` (a truncated topology sample whose node-limit
        cut drops relationship endpoints), this returns every University and
        OrgUnit node plus their ``PART_OF`` edges, so the frontend can render a
        connected per-university subgraph. Node ``id`` is ``payload.graph_key``
        — the same key ``rel:PART_OF`` rows carry in ``start_graph_key`` /
        ``end_graph_key`` — so ECharts resolves every link endpoint.
        """
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            self._get_build(connection, build_id)
            university_rows = list(
                connection.execute(
                    "SELECT payload_json FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='node:University' "
                    "ORDER BY row_key",
                    (build_id,),
                )
            )
            orgunit_rows = list(
                connection.execute(
                    "SELECT payload_json FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='node:OrgUnit' "
                    "ORDER BY row_key",
                    (build_id,),
                )
            )
            part_of_rows = list(
                connection.execute(
                    "SELECT start_graph_key, end_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:PART_OF'",
                    (build_id,),
                )
            )
            affiliated_rows = list(
                connection.execute(
                    "SELECT end_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:AFFILIATED_WITH'",
                    (build_id,),
                )
            )

        # Professor count per OrgUnit (end_graph_key is the org).
        prof_count_by_org: dict[str, int] = {}
        for row in affiliated_rows:
            prof_count_by_org[row["end_graph_key"]] = (
                prof_count_by_org.get(row["end_graph_key"], 0) + 1
            )

        # org_key -> univ_key from PART_OF edges; also build links.
        org_to_univ: dict[str, str] = {}
        links: list[dict[str, Any]] = []
        for row in part_of_rows:
            org_key = str(row["start_graph_key"])
            univ_key = str(row["end_graph_key"])
            org_to_univ[org_key] = univ_key
            links.append(
                {"source": org_key, "target": univ_key, "label": "PART_OF"}
            )

        nodes: list[dict[str, Any]] = []
        universities: list[dict[str, Any]] = []
        prof_count_by_univ: dict[str, int] = {}
        org_count_by_univ: dict[str, int] = {}
        for row in university_rows:
            payload = json_loads(row["payload_json"], {})
            graph_key = str(payload.get("graph_key") or payload.get("id") or "")
            nodes.append(
                {
                    "id": graph_key,
                    "label": str(payload.get("name") or graph_key),
                    "category": "University",
                    "professor_count": 0,
                    "orgunit_count": 0,
                }
            )
            universities.append(
                {
                    "graph_key": graph_key,
                    "name": str(payload.get("name") or graph_key),
                    "logical_id": str(payload.get("logical_id") or ""),
                    "orgunit_count": 0,
                    "professor_count": 0,
                }
            )
            prof_count_by_univ[graph_key] = 0
            org_count_by_univ[graph_key] = 0

        for row in orgunit_rows:
            payload = json_loads(row["payload_json"], {})
            graph_key = str(payload.get("graph_key") or payload.get("id") or "")
            univ_key = org_to_univ.get(graph_key, "")
            profs = prof_count_by_org.get(graph_key, 0)
            nodes.append(
                {
                    "id": graph_key,
                    "label": str(payload.get("name") or graph_key),
                    "category": "OrgUnit",
                    "kind": str(payload.get("kind") or ""),
                    "professor_count": profs,
                    "university": univ_key,
                }
            )
            if univ_key in prof_count_by_univ:
                prof_count_by_univ[univ_key] += profs
                org_count_by_univ[univ_key] += 1

        # Fold per-university aggregates back onto the node + summary entries.
        for node in nodes:
            if node["category"] == "University":
                node["professor_count"] = prof_count_by_univ.get(node["id"], 0)
                node["orgunit_count"] = org_count_by_univ.get(node["id"], 0)
        for uni in universities:
            uni["professor_count"] = prof_count_by_univ.get(uni["graph_key"], 0)
            uni["orgunit_count"] = org_count_by_univ.get(uni["graph_key"], 0)

        return {
            "build_id": build_id,
            "universities": universities,
            "nodes": nodes,
            "links": links,
        }

    def orgunit_professors(self, build_id: str, org_graph_key: str) -> dict[str, Any]:
        """Professors affiliated with one OrgUnit + their AFFILIATED_WITH edges.

        ``org_graph_key`` is the OrgUnit ``id`` returned by ``graph_tree``
        (``payload.graph_key``). Reads ``rel:AFFILIATED_WITH`` to find professor
        keys whose affiliation ends at this org, then ``node:Professor`` payloads
        for their names/titles. Empty result for an unknown org is legal.
        """
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            self._get_build(connection, build_id)
            affiliated_rows = list(
                connection.execute(
                    "SELECT start_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:AFFILIATED_WITH' "
                    "AND end_graph_key=?",
                    (build_id, org_graph_key),
                )
            )
            professor_keys = {str(row["start_graph_key"]) for row in affiliated_rows}

            org_row = connection.execute(
                "SELECT payload_json FROM graph_export_rows "
                "WHERE build_id=? AND partition_key='node:OrgUnit' "
                "AND json_extract(payload_json, '$.graph_key')=?",
                (build_id, org_graph_key),
            ).fetchone()
            org_payload = json_loads(org_row["payload_json"], {}) if org_row else {}
            org_to_univ = self._org_to_university(connection, build_id)
            orgunit = {
                "graph_key": org_graph_key,
                "label": str(org_payload.get("name") or org_graph_key),
                "kind": str(org_payload.get("kind") or ""),
                "university": org_to_univ.get(org_graph_key, ""),
            }

            professors: list[dict[str, Any]] = []
            if professor_keys:
                placeholders = ",".join("?" for _ in professor_keys)
                prof_rows = connection.execute(
                    f"SELECT payload_json FROM graph_export_rows "
                    f"WHERE build_id=? AND partition_key='node:Professor' "
                    f"AND json_extract(payload_json, '$.graph_key') IN ({placeholders})",
                    (build_id, *professor_keys),
                )
                for row in prof_rows:
                    payload = json_loads(row["payload_json"], {})
                    graph_key = str(payload.get("graph_key") or payload.get("id") or "")
                    professors.append(
                        {
                            "graph_key": graph_key,
                            "name": str(payload.get("name") or graph_key),
                            "title": payload.get("title"),
                            "title_family": payload.get("title_family"),
                            "role_status": str(payload.get("role_status") or ""),
                        }
                    )
            professors.sort(key=lambda p: p["name"])

        links = [
            {"source": p["graph_key"], "target": org_graph_key, "label": "AFFILIATED_WITH"}
            for p in professors
        ]
        return {
            "build_id": build_id,
            "orgunit": orgunit,
            "professors": professors,
            "links": links,
        }

    def professor_topics(self, build_id: str, professor_graph_key: str) -> dict[str, Any]:
        """One Professor node and its related Topic nodes.

        The graph export models Topic membership via the evidence path
        Professor -> ResearchStatement -> Topic. The monitor UI does not render
        ResearchStatement nodes for this drill-down; it uses them only to
        aggregate direct-looking Professor->Topic links grouped by
        (topic_graph_key, relation_type).
        """
        with self.reader.connect() as connection:
            self.reader.require_supported_schema(connection)
            self._get_build(connection, build_id)
            professor_row = connection.execute(
                "SELECT payload_json FROM graph_export_rows "
                "WHERE build_id=? AND partition_key='node:Professor' "
                "AND json_extract(payload_json, '$.graph_key')=?",
                (build_id, professor_graph_key),
            ).fetchone()
            if professor_row is None:
                raise MonitorCatalogError(f"unknown professor graph key: {professor_graph_key}")
            professor_payload = json_loads(professor_row["payload_json"], {})
            professor = {
                "graph_key": professor_graph_key,
                "name": str(professor_payload.get("name") or professor_graph_key),
                "title": professor_payload.get("title"),
                "title_family": professor_payload.get("title_family"),
                "role_status": str(professor_payload.get("role_status") or ""),
            }

            statement_rows = list(
                connection.execute(
                    "SELECT end_graph_key FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='rel:HAS_RESEARCH_STATEMENT' "
                    "AND start_graph_key=?",
                    (build_id, professor_graph_key),
                )
            )
            statement_keys = sorted(
                {str(row["end_graph_key"]) for row in statement_rows if row["end_graph_key"]}
            )
            if not statement_keys:
                return {
                    "build_id": build_id,
                    "professor": professor,
                    "topics": [],
                    "links": [],
                }

            partition_placeholders = ",".join("?" for _ in TOPIC_RELATION_PARTITIONS)
            statement_placeholders = ",".join("?" for _ in statement_keys)
            topic_rel_rows = list(
                connection.execute(
                    "SELECT partition_key, end_graph_key, payload_json FROM graph_export_rows "
                    f"WHERE build_id=? AND partition_key IN ({partition_placeholders}) "
                    f"AND start_graph_key IN ({statement_placeholders}) "
                    "ORDER BY partition_key, row_key",
                    (build_id, *TOPIC_RELATION_PARTITIONS, *statement_keys),
                )
            )
            aggregate: dict[tuple[str, str], dict[str, Any]] = {}
            topic_keys: set[str] = set()
            for row in topic_rel_rows:
                topic_key = str(row["end_graph_key"] or "")
                if not topic_key:
                    continue
                relation = str(row["partition_key"]).removeprefix("rel:")
                payload = json_loads(row["payload_json"], {})
                evidence_count = int(payload.get("evidence_count") or 1)
                confidence_value = payload.get("confidence")
                confidence = (
                    float(confidence_value)
                    if confidence_value is not None
                    else None
                )
                key = (topic_key, relation)
                previous = aggregate.get(key)
                if previous is None:
                    aggregate[key] = {
                        "source": professor_graph_key,
                        "target": topic_key,
                        "label": relation,
                        "evidence_count": evidence_count,
                        "confidence": confidence,
                    }
                else:
                    previous["evidence_count"] += evidence_count
                    if confidence is not None:
                        old_confidence = previous.get("confidence")
                        previous["confidence"] = (
                            confidence
                            if old_confidence is None
                            else min(float(old_confidence), confidence)
                        )
                topic_keys.add(topic_key)

            topics: list[dict[str, Any]] = []
            if topic_keys:
                topic_placeholders = ",".join("?" for _ in topic_keys)
                rows = connection.execute(
                    "SELECT payload_json FROM graph_export_rows "
                    "WHERE build_id=? AND partition_key='node:Topic' "
                    f"AND json_extract(payload_json, '$.graph_key') IN ({topic_placeholders})",
                    (build_id, *sorted(topic_keys)),
                )
                for row in rows:
                    payload = json_loads(row["payload_json"], {})
                    graph_key = str(payload.get("graph_key") or payload.get("id") or "")
                    if not graph_key:
                        continue
                    topics.append(
                        {
                            "graph_key": graph_key,
                            "logical_id": str(payload.get("logical_id") or ""),
                            "canonical_name": str(payload.get("canonical_name") or graph_key),
                            "normalized_name": str(payload.get("normalized_name") or ""),
                            "kind": str(payload.get("kind") or ""),
                            "status": str(payload.get("status") or ""),
                            "taxonomy_version": str(payload.get("taxonomy_version") or ""),
                        }
                    )

        topic_names = {topic["graph_key"]: topic["canonical_name"] for topic in topics}
        topics.sort(key=lambda t: (t["kind"], t["canonical_name"], t["graph_key"]))
        links = [
            link
            for link in aggregate.values()
            if link["target"] in topic_names
        ]
        links.sort(key=lambda link: (topic_names[link["target"]], link["label"]))
        return {
            "build_id": build_id,
            "professor": professor,
            "topics": topics,
            "links": links,
        }

    def _org_to_university(self, connection, build_id: str) -> dict[str, str]:
        rows = connection.execute(
            "SELECT start_graph_key, end_graph_key FROM graph_export_rows "
            "WHERE build_id=? AND partition_key='rel:PART_OF'",
            (build_id,),
        )
        return {str(r["start_graph_key"]): str(r["end_graph_key"]) for r in rows}

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
