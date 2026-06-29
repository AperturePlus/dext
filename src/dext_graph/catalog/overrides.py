"""Audited field/role overrides and explicit entity merges."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import (
    CatalogError,
    backup_existing_catalog,
    catalog_write_lock,
    connect_catalog,
    initialize_catalog,
    json_dumps,
    utcnow_iso,
)
from dext_graph.catalog.ids import uuid7


def _next_version(
    connection: sqlite3.Connection, entity_id: str, kind: str, field_name: str | None
) -> int:
    row = connection.execute(
        "SELECT MAX(version) FROM curation_overrides WHERE entity_id=? AND kind=? "
        "AND field_name IS ?",
        (entity_id, kind, field_name),
    ).fetchone()
    return int(row[0] or 0) + 1


def _insert_override(
    connection: sqlite3.Connection,
    *,
    entity_id: str,
    kind: str,
    field_name: str | None,
    value: Any,
    actor: str,
    reason: str,
    supersedes_id: str | None,
) -> str:
    if not actor.strip() or not reason.strip():
        raise ValueError("actor and reason are required")
    entity = connection.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone()
    if entity is None:
        raise CatalogError(f"unknown entity: {entity_id}")
    if supersedes_id is not None:
        previous = connection.execute(
            "SELECT entity_id FROM curation_overrides WHERE id=? AND active=1",
            (supersedes_id,),
        ).fetchone()
        if previous is None or previous[0] != entity_id:
            raise CatalogError("superseded override is missing, inactive, or belongs to another entity")
        connection.execute("UPDATE curation_overrides SET active=0 WHERE id=?", (supersedes_id,))
    override_id = uuid7()
    connection.execute(
        """
        INSERT INTO curation_overrides(
          id, entity_id, kind, field_name, value_json, actor, reason,
          version, supersedes_id, active, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            override_id,
            entity_id,
            kind,
            field_name,
            json_dumps(value),
            actor.strip(),
            reason.strip(),
            _next_version(connection, entity_id, kind, field_name),
            supersedes_id,
            utcnow_iso(),
        ),
    )
    return override_id


def record_override(
    catalog_path: str | Path,
    *,
    entity_id: str,
    kind: str,
    value: Any,
    actor: str,
    reason: str,
    field_name: str | None = None,
    supersedes_id: str | None = None,
) -> str:
    if kind not in {"field", "role"}:
        raise ValueError("kind must be 'field' or 'role'")
    if kind == "field" and not field_name:
        raise ValueError("field overrides require field_name")
    if kind == "role" and value not in {"included", "review", "excluded"}:
        raise ValueError("role override must be included, review, or excluded")
    path = Path(catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        connection = connect_catalog(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            override_id = _insert_override(
                connection,
                entity_id=entity_id,
                kind=kind,
                field_name=field_name,
                value=value,
                actor=actor,
                reason=reason,
                supersedes_id=supersedes_id,
            )
            connection.commit()
            return override_id
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


def _merge_target(connection: sqlite3.Connection, entity_id: str) -> str:
    seen: set[str] = set()
    current = entity_id
    while True:
        if current in seen:
            raise CatalogError("entity merge cycle detected")
        seen.add(current)
        row = connection.execute(
            "SELECT status, merged_into_id FROM entities WHERE id=?", (current,)
        ).fetchone()
        if row is None:
            raise CatalogError(f"unknown entity: {current}")
        if row["status"] != "merged" or row["merged_into_id"] is None:
            return current
        current = str(row["merged_into_id"])


def merge_entities(
    catalog_path: str | Path,
    *,
    source_entity_id: str,
    target_entity_id: str,
    actor: str,
    reason: str,
) -> str:
    path = Path(catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        connection = connect_catalog(path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            source = _merge_target(connection, source_entity_id)
            target = _merge_target(connection, target_entity_id)
            if source != source_entity_id:
                raise CatalogError("source entity is already merged")
            if source == target:
                raise CatalogError("source and target resolve to the same entity")
            cursor = target
            while True:
                if cursor == source:
                    raise CatalogError("merge would create a cycle")
                row = connection.execute(
                    "SELECT merged_into_id FROM entities WHERE id=?", (cursor,)
                ).fetchone()
                if row is None or row[0] is None:
                    break
                cursor = str(row[0])
            override_id = _insert_override(
                connection,
                entity_id=source,
                kind="merge",
                field_name=None,
                value={"target_entity_id": target},
                actor=actor,
                reason=reason,
                supersedes_id=None,
            )
            connection.execute(
                "UPDATE entities SET status='merged', merged_into_id=?, updated_at=? WHERE id=?",
                (target, utcnow_iso(), source),
            )
            connection.commit()
            return override_id
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


__all__ = ["merge_entities", "record_override"]
