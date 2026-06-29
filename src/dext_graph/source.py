"""Strict read-only access to the current per-university wide-table schema."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path

from dext_graph.models import SourceInfo, SourceProfessor, ValueValidationError

_POINT_NAMESPACE = uuid.UUID("908bf342-5782-4f3c-9e35-f8f0c5d2979e")
_REQUIRED_COLUMNS = {
    "university_meta": {"name", "abbr", "crawl_status"},
    "professors": {
        "id",
        "name",
        "org_unit_name",
        "title",
        "research_areas",
        "publications",
        "bio",
    },
    "org_units": {"id", "name"},
    "professor_affiliations": {"professor_id", "org_unit_id"},
}


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_quiescent(path: Path) -> None:
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueValidationError(
            f"source DB has a non-empty WAL at {wal}; close the crawler and checkpoint/copy the DB first"
        )


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing_tables = sorted(set(_REQUIRED_COLUMNS) - tables)
    if missing_tables:
        raise ValueValidationError(
            "source DB is missing required tables: " + ", ".join(missing_tables)
        )
    for table, required in _REQUIRED_COLUMNS.items():
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        missing = sorted(required - columns)
        if missing:
            raise ValueValidationError(
                f"source DB table {table!r} is missing columns: {', '.join(missing)}"
            )


def inspect_source(path: str | Path, *, require_completed: bool = False) -> SourceInfo:
    db_path = Path(path).expanduser().resolve()
    if not db_path.is_file():
        raise ValueValidationError(f"source DB does not exist: {db_path}")
    _ensure_quiescent(db_path)
    before = file_sha256(db_path)
    with _connect_read_only(db_path) as connection:
        _validate_schema(connection)
        metadata = connection.execute(
            "SELECT name, abbr, crawl_status FROM university_meta ORDER BY id"
        ).fetchall()
        if len(metadata) != 1:
            raise ValueValidationError(
                f"source DB must contain exactly one university_meta row; found {len(metadata)}"
            )
        row_counts = {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in _REQUIRED_COLUMNS
        }
        coverage_row = connection.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN TRIM(COALESCE(research_areas, '')) <> '' THEN 1 ELSE 0 END),
              SUM(CASE WHEN TRIM(COALESCE(publications, '')) <> '' THEN 1 ELSE 0 END),
              SUM(CASE WHEN TRIM(COALESCE(bio, '')) <> '' THEN 1 ELSE 0 END),
              SUM(CASE WHEN TRIM(COALESCE(research_areas, '')) <> ''
                         OR TRIM(COALESCE(publications, '')) <> ''
                         OR TRIM(COALESCE(bio, '')) <> '' THEN 1 ELSE 0 END)
            FROM professors
            """
        ).fetchone()
    _ensure_quiescent(db_path)
    after = file_sha256(db_path)
    if before != after:
        raise ValueValidationError("source DB changed while it was being inspected")

    meta = metadata[0]
    info = SourceInfo(
        path=str(db_path),
        sha256=before,
        university=str(meta["name"]),
        abbreviation=meta["abbr"],
        crawl_status=str(meta["crawl_status"]),
        row_counts=row_counts,
        coverage={
            "professors": int(coverage_row[0] or 0),
            "with_research_areas": int(coverage_row[1] or 0),
            "with_publications": int(coverage_row[2] or 0),
            "with_bio": int(coverage_row[3] or 0),
            "with_semantic_content": int(coverage_row[4] or 0),
            "without_semantic_content": int((coverage_row[0] or 0) - (coverage_row[4] or 0)),
        },
    )
    if require_completed and info.crawl_status != "completed":
        raise ValueValidationError(
            f"source DB crawl_status={info.crawl_status!r}; expected 'completed' "
            f"(university={info.university!r}, professors={info.row_counts['professors']})"
        )
    return info


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def iter_professor_batches(
    path: str | Path,
    source: SourceInfo,
    *,
    batch_size: int = 100,
) -> Iterator[list[SourceProfessor]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    db_path = Path(path).expanduser().resolve()
    _ensure_quiescent(db_path)
    if file_sha256(db_path) != source.sha256:
        raise ValueValidationError("source DB hash no longer matches the inspected input")

    connection = _connect_read_only(db_path)
    last_id = 0
    try:
        connection.execute("BEGIN")
        while True:
            rows = connection.execute(
                """
                SELECT p.id, p.name, p.org_unit_name, p.title,
                       p.research_areas, p.publications, p.bio,
                       (
                         SELECT GROUP_CONCAT(ordered.name, char(31))
                         FROM (
                           SELECT ou.name AS name
                           FROM professor_affiliations pa
                           JOIN org_units ou ON ou.id = pa.org_unit_id
                           WHERE pa.professor_id = p.id
                           ORDER BY ou.name COLLATE BINARY
                         ) AS ordered
                       ) AS affiliation_names
                FROM professors p
                WHERE p.id > ?
                ORDER BY p.id
                LIMIT ?
                """,
                (last_id, batch_size),
            ).fetchall()
            if not rows:
                break
            batch: list[SourceProfessor] = []
            for row in rows:
                source_id = int(row["id"])
                source_row_key = f"{source.sha256}:professors:{source_id}"
                affiliations = (
                    str(row["affiliation_names"]).split(chr(31))
                    if row["affiliation_names"]
                    else []
                )
                fallback = _clean(row["org_unit_name"])
                if fallback:
                    affiliations.append(fallback)
                org_units = tuple(sorted({item.strip() for item in affiliations if item.strip()}))
                batch.append(
                    SourceProfessor(
                        source_id=source_id,
                        source_row_key=source_row_key,
                        point_id=str(uuid.uuid5(_POINT_NAMESPACE, source_row_key)),
                        name=str(row["name"]),
                        university=source.university,
                        org_units=org_units,
                        title=_clean(row["title"]),
                        research_areas=_clean(row["research_areas"]),
                        publications=_clean(row["publications"]),
                        bio=_clean(row["bio"]),
                    )
                )
            last_id = int(rows[-1]["id"])
            yield batch
        connection.execute("COMMIT")
    finally:
        connection.close()
    _ensure_quiescent(db_path)
    if file_sha256(db_path) != source.sha256:
        raise ValueValidationError("source DB changed while professors were being read")


__all__ = ["file_sha256", "inspect_source", "iter_professor_batches"]
