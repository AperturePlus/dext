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
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class CatalogProfessorDetailRows:
    canonical: Mapping[str, Any]            # canonical_professors row (dict)
    profile_payload: dict[str, Any]          # parsed professor_profiles.payload_json
    profile_hash: str | None
    university_name: str | None              # from build_source_tasks by university_id
    observations: tuple[Mapping[str, Any], ...]   # active professor_observations rows
    statements: tuple[Mapping[str, Any], ...]
    mentions: tuple[Mapping[str, Any], ...]
    topic_links: tuple[Mapping[str, Any], ...]  # joined with topics.canonical_name
    findings: tuple[Mapping[str, Any], ...]      # unresolved, this build, this entity
    source_urls: tuple[Mapping[str, Any], ...]   # canonical source url rows w/ fetched_at


_CANONICAL_SQL = """
SELECT entity_id, name AS display_name, title_raw, title_family, role_status,
       role_reason_codes, master_eligibility, phd_eligibility,
       research_areas_text AS research_areas, bio, email, phone,
       profile_url, external_url, active, completeness
FROM canonical_professors
WHERE build_id=? AND entity_id=? AND active=1 AND role_status!='excluded'
"""

_PROFILE_SQL = """
SELECT profile_hash, payload_json
FROM professor_profiles
WHERE build_id=? AND entity_id=?
"""

_OBSERVATIONS_SQL = """
SELECT po.id, po.university_id, po.source_url, po.source_document_id,
       po.payload_json AS observation_payload_json
FROM professor_observations po
JOIN entity_observations eo
  ON eo.observation_id=po.id AND eo.build_id=? AND eo.entity_id=?
WHERE po.active=1
ORDER BY po.id
"""

_STATEMENTS_SQL = """
SELECT id, observation_id, normalized_text, language
FROM research_statements
WHERE build_id=? AND entity_id=?
ORDER BY id
"""

_MENTIONS_SQL = """
SELECT id, observation_id, normalized_text, doi, year, confidence, needs_review
FROM publication_mentions
WHERE build_id=? AND entity_id=?
ORDER BY id, observation_id
"""

_TOPIC_LINKS_SQL = """
SELECT l.statement_id, l.topic_id, l.relation_type, l.evidence_span,
       l.review_status, l.provenance_ref, t.canonical_name, t.kind, t.status
FROM statement_topic_links l
JOIN topics t
  ON t.taxonomy_version=l.taxonomy_version AND t.id=l.topic_id
JOIN research_statements s
  ON s.build_id=l.build_id AND s.id=l.statement_id
WHERE s.build_id=? AND s.entity_id=?
  AND t.status='active'
ORDER BY l.statement_id, l.topic_id
"""

_FINDINGS_SQL = """
SELECT id, severity, code, details_json, resolved
FROM quality_findings
WHERE build_id=? AND entity_id=? AND resolved=0
ORDER BY id
"""

_UNIVERSITY_NAME_SQL = """
SELECT university_name FROM build_source_tasks WHERE build_id=? AND university_id=?
"""

_SOURCE_URLS_SQL = """
SELECT DISTINCT po.source_url, po.source_document_id, sd.fetched_at
FROM professor_observations po
JOIN entity_observations eo
  ON eo.observation_id=po.id AND eo.build_id=?
LEFT JOIN source_documents sd ON sd.id=po.source_document_id
WHERE eo.entity_id=? AND po.active=1 AND po.source_url IS NOT NULL
ORDER BY po.source_url
"""


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

    async def read_detail_rows(
        self, build_id: str, entity_id: str,
    ) -> CatalogProfessorDetailRows | None:
        def _read() -> CatalogProfessorDetailRows | None:
            with closing(self._connect_ro()) as conn:
                canon = conn.execute(
                    _CANONICAL_SQL, (build_id, entity_id),
                ).fetchone()
                if canon is None:
                    return None
                canon = dict(canon)
                profile_row = conn.execute(
                    _PROFILE_SQL, (build_id, entity_id),
                ).fetchone()
                profile_payload: dict[str, Any] = {}
                profile_hash: str | None = None
                if profile_row is not None:
                    profile_hash = str(profile_row["profile_hash"] or "") or None
                    profile_payload = _safe_json(
                        profile_row["payload_json"], entity_id=entity_id,
                    )
                university_name: str | None = None
                uni_id = profile_payload.get("university_id")
                if uni_id is not None:
                    u = conn.execute(
                        _UNIVERSITY_NAME_SQL, (build_id, str(uni_id)),
                    ).fetchone()
                    if u is not None:
                        university_name = u["university_name"]
                observations = tuple(
                    dict(r) for r in conn.execute(
                        _OBSERVATIONS_SQL, (build_id, entity_id),
                    )
                )
                statements = tuple(
                    dict(r) for r in conn.execute(_STATEMENTS_SQL, (build_id, entity_id))
                )
                mentions = tuple(
                    dict(r) for r in conn.execute(_MENTIONS_SQL, (build_id, entity_id))
                )
                topic_links = tuple(
                    dict(r) for r in conn.execute(_TOPIC_LINKS_SQL, (build_id, entity_id))
                )
                findings = tuple(
                    dict(r) for r in conn.execute(_FINDINGS_SQL, (build_id, entity_id))
                )
                source_urls = tuple(
                    dict(r) for r in conn.execute(_SOURCE_URLS_SQL, (build_id, entity_id))
                )
                return CatalogProfessorDetailRows(
                    canonical=canon,
                    profile_payload=profile_payload,
                    profile_hash=profile_hash,
                    university_name=university_name,
                    observations=observations,
                    statements=statements,
                    mentions=mentions,
                    topic_links=topic_links,
                    findings=findings,
                    source_urls=source_urls,
                )
        return await asyncio.wait_for(asyncio.to_thread(_read), self._timeout)


__all__ = [
    "CatalogProfessorDetailRows", "CatalogProfessorFactReader",
    "CatalogSqliteFactReader",
]
