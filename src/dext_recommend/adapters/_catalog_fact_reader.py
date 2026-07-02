"""Catalog SQLite dialect seam for R4 professor-fact reads.

Read-only URI mode + query_only pragma guarantees no writes. Every blocking
read is offloaded with asyncio.to_thread and bounded by asyncio.wait_for so
the event loop never blocks on SQLite I/O. Never imports dext_graph; the
published schema is pinned in _catalog_fact_schema.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

from dext_recommend.adapters._catalog_fact_schema import (
    COLUMN_LIST_SQL, MIN_FACT_CATALOG_SCHEMA_VERSION, REQUIRED_FACT_COLUMNS,
    REQUIRED_FACT_TABLES, SCHEMA_VERSION_SQL, TABLE_LIST_SQL,
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


__all__ = ["CatalogProfessorFactReader", "CatalogSqliteFactReader"]
