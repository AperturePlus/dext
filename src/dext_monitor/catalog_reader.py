"""Read-only SQLite access for monitor APIs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SUPPORTED_SCHEMA_VERSIONS = frozenset({3, 4, 5, 6})
SUPPORTED_SCHEMA_VERSION = 6


class MonitorCatalogError(RuntimeError):
    """User-actionable monitor catalog read failure."""


def json_loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def serialize_row(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for key in ("settings_json", "summary_json", "row_counts_json", "details_json"):
        if key in value:
            value[key] = json_loads(value[key], {})
    for key in ("deactivation_eligible", "resolved", "active", "snapshot_reused"):
        if key in value:
            value[key] = bool(value[key])
    return value


class CatalogReader:
    """Tiny repository boundary around a read-only SQLite connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.is_file():
            raise MonitorCatalogError(f"catalog does not exist: {self.path}")
        connection = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=15000")
            yield connection
        finally:
            connection.close()

    def schema_version(self) -> int:
        with self.connect() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def require_supported_schema(self, connection: sqlite3.Connection) -> int:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise MonitorCatalogError(
                f"catalog schema version {version} is incompatible with monitor schema "
                f"{SUPPORTED_SCHEMA_VERSION}"
            )
        return version

    def table_exists(self, connection: sqlite3.Connection, name: str) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            is not None
        )


__all__ = [
    "CatalogReader",
    "MonitorCatalogError",
    "SUPPORTED_SCHEMA_VERSION",
    "json_loads",
    "serialize_row",
]
