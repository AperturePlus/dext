"""Shared, deterministic OrgUnit resolution for graph and vector projections."""

from __future__ import annotations

import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from dext_graph.catalog.ids import canonical_source_url
from dext_graph.catalog.db import json_loads


@dataclass(frozen=True)
class OrgUnitRecord:
    logical_id: str
    graph_key: str
    university_id: str
    source_id: int
    name: str
    url: str | None
    kind: str | None


@dataclass(frozen=True)
class ResolvedAffiliation:
    entity_id: str
    observation_id: str
    provenance_grade: str
    org_unit: OrgUnitRecord


def _text(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def org_logical_id(university_id: str, url: object, name: object) -> str:
    canonical = canonical_source_url(url)
    identity = canonical or _text(name).casefold()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{university_id}\0{identity}"))


def load_org_records(
    connection: sqlite3.Connection, build_id: str
) -> dict[tuple[str, int], OrgUnitRecord]:
    records: dict[tuple[str, int], OrgUnitRecord] = {}
    tasks = connection.execute(
        """
        SELECT t.university_id,s.snapshot_path
        FROM build_source_tasks t
        JOIN source_snapshots s ON s.id=t.source_snapshot_id
        WHERE t.build_id=? AND t.status='COMPLETED'
        ORDER BY t.university_id
        """,
        (build_id,),
    )
    for task in tasks:
        university_id = str(task["university_id"])
        path = Path(task["snapshot_path"]).resolve()
        source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='org_units'"
            ).fetchone() is None:
                continue
            columns = {
                str(row[1]) for row in source.execute("PRAGMA table_info(org_units)")
            }
            url = "url" if "url" in columns else "NULL AS url"
            kind = "kind" if "kind" in columns else "NULL AS kind"
            for row in source.execute(
                f"SELECT id,name,{url},{kind} FROM org_units ORDER BY id"
            ):
                logical_id = org_logical_id(university_id, row["url"], row["name"])
                source_id = int(row["id"])
                records[(university_id, source_id)] = OrgUnitRecord(
                    logical_id=logical_id,
                    graph_key=f"{build_id}:{logical_id}",
                    university_id=university_id,
                    source_id=source_id,
                    name=str(row["name"]),
                    url=str(row["url"]) if row["url"] is not None else None,
                    kind=str(row["kind"]) if row["kind"] is not None else None,
                )
        finally:
            source.close()
    return records


def iter_resolved_affiliations(
    connection: sqlite3.Connection,
    build_id: str,
    *,
    entity_id: str | None = None,
) -> Iterator[ResolvedAffiliation]:
    orgs = load_org_records(connection, build_id)
    params: list[object] = [build_id, build_id]
    entity_clause = ""
    if entity_id is not None:
        entity_clause = " AND eo.entity_id=?"
        params.append(entity_id)
    query = f"""
        SELECT eo.entity_id,o.id AS observation_id,o.university_id,
               o.org_unit_source_id,o.payload_json,o.provenance_grade
        FROM entity_observations eo
        JOIN professor_observations o ON o.id=eo.observation_id AND o.active=1
        JOIN canonical_professors cp
          ON cp.entity_id=eo.entity_id AND cp.build_id=? AND cp.active=1
        WHERE eo.build_id=?{entity_clause}
        ORDER BY eo.entity_id,o.id
    """
    for row in connection.execute(query, params):
        payload = json_loads(row["payload_json"], {})
        affiliations = payload.get("affiliations") or []
        if not affiliations:
            affiliations = [
                {
                    "org_unit_id": row["org_unit_source_id"],
                    "org_unit_name": payload.get("org_unit_name"),
                }
            ]
        seen: set[str] = set()
        for affiliation in affiliations:
            if not isinstance(affiliation, dict):
                continue
            source_id = affiliation.get("org_unit_id")
            if source_id is None:
                source_id = row["org_unit_source_id"]
            try:
                key = (str(row["university_id"]), int(source_id))
            except (TypeError, ValueError):
                continue
            record = orgs.get(key)
            if record is None or record.logical_id in seen:
                continue
            seen.add(record.logical_id)
            yield ResolvedAffiliation(
                entity_id=str(row["entity_id"]),
                observation_id=str(row["observation_id"]),
                provenance_grade=str(row["provenance_grade"]),
                org_unit=record,
            )


def entity_org_unit_ids(
    connection: sqlite3.Connection, build_id: str, entity_id: str
) -> list[str]:
    return sorted(
        {
            affiliation.org_unit.logical_id
            for affiliation in iter_resolved_affiliations(
                connection, build_id, entity_id=entity_id
            )
        }
    )


def entity_org_unit_map(
    connection: sqlite3.Connection, build_id: str
) -> dict[str, list[str]]:
    grouped: dict[str, set[str]] = {}
    for affiliation in iter_resolved_affiliations(connection, build_id):
        grouped.setdefault(affiliation.entity_id, set()).add(
            affiliation.org_unit.logical_id
        )
    return {
        entity_id: sorted(org_ids) for entity_id, org_ids in grouped.items()
    }


__all__ = [
    "OrgUnitRecord",
    "ResolvedAffiliation",
    "entity_org_unit_ids",
    "entity_org_unit_map",
    "iter_resolved_affiliations",
    "load_org_records",
    "org_logical_id",
]
