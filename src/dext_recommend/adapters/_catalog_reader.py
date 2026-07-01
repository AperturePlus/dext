"""Catalog SQLite dialect seam: read raw active-build rows without importing dext_graph.

The reader returns plain sqlite3.Row mappings; domain mapping lives in _mappings.py.
Read-only URI mode + query_only pragma guarantees no writes.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, Protocol

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


_ACTIVE_SQL = """
SELECT id, status, curation_version, taxonomy_version,
       graph_schema_version, vector_schema_version, embedding_provider,
       embedding_model, embedding_fingerprint, embedding_dimension,
       started_at, finished_at
FROM graph_builds WHERE status='ACTIVE'
"""

_ACTIVE_ENTITY_IDS_SQL = """
SELECT entity_id FROM canonical_professors
WHERE build_id=? AND active=1 ORDER BY entity_id
"""

_SAMPLE_SQL = """
SELECT cp.entity_id, cp.role_status, cp.master_eligibility, cp.phd_eligibility,
       pp.profile_hash
FROM canonical_professors cp
LEFT JOIN professor_profiles pp
  ON pp.build_id=cp.build_id AND pp.entity_id=cp.entity_id
WHERE cp.build_id=? AND cp.entity_id IN (%s)
ORDER BY cp.entity_id
"""


class CatalogReleaseReader(Protocol):
    async def read_active_row(self) -> Mapping[str, Any] | None: ...

    async def read_sample_rows(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]: ...


class CatalogSqliteReader:
    def __init__(self, path: Path | str, *, timeout: float = 5.0) -> None:
        self._path = Path(path)
        self._timeout = timeout

    def _connect_ro(self, path: Path | None = None) -> sqlite3.Connection:
        return _connect_ro(path or self._path)

    async def read_active_row(self) -> Mapping[str, Any] | None:
        def _read() -> Mapping[str, Any] | None:
            with closing(self._connect_ro()) as conn:
                row = conn.execute(_ACTIVE_SQL).fetchone()
                if row is None:
                    return None
                data = dict(row)
                ids = [r["entity_id"] for r in conn.execute(
                    _ACTIVE_ENTITY_IDS_SQL, (data["id"],)
                )]
                data["active_entity_ids"] = tuple(ids)
                return data
        return await asyncio.wait_for(
            asyncio.to_thread(_read), self._timeout,
        )

    async def read_sample_rows(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[Mapping[str, Any], ...]:
        if not sample_ids:
            return ()
        placeholders = ",".join("?" for _ in sample_ids)
        sql = _SAMPLE_SQL % placeholders

        def _read() -> tuple[Mapping[str, Any], ...]:
            with closing(self._connect_ro()) as conn:
                return tuple(
                    dict(r) for r in conn.execute(sql, (build_id, *sample_ids))
                )
        return await asyncio.wait_for(
            asyncio.to_thread(_read), self._timeout,
        )


__all__ = ["CatalogReleaseReader", "CatalogSqliteReader"]
