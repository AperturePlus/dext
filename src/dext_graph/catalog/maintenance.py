"""Read-only diagnostics and cache compaction for the catalog database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import (
    CatalogError,
    backup_existing_catalog,
    catalog_write_lock,
    connect_catalog,
    connect_catalog_read_only,
    ensure_free_space,
    initialize_catalog,
    sqlite_sidecar_paths,
)
from dext_graph.catalog.evidence import set_graph_export_pruned
from dext_graph.config import GraphSettings

PROTECTED_COMPACTION_STATUSES = {"ACTIVE", "READY", "VALIDATING"}
_VACUUM_FREE_SPACE_RESERVE = 64 * 1024 * 1024


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _dbstat_objects(connection: sqlite3.Connection) -> tuple[bool, list[dict[str, Any]], str | None]:
    try:
        rows = connection.execute(
            "SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name ORDER BY bytes DESC"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        return False, [], str(exc)
    return True, [{"name": row["name"], "bytes": int(row["bytes"] or 0)} for row in rows], None


def _backup_stats(catalog_path: Path) -> dict[str, Any]:
    backup_dir = catalog_path.parent / "backups"
    files = sorted(
        [path for path in backup_dir.glob("catalog-*.db*") if path.is_file()]
        if backup_dir.exists()
        else [],
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    db_files = [path for path in files if path.name.endswith(".db")]
    return {
        "directory": str(backup_dir),
        "count": len(db_files),
        "file_count": len(files),
        "total_bytes": sum(_file_size(path) for path in files),
        "recent": [
            {
                "path": str(path),
                "bytes": _file_size(path),
                "sha256_present": path.with_suffix(path.suffix + ".sha256").is_file(),
            }
            for path in db_files[:5]
        ],
    }


def _source_snapshot_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT snapshot_path FROM source_snapshots").fetchall()
    total = 0
    existing = 0
    missing = 0
    for row in rows:
        path = Path(str(row["snapshot_path"])).expanduser()
        if path.is_file():
            existing += 1
            total += _file_size(path)
        else:
            missing += 1
    return {
        "count": len(rows),
        "existing_count": existing,
        "missing_count": missing,
        "total_bytes": total,
    }


def _graph_export_distribution(connection: sqlite3.Connection) -> dict[str, Any]:
    by_build = [
        {
            "build_id": row["build_id"],
            "status": row["status"],
            "row_count": int(row["row_count"] or 0),
            "partition_count": int(row["partition_count"] or 0),
            "payload_json_bytes": int(row["payload_json_bytes"] or 0),
        }
        for row in connection.execute(
            """
            SELECT b.id AS build_id, b.status,
                   COUNT(r.row_key) AS row_count,
                   COUNT(DISTINCT r.partition_key) AS partition_count,
                   COALESCE(SUM(LENGTH(r.payload_json)), 0) AS payload_json_bytes
            FROM graph_builds b
            LEFT JOIN graph_export_rows r ON r.build_id=b.id
            GROUP BY b.id, b.status
            HAVING row_count > 0 OR partition_count > 0
            ORDER BY row_count DESC, b.id
            """
        )
    ]
    by_partition = [
        {
            "build_id": row["build_id"],
            "status": row["status"],
            "partition_key": row["partition_key"],
            "row_kind": row["row_kind"],
            "label_or_type": row["label_or_type"],
            "row_count": int(row["row_count"] or 0),
            "payload_json_bytes": int(row["payload_json_bytes"] or 0),
        }
        for row in connection.execute(
            """
            SELECT r.build_id, b.status, r.partition_key, r.row_kind, r.label_or_type,
                   COUNT(*) AS row_count,
                   COALESCE(SUM(LENGTH(r.payload_json)), 0) AS payload_json_bytes
            FROM graph_export_rows r
            JOIN graph_builds b ON b.id=r.build_id
            GROUP BY r.build_id, b.status, r.partition_key, r.row_kind, r.label_or_type
            ORDER BY r.build_id, r.partition_key
            """
        )
    ]
    return {
        "total_rows": sum(item["row_count"] for item in by_build),
        "total_payload_json_bytes": sum(item["payload_json_bytes"] for item in by_build),
        "by_build": by_build,
        "by_partition": by_partition,
    }


def catalog_size(settings: GraphSettings) -> dict[str, Any]:
    catalog_path = Path(settings.catalog_path).expanduser().resolve()
    files = {
        "catalog": {"path": str(catalog_path), "bytes": _file_size(catalog_path)}
    }
    for sidecar in sqlite_sidecar_paths(catalog_path):
        files[sidecar.name.removeprefix(catalog_path.name)] = {
            "path": str(sidecar),
            "bytes": _file_size(sidecar),
        }
    files["total_bytes"] = sum(
        int(value["bytes"]) for value in files.values() if isinstance(value, dict)
    )
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        available, objects, error = _dbstat_objects(connection)
        return {
            "catalog_path": str(catalog_path),
            "files": files,
            "backups": _backup_stats(catalog_path),
            "source_snapshots": _source_snapshot_stats(connection),
            "dbstat": {
                "available": available,
                "objects": objects,
                "error": error,
            },
            "graph_export_rows": _graph_export_distribution(connection),
        }


def _build_stats(connection: sqlite3.Connection, build_id: str) -> dict[str, int]:
    rows = int(
        connection.execute(
            "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=?", (build_id,)
        ).fetchone()[0]
    )
    partitions = int(
        connection.execute(
            "SELECT COUNT(*) FROM graph_export_partitions WHERE build_id=?", (build_id,)
        ).fetchone()[0]
    )
    checkpoints = int(
        connection.execute(
            "SELECT COUNT(*) FROM sink_checkpoints "
            "WHERE build_id=? AND sink IN ('graph_export','neo4j')",
            (build_id,),
        ).fetchone()[0]
    )
    manifest_rows = int(
        connection.execute(
            "SELECT COALESCE(SUM(row_count), 0) FROM graph_export_partitions WHERE build_id=?",
            (build_id,),
        ).fetchone()[0]
    )
    return {
        "graph_export_rows": rows,
        "manifest_rows": manifest_rows,
        "partitions": partitions,
        "checkpoints": checkpoints,
    }


def _graph_export_dbstat_bytes(objects: list[dict[str, Any]]) -> int | None:
    if not objects:
        return None
    total = 0
    for item in objects:
        name = str(item["name"])
        if (
            name in {"graph_export_rows", "graph_export_partitions", "ix_graph_export_partition_key"}
            or name.startswith("sqlite_autoindex_graph_export_rows")
            or name.startswith("sqlite_autoindex_graph_export_partitions")
        ):
            total += int(item["bytes"])
    return total


def _plan_from_connection(
    connection: sqlite3.Connection,
    catalog_path: Path,
    keep_build: str,
    prune_builds: tuple[str, ...],
) -> dict[str, Any]:
    if not keep_build:
        raise CatalogError("--keep-build is required")
    build_rows = {
        str(row["id"]): dict(row)
        for row in connection.execute("SELECT id, status, started_at FROM graph_builds")
    }
    if keep_build not in build_rows:
        raise CatalogError(f"unknown keep build ID: {keep_build}")
    explicit = tuple(dict.fromkeys(prune_builds))
    missing = [build_id for build_id in explicit if build_id not in build_rows]
    if missing:
        raise CatalogError(f"unknown prune build ID: {', '.join(missing)}")
    protected = {
        build_id
        for build_id, row in build_rows.items()
        if str(row["status"]) in PROTECTED_COMPACTION_STATUSES
    }
    protected.add(keep_build)
    blocked = [build_id for build_id in explicit if build_id in protected]
    if blocked:
        raise CatalogError(f"cannot prune protected build ID: {', '.join(blocked)}")

    automatic = [
        build_id
        for build_id, row in build_rows.items()
        if str(row["status"]) == "FAILED" and build_id not in protected
    ]
    target_ids = list(dict.fromkeys([*automatic, *explicit]))
    targets: list[dict[str, Any]] = []
    for build_id in target_ids:
        stats = _build_stats(connection, build_id)
        if not any(stats.values()) and build_id not in explicit:
            continue
        targets.append(
            {
                "build_id": build_id,
                "status": str(build_rows[build_id]["status"]),
                "explicit": build_id in explicit,
                **stats,
            }
        )

    available, objects, error = _dbstat_objects(connection)
    total_export_rows = int(
        connection.execute("SELECT COUNT(*) FROM graph_export_rows").fetchone()[0]
    )
    target_rows = sum(item["graph_export_rows"] for item in targets)
    dbstat_export_bytes = _graph_export_dbstat_bytes(objects) if available else None
    estimated = (
        int(dbstat_export_bytes * (target_rows / total_export_rows))
        if dbstat_export_bytes is not None and total_export_rows > 0
        else None
    )
    return {
        "catalog_path": str(catalog_path),
        "keep_build": keep_build,
        "protected_statuses": sorted(PROTECTED_COMPACTION_STATUSES),
        "explicit_prune_builds": list(explicit),
        "target_builds": targets,
        "totals": {
            "builds": len(targets),
            "graph_export_rows": target_rows,
            "partitions": sum(item["partitions"] for item in targets),
            "checkpoints": sum(item["checkpoints"] for item in targets),
            "estimated_reclaim_bytes": estimated,
        },
        "vacuum": {
            "required_free_bytes_hint": _file_size(catalog_path) + _VACUUM_FREE_SPACE_RESERVE,
            "wal_checkpoint": "TRUNCATE",
        },
        "dbstat": {
            "available": available,
            "error": error,
            "graph_export_bytes": dbstat_export_bytes,
        },
    }


def plan_catalog_compaction(
    settings: GraphSettings,
    *,
    keep_build: str,
    prune_builds: tuple[str, ...] = (),
) -> dict[str, Any]:
    catalog_path = Path(settings.catalog_path).expanduser().resolve()
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        return _plan_from_connection(connection, catalog_path, keep_build, prune_builds)


def compact_catalog(
    settings: GraphSettings,
    *,
    keep_build: str,
    prune_builds: tuple[str, ...] = (),
    yes: bool = False,
) -> dict[str, Any]:
    if not yes:
        plan = plan_catalog_compaction(
            settings, keep_build=keep_build, prune_builds=prune_builds
        )
        plan["dry_run"] = True
        plan["executed"] = False
        return plan

    catalog_path = Path(settings.catalog_path).expanduser().resolve()
    if not catalog_path.is_file():
        raise CatalogError(f"catalog does not exist: {catalog_path}")
    with catalog_write_lock(catalog_path):
        with closing(connect_catalog_read_only(catalog_path)) as connection:
            _plan_from_connection(connection, catalog_path, keep_build, prune_builds)
        backup_path = backup_existing_catalog(
            catalog_path,
            retention=settings.catalog_backup_retention,
        )
        initialize_catalog(catalog_path)
        ensure_free_space(
            catalog_path.parent,
            _file_size(catalog_path) + _VACUUM_FREE_SPACE_RESERVE,
            operation="catalog VACUUM",
        )
        with closing(connect_catalog(catalog_path)) as connection:
            plan = _plan_from_connection(
                connection, catalog_path, keep_build, prune_builds
            )
            target_ids = [item["build_id"] for item in plan["target_builds"]]
            if target_ids:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for build_id in target_ids:
                        connection.execute(
                            "DELETE FROM graph_export_rows WHERE build_id=?", (build_id,)
                        )
                        connection.execute(
                            "DELETE FROM graph_export_partitions WHERE build_id=?",
                            (build_id,),
                        )
                        connection.execute(
                            "DELETE FROM sink_checkpoints "
                            "WHERE build_id=? AND sink IN ('graph_export','neo4j')",
                            (build_id,),
                        )
                        set_graph_export_pruned(connection, build_id, True)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        vacuum = {"ran": False, "before_bytes": _file_size(catalog_path), "after_bytes": None}
        if plan["target_builds"]:
            with closing(connect_catalog(catalog_path)) as connection:
                before = _file_size(catalog_path)
                connection.execute("VACUUM")
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                after = _file_size(catalog_path)
                vacuum = {"ran": True, "before_bytes": before, "after_bytes": after}
        plan["dry_run"] = False
        plan["executed"] = True
        plan["backup_path"] = str(backup_path) if backup_path is not None else None
        plan["vacuum"].update(vacuum)
        return plan


__all__ = [
    "PROTECTED_COMPACTION_STATUSES",
    "catalog_size",
    "compact_catalog",
    "plan_catalog_compaction",
]
