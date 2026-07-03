"""Read-only source snapshot inspection and bounded legacy row iteration."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from dext_graph.catalog.db import (
    CatalogError,
    file_sha256,
    online_backup,
    remove_sqlite_copy,
    sqlite_sidecar_paths,
    verify_sqlite,
)
from dext_graph.catalog.ids import snapshot_id

_KEY_TABLES = (
    "university_meta",
    "org_units",
    "professors",
    "professor_affiliations",
    "crawl_page_cache",
    "crawl_graph_nodes",
    "crawl_extraction_attempts",
)


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _connect_read_only(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    _assert_snapshot_self_contained(resolved)
    connection = sqlite3.connect(
        f"{resolved.as_uri()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def _assert_snapshot_self_contained(path: Path) -> None:
    if not path.is_file():
        raise CatalogError(f"source snapshot does not exist: {path}")
    for sidecar in sqlite_sidecar_paths(path):
        if sidecar.name.endswith(("-wal", "-journal")) and sidecar.is_file():
            if sidecar.stat().st_size:
                raise CatalogError(
                    f"immutable source snapshot has a non-empty sidecar: {sidecar}"
                )


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(row[1] for row in connection.execute(f"PRAGMA table_info({_quoted(table)})"))


@dataclass(frozen=True)
class SnapshotInspection:
    university_name: str
    abbreviation: str | None
    crawl_status: str
    schema_version: int
    row_counts: dict[str, int | None]
    tables: frozenset[str]
    columns: dict[str, tuple[str, ...]]
    graph_blocking_rows: int | None
    org_blocking_rows: int | None

    @property
    def missing_optional_tables(self) -> tuple[str, ...]:
        return tuple(sorted(set(_KEY_TABLES[1:]) - self.tables))

    @property
    def incomplete_capabilities(self) -> tuple[str, ...]:
        issues: list[str] = []
        if "homepage" not in self.columns.get("professors", ()):
            issues.append("professors.homepage")
        if "crawl_page_cache" in self.tables and not self.has_page_cache:
            issues.append("crawl_page_cache.columns")
        affiliation_tables = {"professor_affiliations", "org_units"}
        if affiliation_tables.issubset(self.tables) and not self.has_affiliations:
            issues.append("professor_affiliations.columns")
        if "crawl_graph_nodes" in self.tables and self.graph_blocking_rows is None:
            issues.append("crawl_graph_nodes.status")
        if "org_units" in self.tables and self.org_blocking_rows is None:
            issues.append("org_units.status")
        return tuple(issues)

    @property
    def has_page_cache(self) -> bool:
        required = {"url", "content_hash", "title", "text_snapshot", "updated_at"}
        return required.issubset(self.columns.get("crawl_page_cache", ()))

    @property
    def has_affiliations(self) -> bool:
        return {
            "professor_id",
            "org_unit_id",
        }.issubset(self.columns.get("professor_affiliations", ())) and {
            "id",
            "name",
        }.issubset(self.columns.get("org_units", ()))

    def deactivation_state_is_complete(self) -> bool:
        return (
            self.graph_blocking_rows == 0
            and self.org_blocking_rows == 0
            and "crawl_graph_nodes" in self.tables
            and "org_units" in self.tables
        )


@dataclass(frozen=True)
class SnapshotResult:
    id: str
    file_hash: str
    path: Path
    inspection: SnapshotInspection
    reused_file: bool


@dataclass(frozen=True)
class LegacySourceRow:
    source_id: int
    payload: dict[str, Any]
    affiliations: tuple[dict[str, Any], ...]
    primary_org_unit_id: int | None
    page_url: str | None
    page_content_hash: str | None
    page_title: str | None
    page_text: str | None
    page_fetched_at: str | None


def inspect_snapshot(path: str | Path) -> SnapshotInspection:
    with closing(_connect_read_only(path)) as connection:
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick != "ok":
            raise CatalogError(f"source snapshot quick_check failed: {quick}")
        tables = frozenset(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        )
        missing_required = sorted({"university_meta", "professors"} - tables)
        if missing_required:
            raise CatalogError(
                "source snapshot is missing required tables: " + ", ".join(missing_required)
            )
        columns = {table: _table_columns(connection, table) for table in tables}
        missing_professor = sorted({"id", "name"} - set(columns["professors"]))
        if missing_professor:
            raise CatalogError(
                "source professors table is missing columns: " + ", ".join(missing_professor)
            )
        meta_columns = set(columns["university_meta"])
        if not {"name", "crawl_status"}.issubset(meta_columns):
            raise CatalogError("university_meta must contain name and crawl_status")
        select_meta = ["name", "crawl_status"]
        select_meta.append("abbr" if "abbr" in meta_columns else "NULL AS abbr")
        select_meta.append(
            "schema_version" if "schema_version" in meta_columns else "0 AS schema_version"
        )
        metadata = connection.execute(
            "SELECT " + ", ".join(select_meta) + " FROM university_meta ORDER BY rowid"
        ).fetchall()
        if len(metadata) != 1:
            raise CatalogError(
                f"source snapshot must contain exactly one university_meta row; found {len(metadata)}"
            )
        row_counts: dict[str, int | None] = {}
        for table in _KEY_TABLES:
            row_counts[table] = (
                int(connection.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0])
                if table in tables
                else None
            )
        graph_blocking: int | None = None
        if {"status"}.issubset(columns.get("crawl_graph_nodes", ())):
            graph_blocking = int(
                connection.execute(
                    "SELECT COUNT(*) FROM crawl_graph_nodes "
                    "WHERE status IN ('pending','in_progress','retry','failed')"
                ).fetchone()[0]
            )
        org_blocking: int | None = None
        if {"status"}.issubset(columns.get("org_units", ())):
            org_blocking = int(
                connection.execute(
                    "SELECT COUNT(*) FROM org_units "
                    "WHERE status IN ('pending','in_progress','retry','failed')"
                ).fetchone()[0]
            )
        meta = metadata[0]
        return SnapshotInspection(
            university_name=str(meta["name"]),
            abbreviation=str(meta["abbr"]) if meta["abbr"] is not None else None,
            crawl_status=str(meta["crawl_status"]),
            schema_version=int(meta["schema_version"] or 0),
            row_counts=row_counts,
            tables=tables,
            columns=columns,
            graph_blocking_rows=graph_blocking,
            org_blocking_rows=org_blocking,
        )


async def create_source_snapshot(
    source_path: str | Path,
    snapshot_root: str | Path,
    *,
    university_id: str,
    abbr: str,
    build_id: str,
    progress_hook=None,
) -> SnapshotResult:
    root = Path(snapshot_root).expanduser().resolve() / abbr
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".{build_id}.tmp.db"
    remove_sqlite_copy(temporary)
    try:
        await asyncio.to_thread(
            online_backup,
            source_path,
            temporary,
            pages=256,
            progress_hook=progress_hook,
        )
        verify_sqlite(temporary)
        digest = file_sha256(temporary)
        final_path = root / f"{digest}.db"
        reused = final_path.exists()
        if reused:
            _assert_snapshot_self_contained(final_path)
            if file_sha256(final_path) != digest:
                raise CatalogError(f"existing source snapshot hash mismatch: {final_path}")
        else:
            for sidecar in sqlite_sidecar_paths(final_path):
                if sidecar.is_file() and sidecar.stat().st_size:
                    raise CatalogError(
                        f"refusing to publish over a non-empty orphan sidecar: {sidecar}"
                    )
                sidecar.unlink(missing_ok=True)
            os.replace(temporary, final_path)
        _assert_snapshot_self_contained(final_path)
        if file_sha256(final_path) != digest:
            raise CatalogError(f"published source snapshot hash mismatch: {final_path}")
        inspection = inspect_snapshot(final_path)
        return SnapshotResult(
            id=snapshot_id(university_id, digest),
            file_hash=digest,
            path=final_path,
            inspection=inspection,
            reused_file=reused,
        )
    finally:
        remove_sqlite_copy(temporary)


def iter_legacy_batches(
    snapshot_path: str | Path,
    inspection: SnapshotInspection,
    *,
    last_key: int = 0,
    batch_size: int = 100,
) -> Iterator[list[LegacySourceRow]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    professor_columns = inspection.columns["professors"]
    projected = ", ".join(
        f"p.{_quoted(column)} AS {_quoted('p__' + column)}"
        for column in professor_columns
    )
    homepage_available = "homepage" in professor_columns
    page_join = ""
    page_projection = (
        "NULL AS pc_url, NULL AS pc_content_hash, NULL AS pc_title, "
        "NULL AS pc_text_snapshot, NULL AS pc_updated_at"
    )
    if inspection.has_page_cache and homepage_available:
        page_join = " LEFT JOIN crawl_page_cache pc ON pc.url = p.homepage"
        page_projection = (
            "pc.url AS pc_url, pc.content_hash AS pc_content_hash, pc.title AS pc_title, "
            "pc.text_snapshot AS pc_text_snapshot, pc.updated_at AS pc_updated_at"
        )
    connection = _connect_read_only(snapshot_path)
    current_key = last_key
    try:
        connection.execute("BEGIN")
        while True:
            rows = connection.execute(
                f"SELECT {projected}, {page_projection} FROM professors p{page_join} "
                "WHERE p.id > ? ORDER BY p.id LIMIT ?",
                (current_key, batch_size),
            ).fetchall()
            if not rows:
                break
            source_ids = [int(row["p__id"]) for row in rows]
            affiliations: dict[int, list[dict[str, Any]]] = {value: [] for value in source_ids}
            if inspection.has_affiliations:
                placeholders = ",".join("?" for _ in source_ids)
                for affiliation in connection.execute(
                    "SELECT pa.professor_id, ou.id AS org_unit_id, ou.name AS org_unit_name "
                    "FROM professor_affiliations pa JOIN org_units ou ON ou.id=pa.org_unit_id "
                    f"WHERE pa.professor_id IN ({placeholders}) "
                    "ORDER BY pa.professor_id, ou.id",
                    source_ids,
                ):
                    affiliations[int(affiliation["professor_id"])].append(
                        {
                            "org_unit_id": int(affiliation["org_unit_id"]),
                            "org_unit_name": str(affiliation["org_unit_name"]),
                        }
                    )
            names = sorted(
                {
                    str(row["p__org_unit_name"])
                    for row in rows
                    if "org_unit_name" in professor_columns
                    and row["p__org_unit_name"] is not None
                    and str(row["p__org_unit_name"]).strip()
                }
            )
            org_ids_by_name: dict[str, int] = {}
            if names and {"id", "name"}.issubset(inspection.columns.get("org_units", ())):
                placeholders = ",".join("?" for _ in names)
                for org_row in connection.execute(
                    f"SELECT id, name FROM org_units WHERE name IN ({placeholders}) ORDER BY id",
                    names,
                ):
                    org_ids_by_name.setdefault(str(org_row["name"]), int(org_row["id"]))
            batch: list[LegacySourceRow] = []
            for row in rows:
                payload = {column: row["p__" + column] for column in professor_columns}
                source_id = int(payload["id"])
                row_affiliations = tuple(affiliations[source_id])
                primary_org_unit_id = None
                raw_org_name = payload.get("org_unit_name")
                if raw_org_name is not None:
                    primary_org_unit_id = org_ids_by_name.get(str(raw_org_name))
                if primary_org_unit_id is None and len(row_affiliations) == 1:
                    primary_org_unit_id = int(row_affiliations[0]["org_unit_id"])
                batch.append(
                    LegacySourceRow(
                        source_id=source_id,
                        payload=payload,
                        affiliations=row_affiliations,
                        primary_org_unit_id=primary_org_unit_id,
                        page_url=str(row["pc_url"]) if row["pc_url"] is not None else None,
                        page_content_hash=(
                            str(row["pc_content_hash"])
                            if row["pc_content_hash"] is not None
                            else None
                        ),
                        page_title=(str(row["pc_title"]) if row["pc_title"] is not None else None),
                        page_text=(
                            str(row["pc_text_snapshot"])
                            if row["pc_text_snapshot"] is not None
                            else None
                        ),
                        page_fetched_at=(
                            str(row["pc_updated_at"])
                            if row["pc_updated_at"] is not None
                            else None
                        ),
                    )
                )
            current_key = source_ids[-1]
            yield batch
        connection.execute("COMMIT")
    finally:
        connection.close()


__all__ = [
    "LegacySourceRow",
    "SnapshotInspection",
    "SnapshotResult",
    "create_source_snapshot",
    "inspect_snapshot",
    "iter_legacy_batches",
]
