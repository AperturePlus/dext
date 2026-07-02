"""Catalog SQLite dialect seam for R4 professor-fact reads.

Read-only URI mode + query_only pragma guarantees no writes. Every blocking
read is offloaded with asyncio.to_thread and bounded by asyncio.wait_for so
the event loop never blocks on SQLite I/O. Never imports dext_graph; the
published schema is pinned in _catalog_fact_schema.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from dext_recommend.adapters._catalog_fact_schema import (
    COLUMN_LIST_SQL, FACT_SQLITE_PARAM_LIMIT, MIN_FACT_CATALOG_SCHEMA_VERSION,
    REQUIRED_FACT_COLUMNS, REQUIRED_FACT_TABLES, SCHEMA_VERSION_SQL,
    TABLE_LIST_SQL,
)
from dext_recommend.ports.release_readback import ReadinessSourceError


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ReadinessSourceError("catalog", f"catalog not found: {resolved}")
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


class CatalogProfessorFactReader(Protocol):
    async def check_capability(self) -> int: ...
    async def read_fact_rows(
        self, build_id: str, entity_ids: list[str],
    ) -> tuple[Mapping[str, Any], ...]: ...


_FACT_ROWS_SQL = """
SELECT cp.entity_id, cp.name AS display_name, cp.title_family,
       cp.role_status, cp.master_eligibility, cp.phd_eligibility,
       cp.profile_url, cp.profile_url AS external_url,
       pp.profile_hash, pp.payload_json AS profile_payload_json,
       cp.research_areas_text
FROM canonical_professors cp
LEFT JOIN professor_profiles pp
  ON pp.build_id=cp.build_id AND pp.entity_id=cp.entity_id
WHERE cp.build_id=? AND cp.active=1 AND cp.role_status!='excluded'
  AND cp.entity_id IN (%s)
ORDER BY cp.entity_id
"""


def _safe_json(payload: str | None, *, entity_id: str) -> dict[str, Any]:
    if payload is None:
        return {}
    try:
        value = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ReadinessSourceError(
            "catalog", f"invalid profile payload_json for entity {entity_id}",
        ) from exc
    if not isinstance(value, dict):
        raise ReadinessSourceError(
            "catalog", f"profile payload_json not an object for entity {entity_id}",
        )
    return value


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v is not None)
    return (str(value),)


class CatalogSqliteFactReader:
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        self._path = Path(path)
        self._timeout = timeout

    def _connect_ro(self) -> sqlite3.Connection:
        return _connect_ro(self._path)

    async def check_capability(self) -> int:
        def _check() -> int:
            with closing(self._connect_ro()) as conn:
                version_row = conn.execute(SCHEMA_VERSION_SQL).fetchone()
                if version_row is None:
                    raise ReadinessSourceError(
                        "catalog", "schema_version not recorded in catalog_meta",
                    )
                try:
                    version = int(version_row[0])
                except (TypeError, ValueError) as exc:
                    raise ReadinessSourceError(
                        "catalog", f"unparseable schema_version: {version_row[0]!r}",
                    ) from exc
                if version < MIN_FACT_CATALOG_SCHEMA_VERSION:
                    raise ReadinessSourceError(
                        "catalog",
                        f"schema_version {version} < required "
                        f"{MIN_FACT_CATALOG_SCHEMA_VERSION}",
                    )
                present = {
                    str(r[0]) for r in conn.execute(TABLE_LIST_SQL)
                }
                missing_tables = [
                    t for t in REQUIRED_FACT_TABLES if t not in present
                ]
                if missing_tables:
                    raise ReadinessSourceError(
                        "catalog",
                        f"missing required tables: {missing_tables}",
                    )
                for table, required_cols in REQUIRED_FACT_COLUMNS.items():
                    actual = {
                        str(r[1]) for r in conn.execute(
                            COLUMN_LIST_SQL.format(table=table)
                        )
                    }
                    missing_cols = [c for c in required_cols if c not in actual]
                    if missing_cols:
                        raise ReadinessSourceError(
                            "catalog",
                            f"table {table} missing columns: {missing_cols}",
                        )
                return version
        return await asyncio.wait_for(
            asyncio.to_thread(_check), self._timeout,
        )

    async def read_fact_rows(
        self, build_id: str, entity_ids: list[str],
    ) -> tuple[Mapping[str, Any], ...]:
        if not entity_ids:
            return ()
        # reserve 1 placeholder for build_id
        chunk_size = max(1, FACT_SQLITE_PARAM_LIMIT - 1)

        def _read_chunk(chunk: tuple[str, ...]) -> tuple[Mapping[str, Any], ...]:
            placeholders = ",".join("?" for _ in chunk)
            sql = _FACT_ROWS_SQL % placeholders
            with closing(self._connect_ro()) as conn:
                rows = conn.execute(sql, (build_id, *chunk)).fetchall()
            out: list[Mapping[str, Any]] = []
            for raw in rows:
                d = dict(raw)
                payload = _safe_json(
                    d.pop("profile_payload_json", None),
                    entity_id=d["entity_id"],
                )
                d["university_id"] = payload.get("university_id")
                d["org_unit_ids"] = _coerce_str_tuple(payload.get("org_unit_ids"))
                d["city_name"] = payload.get("city")
                d["topic_ids"] = _coerce_str_tuple(payload.get("topic_ids"))
                if not d.get("profile_hash"):
                    d["profile_hash"] = None
                ph = payload.get("profile_hash")
                if ph and not d.get("profile_hash"):
                    d["profile_hash"] = str(ph)
                out.append(d)
            return tuple(out)

        async def _read_all() -> tuple[Mapping[str, Any], ...]:
            ordered = tuple(dict.fromkeys(entity_ids))
            results: list[Mapping[str, Any]] = []
            for i in range(0, len(ordered), chunk_size):
                chunk = ordered[i:i + chunk_size]
                results.extend(await asyncio.to_thread(_read_chunk, chunk))
            return tuple(results)

        return await asyncio.wait_for(_read_all(), self._timeout)


__all__ = ["CatalogProfessorFactReader", "CatalogSqliteFactReader"]
