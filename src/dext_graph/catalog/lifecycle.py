"""Deterministic validation and recoverable READY/ACTIVE publication."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Awaitable, Callable

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
from dext_graph.catalog.evidence import PARTITIONS
from dext_graph.catalog.ids import canonical_json_hash, hash_parts
from dext_graph.catalog.neo4j_sink import (
    get_active_build,
    set_active_build,
    validate_persisted_exports,
)
from dext_graph.catalog.org_units import entity_org_unit_map
from dext_graph.catalog.vector_sink import ProfessorQdrant, professor_collection_name
from dext_graph.config import GraphSettings

VALIDATION_VERSION = "release-validation-v1"
GOLD_GATE_NAMES = frozenset(
    {"curation_gold_gate", "graph_gold_gate", "topic_gold_gate"}
)

Neo4jValidator = Callable[[str, GraphSettings], Awaitable[None]]
Neo4jSetter = Callable[[str, GraphSettings], Awaitable[None]]
Neo4jReader = Callable[[GraphSettings], Awaitable[str | None]]


def _safe_error(exc: BaseException) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (value or type(exc).__name__)[:2000]


def _check(
    name: str,
    passed: bool,
    *,
    expected: object,
    actual: object,
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": bool(passed),
        "expected": expected,
        "actual": actual,
    }


def _summary(connection: sqlite3.Connection, table: str, build_id: str) -> dict[str, Any]:
    row = connection.execute(
        f"SELECT status,summary_json FROM {table} WHERE build_id=?", (build_id,)
    ).fetchone()
    if row is None:
        return {"status": "MISSING"}
    return {"status": str(row["status"]), **json_loads(row["summary_json"], {})}


def _topic_cycle_count(connection: sqlite3.Connection, build_id: str) -> int:
    adjacency: dict[str, set[str]] = {}
    for row in connection.execute(
        "SELECT from_topic_id,to_topic_id FROM topic_relations "
        "WHERE build_id=? AND relation_type='SUBTOPIC_OF'",
        (build_id,),
    ):
        adjacency.setdefault(str(row["from_topic_id"]), set()).add(
            str(row["to_topic_id"])
        )

    def reaches(start: str, target: str) -> bool:
        pending = list(adjacency.get(start, ()))
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(adjacency.get(current, ()))
        return False

    return sum(
        child == parent or reaches(parent, child)
        for child, parents in adjacency.items()
        for parent in parents
    )


def _manifest_mismatches(
    connection: sqlite3.Connection, build_id: str
) -> tuple[int, list[str]]:
    mismatches = 0
    present: list[str] = []
    for partition in PARTITIONS:
        manifest = connection.execute(
            "SELECT row_count,checksum FROM graph_export_partitions "
            "WHERE build_id=? AND partition_key=?",
            (build_id, partition),
        ).fetchone()
        if manifest is None:
            mismatches += 1
            continue
        present.append(partition)
        digest = hashlib.sha256()
        count = 0
        for row in connection.execute(
            "SELECT row_checksum FROM graph_export_rows "
            "WHERE build_id=? AND partition_key=? ORDER BY row_key",
            (build_id, partition),
        ):
            digest.update(str(row["row_checksum"]).encode("ascii"))
            digest.update(b"\n")
            count += 1
        if count != int(manifest["row_count"]) or digest.hexdigest() != str(
            manifest["checksum"]
        ):
            mismatches += 1
    extra = {
        str(row[0])
        for row in connection.execute(
            "SELECT partition_key FROM graph_export_partitions WHERE build_id=?",
            (build_id,),
        )
    } - set(PARTITIONS)
    mismatches += len(extra)
    return mismatches, sorted(present)


def _catalog_checks(
    connection: sqlite3.Connection, build_id: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    run_summaries = {
        "curation": _summary(connection, "curation_runs", build_id),
        "graph": _summary(connection, "graph_runs", build_id),
        "topics": _summary(connection, "topic_runs", build_id),
        "vector": _summary(connection, "vector_runs", build_id),
    }
    run_statuses = {name: value["status"] for name, value in run_summaries.items()}
    checks.append(
        _check(
            "stage_runs_completed",
            all(value == "COMPLETED" for value in run_statuses.values()),
            expected={name: "COMPLETED" for name in sorted(run_statuses)},
            actual=dict(sorted(run_statuses.items())),
        )
    )

    tasks = [
        dict(row)
        for row in connection.execute(
            "SELECT university_id,status,rows_read,observations_written,rejected_rows "
            "FROM build_source_tasks WHERE build_id=? ORDER BY university_id",
            (build_id,),
        )
    ]
    bad_tasks = sum(
        task["status"] != "COMPLETED"
        or int(task["rows_read"])
        != int(task["observations_written"]) + int(task["rejected_rows"])
        for task in tasks
    )
    checks.append(
        _check(
            "source_observation_coverage",
            bool(tasks) and bad_tasks == 0,
            expected=0,
            actual=bad_tasks,
        )
    )
    rejected = sum(int(task["rejected_rows"]) for task in tasks)
    rejection_findings = int(
        connection.execute(
            "SELECT COUNT(*) FROM quality_findings "
            "WHERE build_id=? AND code='legacy_row_unimportable'",
            (build_id,),
        ).fetchone()[0]
    )
    checks.append(
        _check(
            "source_rejections_explained",
            rejection_findings >= rejected,
            expected=f">={rejected}",
            actual=rejection_findings,
        )
    )

    missing_observations = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM canonical_professors cp
            WHERE cp.build_id=? AND cp.active=1 AND NOT EXISTS (
              SELECT 1 FROM entity_observations eo
              JOIN professor_observations o ON o.id=eo.observation_id AND o.active=1
              WHERE eo.build_id=cp.build_id AND eo.entity_id=cp.entity_id
            )
            """,
            (build_id,),
        ).fetchone()[0]
    )
    checks.append(
        _check(
            "active_canonical_has_observation",
            missing_observations == 0,
            expected=0,
            actual=missing_observations,
        )
    )
    duplicate_strong = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT claim_type,claim_value
              FROM identity_claims WHERE active=1 AND strength='strong'
              GROUP BY claim_type,claim_value HAVING COUNT(DISTINCT entity_id)>1
            )
            """
        ).fetchone()[0]
    )
    checks.append(
        _check(
            "strong_claim_uniqueness",
            duplicate_strong == 0,
            expected=0,
            actual=duplicate_strong,
        )
    )
    orphan_statements = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM research_statements s
            LEFT JOIN professor_observations o ON o.id=s.observation_id
            LEFT JOIN entity_observations eo
              ON eo.build_id=s.build_id AND eo.observation_id=s.observation_id
            WHERE s.build_id=? AND (o.id IS NULL OR eo.observation_id IS NULL)
            """,
            (build_id,),
        ).fetchone()[0]
    )
    checks.append(
        _check(
            "research_statement_traceability",
            orphan_statements == 0,
            expected=0,
            actual=orphan_statements,
        )
    )
    bad_provenance = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM graph_export_rows
            WHERE build_id=? AND (
              provenance_ref IS NULL OR provenance_ref='' OR
              (provenance_ref NOT LIKE 'catalog:%' AND provenance_ref NOT LIKE 'taxonomy:%')
            )
            """,
            (build_id,),
        ).fetchone()[0]
    )
    checks.append(
        _check(
            "graph_provenance_parseable",
            bad_provenance == 0,
            expected=0,
            actual=bad_provenance,
        )
    )
    manifest_mismatches, present_partitions = _manifest_mismatches(
        connection, build_id
    )
    checks.append(
        _check(
            "catalog_graph_manifest",
            manifest_mismatches == 0 and set(present_partitions) == set(PARTITIONS),
            expected=len(PARTITIONS),
            actual={"partitions": len(present_partitions), "mismatches": manifest_mismatches},
        )
    )

    incompatible_topic_links = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM statement_topic_links l
            JOIN topics t ON t.taxonomy_version=l.taxonomy_version AND t.id=l.topic_id
            WHERE l.build_id=? AND l.review_status='approved' AND (
              t.status!='active' OR
              (l.relation_type='PRIMARY_TOPIC' AND t.kind!='discipline') OR
              (l.relation_type='USES_METHOD' AND t.kind!='method') OR
              (l.relation_type='APPLIED_TO' AND t.kind!='application_domain') OR
              (l.relation_type='TARGETS_TASK' AND t.kind!='task') OR
              (l.relation_type='STUDIES' AND t.kind!='research_object')
            )
            """,
            (build_id,),
        ).fetchone()[0]
    )
    checks.append(
        _check(
            "topic_link_compatibility",
            incompatible_topic_links == 0,
            expected=0,
            actual=incompatible_topic_links,
        )
    )
    cycles = _topic_cycle_count(connection, build_id)
    checks.append(
        _check("topic_dag_cycles", cycles == 0, expected=0, actual=cycles)
    )

    org_ids_by_entity = entity_org_unit_map(connection, build_id)
    eligible_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT cp.entity_id,cp.role_status,pp.profile_hash
            FROM canonical_professors cp
            LEFT JOIN professor_profiles pp
              ON pp.build_id=cp.build_id AND pp.entity_id=cp.entity_id
            WHERE cp.build_id=? AND cp.active=1 AND cp.role_status!='excluded'
            ORDER BY cp.entity_id
            """,
            (build_id,),
        )
    ]
    missing_profiles = sum(row["profile_hash"] is None for row in eligible_rows)
    checks.append(
        _check(
            "eligible_professor_profiles",
            missing_profiles == 0,
            expected=0,
            actual=missing_profiles,
        )
    )
    professor_exports = {
        str(row["row_key"]): json_loads(row["payload_json"], {})
        for row in connection.execute(
            "SELECT row_key,payload_json FROM graph_export_rows "
            "WHERE build_id=? AND partition_key='node:Professor' ORDER BY row_key",
            (build_id,),
        )
    }
    profile_mismatches = sum(
        professor_exports.get(str(row["entity_id"]), {}).get("profile_hash")
        != row["profile_hash"]
        for row in eligible_rows
    )
    checks.append(
        _check(
            "catalog_graph_profile_hash",
            profile_mismatches == 0,
            expected=0,
            actual=profile_mismatches,
        )
    )
    graph_orgs: dict[str, set[str]] = {}
    prefix = f"{build_id}:"
    for row in connection.execute(
        "SELECT start_graph_key,end_graph_key FROM graph_export_rows "
        "WHERE build_id=? AND partition_key='rel:AFFILIATED_WITH'",
        (build_id,),
    ):
        start = str(row["start_graph_key"])
        end = str(row["end_graph_key"])
        if start.startswith(prefix) and end.startswith(prefix):
            graph_orgs.setdefault(start.removeprefix(prefix), set()).add(
                end.removeprefix(prefix)
            )
    active_entities = [
        str(row[0])
        for row in connection.execute(
            "SELECT entity_id FROM canonical_professors "
            "WHERE build_id=? AND active=1 ORDER BY entity_id",
            (build_id,),
        )
    ]
    org_mismatches = sum(
        sorted(graph_orgs.get(entity_id, set()))
        != org_ids_by_entity.get(entity_id, [])
        for entity_id in active_entities
    )
    checks.append(
        _check(
            "catalog_graph_org_units",
            org_mismatches == 0,
            expected=0,
            actual=org_mismatches,
        )
    )

    curation_gold = run_summaries["curation"].get("gold", {})
    curation_gold_passed = (
        curation_gold.get("status") == "evaluated"
        and float(curation_gold.get("auto_merge_pairwise_precision", 0)) >= 0.995
        and float(curation_gold.get("excluded_precision", 0)) >= 0.99
        and float(curation_gold.get("supervised_lecturer_retention", 0)) >= 0.95
    )
    checks.append(
        _check(
            "curation_gold_gate",
            curation_gold_passed,
            expected="evaluated and thresholds passed",
            actual=curation_gold.get("status", "missing"),
        )
    )
    graph_gold = run_summaries["graph"].get("graph_gold", {})
    checks.append(
        _check(
            "graph_gold_gate",
            run_summaries["graph"].get("graph_gold_status") == "evaluated"
            and graph_gold.get("gate_passed") is True,
            expected="evaluated and passed",
            actual=run_summaries["graph"].get("graph_gold_status", "missing"),
        )
    )
    topic_gold = run_summaries["topics"].get("gold", {})
    topic_gold_passed = (
        run_summaries["topics"].get("gold_status") == "evaluated"
        and topic_gold.get("topic_gate_passed") is True
        and topic_gold.get("relation_gate_passed") is True
        and topic_gold.get("subtopic_gate_passed") is True
        and topic_gold.get("dag_gate_passed") is True
    )
    checks.append(
        _check(
            "topic_gold_gate",
            topic_gold_passed,
            expected="evaluated and passed",
            actual=run_summaries["topics"].get("gold_status", "missing"),
        )
    )

    expected_vectors = {
        str(row["entity_id"]): {
            "build_id": build_id,
            "entity_id": str(row["entity_id"]),
            "profile_hash": row["profile_hash"],
            "role_status": str(row["role_status"]),
            "org_unit_ids": org_ids_by_entity.get(str(row["entity_id"]), []),
        }
        for row in eligible_rows
    }
    return checks, expected_vectors


async def _qdrant_checks(
    sink: Any,
    collection_name: str,
    expected: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    count = await sink.count(collection_name)
    actual: dict[str, dict[str, Any]] = {}
    duplicates = 0
    async for item in sink.iter_payloads(collection_name):
        entity_id = str(item["entity_id"])
        duplicates += int(entity_id in actual)
        actual[entity_id] = dict(item["payload"])
    id_mismatches = len(set(expected) ^ set(actual)) + duplicates
    payload_mismatches = 0
    canonical_actual: list[dict[str, Any]] = []
    for entity_id in sorted(actual):
        payload = actual[entity_id]
        canonical = {
            "build_id": payload.get("build_id"),
            "entity_id": payload.get("entity_id"),
            "profile_hash": payload.get("profile_hash"),
            "role_status": payload.get("role_status"),
            "org_unit_ids": sorted(payload.get("org_unit_ids") or []),
        }
        canonical_actual.append(canonical)
        wanted = expected.get(entity_id)
        if wanted is None or canonical != wanted:
            payload_mismatches += 1
    return [
        _check(
            "qdrant_point_count",
            count == len(expected) == len(actual),
            expected=len(expected),
            actual={"count": count, "payloads": len(actual)},
        ),
        _check(
            "qdrant_entity_ids",
            id_mismatches == 0,
            expected=0,
            actual=id_mismatches,
        ),
        _check(
            "qdrant_payload_reconciliation",
            payload_mismatches == 0,
            expected={"mismatches": 0},
            actual={
                "mismatches": payload_mismatches,
                "checksum": canonical_json_hash(canonical_actual),
            },
        ),
    ]


def _prepare_validation(connection: sqlite3.Connection, build_id: str) -> str:
    build = connection.execute(
        "SELECT status FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    if str(build["status"]) not in {"VALIDATING", "FAILED_VALIDATION", "READY"}:
        raise CatalogError(
            f"build {build_id} cannot validate from status {build['status']}"
        )
    run_id = hash_parts(build_id, VALIDATION_VERSION)
    existing = connection.execute(
        "SELECT validation_version FROM validation_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO validation_runs(
              id,build_id,validation_version,status,manifest_json,started_at
            ) VALUES (?,?,?,'RUNNING','{}',?)
            """,
            (run_id, build_id, VALIDATION_VERSION, utcnow_iso()),
        )
    elif str(existing["validation_version"]) != VALIDATION_VERSION:
        raise CatalogError("validation version changed; create a new build")
    else:
        connection.execute(
            "UPDATE validation_runs SET status='RUNNING',manifest_json='{}',"
            "manifest_hash=NULL,finished_at=NULL,last_error=NULL WHERE build_id=?",
            (build_id,),
        )
    connection.execute(
        "UPDATE graph_builds SET status='VALIDATING',last_error=NULL WHERE id=?",
        (build_id,),
    )
    return run_id


def _finish_validation(
    connection: sqlite3.Connection,
    build_id: str,
    run_id: str,
    manifest: dict[str, Any],
) -> None:
    manifest_hash = canonical_json_hash(manifest)
    passed = bool(manifest["passed"])
    status = "PASSED" if passed else "FAILED"
    build_status = "READY" if passed else "FAILED_VALIDATION"
    failed_names = [
        str(check["name"]) for check in manifest["checks"] if not check["passed"]
    ]
    error = None if passed else "validation failed: " + ", ".join(failed_names)
    now = utcnow_iso()
    connection.execute(
        "UPDATE validation_runs SET status=?,manifest_json=?,manifest_hash=?,"
        "finished_at=?,last_error=? WHERE id=?",
        (status, json_dumps(manifest), manifest_hash, now, error, run_id),
    )
    summary = json_loads(
        connection.execute(
            "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()[0],
        {},
    )
    summary["validation"] = {
        "version": VALIDATION_VERSION,
        "manifest_hash": manifest_hash,
        "passed": passed,
        "release_mode": manifest.get("release_mode", "standard"),
        "skipped_checks": manifest.get("skipped_checks", []),
    }
    connection.execute(
        "UPDATE graph_builds SET status=?,summary_json=?,last_error=? WHERE id=?",
        (build_status, json_dumps(summary), error, build_id),
    )


def _apply_skipped_gold_gates(checks: list[dict[str, Any]]) -> list[str]:
    skipped: list[str] = []
    for check in checks:
        if (
            str(check.get("name")) in GOLD_GATE_NAMES
            and not bool(check.get("passed"))
        ):
            check["passed"] = True
            check["skipped"] = True
            check["skip_reason"] = "skip_gold_gates"
            skipped.append(str(check["name"]))
    return skipped


async def run_validation(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    qdrant_sink: Any | None = None,
    neo4j_validator: Neo4jValidator | None = None,
    skip_gold_gates: bool = False,
) -> dict[str, Any]:
    run_id = await writer.execute(
        lambda connection: _prepare_validation(connection, build_id)
    )
    checks: list[dict[str, Any]] = []
    expected_vectors: dict[str, dict[str, Any]] = {}
    try:
        checks, expected_vectors = await writer.execute(
            lambda connection: _catalog_checks(connection, build_id),
            transactional=False,
        )
    except Exception as exc:  # noqa: BLE001 - make validation failure durable
        checks.append(
            _check(
                "catalog_validation_execution",
                False,
                expected="success",
                actual=type(exc).__name__,
            )
        )

    validator = neo4j_validator or validate_persisted_exports
    try:
        await validator(build_id, settings)
    except Exception as exc:  # noqa: BLE001 - external readback is a gate
        checks.append(
            _check(
                "neo4j_export_manifest",
                False,
                expected="match",
                actual=type(exc).__name__,
            )
        )
    else:
        checks.append(
            _check("neo4j_export_manifest", True, expected="match", actual="match")
        )

    owns_qdrant = qdrant_sink is None
    qdrant_sink = qdrant_sink or ProfessorQdrant(settings.qdrant_url)
    try:
        checks.extend(
            await _qdrant_checks(
                qdrant_sink, professor_collection_name(build_id), expected_vectors
            )
        )
    except Exception as exc:  # noqa: BLE001 - external readback is a gate
        checks.append(
            _check(
                "qdrant_readback",
                False,
                expected="success",
                actual=type(exc).__name__,
            )
        )
    finally:
        if owns_qdrant:
            await qdrant_sink.close()

    checks.sort(key=lambda item: str(item["name"]))
    skipped_checks = _apply_skipped_gold_gates(checks) if skip_gold_gates else []
    manifest = {
        "validation_version": VALIDATION_VERSION,
        "build_id": build_id,
        "checks": checks,
        "release_mode": "skip_gold_gates" if skip_gold_gates else "standard",
        "skipped_checks": skipped_checks,
        "passed": all(bool(check["passed"]) for check in checks),
    }
    await writer.execute(
        lambda connection: _finish_validation(
            connection, build_id, run_id, manifest
        )
    )
    from dext_graph.catalog.workflow import build_status

    return build_status(writer.path, build_id)


async def validate_build(
    build_id: str,
    settings: GraphSettings | None = None,
    *,
    qdrant_sink: Any | None = None,
    neo4j_validator: Neo4jValidator | None = None,
    skip_gold_gates: bool = False,
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path, retention=settings.catalog_backup_retention)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            return await run_validation(
                writer,
                build_id,
                settings,
                qdrant_sink=qdrant_sink,
                neo4j_validator=neo4j_validator,
                skip_gold_gates=skip_gold_gates,
            )


def _prepare_promotion(
    connection: sqlite3.Connection, build_id: str
) -> tuple[str, str]:
    build = connection.execute(
        "SELECT status FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    if str(build["status"]) != "READY":
        raise CatalogError(f"build {build_id} is not READY")
    validation = connection.execute(
        "SELECT status,manifest_hash FROM validation_runs WHERE build_id=?",
        (build_id,),
    ).fetchone()
    if (
        validation is None
        or str(validation["status"]) != "PASSED"
        or validation["manifest_hash"] is None
    ):
        raise CatalogError("promotion requires a passed validation manifest")
    vector = connection.execute(
        "SELECT status,collection_name FROM vector_runs WHERE build_id=?",
        (build_id,),
    ).fetchone()
    if vector is None or str(vector["status"]) != "COMPLETED":
        raise CatalogError("promotion requires a completed vector run")
    previous = connection.execute(
        "SELECT id FROM graph_builds WHERE status='ACTIVE' AND id<>?", (build_id,)
    ).fetchone()
    previous_id = str(previous[0]) if previous is not None else None
    now = utcnow_iso()
    connection.execute(
        """
        INSERT INTO promotion_runs(
          build_id,validation_manifest_hash,previous_active_build_id,status,
          neo4j_done,qdrant_done,readback_done,started_at,updated_at
        ) VALUES (?,?,?,'RUNNING',0,0,0,?,?)
        ON CONFLICT(build_id) DO UPDATE SET
          validation_manifest_hash=excluded.validation_manifest_hash,
          previous_active_build_id=excluded.previous_active_build_id,
          status='RUNNING',neo4j_done=0,qdrant_done=0,readback_done=0,
          updated_at=excluded.updated_at,finished_at=NULL,last_error=NULL
        """,
        (
            build_id,
            str(validation["manifest_hash"]),
            previous_id,
            now,
            now,
        ),
    )
    return str(vector["collection_name"]), str(validation["manifest_hash"])


def _promotion_checkpoint(
    connection: sqlite3.Connection, build_id: str, field: str
) -> None:
    if field not in {"neo4j_done", "qdrant_done", "readback_done"}:
        raise ValueError(f"invalid promotion checkpoint: {field}")
    connection.execute(
        f"UPDATE promotion_runs SET {field}=1,updated_at=? WHERE build_id=?",
        (utcnow_iso(), build_id),
    )


def _finish_promotion(connection: sqlite3.Connection, build_id: str) -> None:
    now = utcnow_iso()
    connection.execute(
        "UPDATE graph_builds SET status='READY' WHERE status='ACTIVE' AND id<>?",
        (build_id,),
    )
    connection.execute(
        "UPDATE graph_builds SET status='ACTIVE',finished_at=?,last_error=NULL WHERE id=?",
        (now, build_id),
    )
    connection.execute(
        "UPDATE promotion_runs SET status='COMPLETED',readback_done=1,"
        "updated_at=?,finished_at=?,last_error=NULL WHERE build_id=?",
        (now, now, build_id),
    )


def _fail_promotion(
    connection: sqlite3.Connection, build_id: str, error: str
) -> None:
    connection.execute(
        "UPDATE promotion_runs SET status='FAILED',updated_at=?,last_error=? "
        "WHERE build_id=?",
        (utcnow_iso(), error, build_id),
    )


async def run_promotion(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    qdrant_sink: Any | None = None,
    neo4j_setter: Neo4jSetter | None = None,
    neo4j_reader: Neo4jReader | None = None,
) -> dict[str, Any]:
    collection_name, _manifest_hash = await writer.execute(
        lambda connection: _prepare_promotion(connection, build_id)
    )
    owns_qdrant = qdrant_sink is None
    qdrant_sink = qdrant_sink or ProfessorQdrant(settings.qdrant_url)
    setter = neo4j_setter or set_active_build
    reader = neo4j_reader or get_active_build
    try:
        await setter(build_id, settings)
        await writer.execute(
            lambda connection: _promotion_checkpoint(
                connection, build_id, "neo4j_done"
            )
        )
        await qdrant_sink.switch_current_alias(collection_name)
        await writer.execute(
            lambda connection: _promotion_checkpoint(
                connection, build_id, "qdrant_done"
            )
        )
        neo4j_active = await reader(settings)
        qdrant_active = await qdrant_sink.resolve_current_alias()
        if neo4j_active != build_id or qdrant_active != collection_name:
            raise CatalogError(
                "promotion readback mismatch: "
                f"neo4j={neo4j_active!r}, qdrant={qdrant_active!r}"
            )
        await writer.execute(
            lambda connection: _promotion_checkpoint(
                connection, build_id, "readback_done"
            )
        )
        await writer.execute(
            lambda connection: _finish_promotion(connection, build_id)
        )
    except Exception as exc:
        await writer.execute(
            lambda connection: _fail_promotion(
                connection, build_id, _safe_error(exc)
            )
        )
        raise
    finally:
        if owns_qdrant:
            await qdrant_sink.close()
    from dext_graph.catalog.workflow import build_status

    return build_status(writer.path, build_id)


async def promote_build(
    build_id: str,
    settings: GraphSettings | None = None,
    *,
    qdrant_sink: Any | None = None,
    neo4j_setter: Neo4jSetter | None = None,
    neo4j_reader: Neo4jReader | None = None,
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path, retention=settings.catalog_backup_retention)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            return await run_promotion(
                writer,
                build_id,
                settings,
                qdrant_sink=qdrant_sink,
                neo4j_setter=neo4j_setter,
                neo4j_reader=neo4j_reader,
            )


__all__ = [
    "VALIDATION_VERSION",
    "promote_build",
    "run_promotion",
    "run_validation",
    "validate_build",
]
