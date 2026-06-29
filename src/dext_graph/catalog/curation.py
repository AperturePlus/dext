"""Stage-2 streaming identity resolution and canonical materialization."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    backup_existing_catalog,
    catalog_write_lock,
    connect_catalog_read_only,
    initialize_catalog,
    json_dumps,
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.ids import canonical_json_hash, finding_id, hash_parts, uuid7
from dext_graph.catalog.normalization import (
    normalize_email,
    normalize_text,
    normalize_url,
    split_multivalue,
)
from dext_graph.catalog.rules import CurationRules, classify_role, load_curation_rules
from dext_graph.config import GraphSettings

_IDENTITY_SINK = "curation_identity"
_FIELD_SINK = "curation_fields"
_CANONICAL_SINK = "curation_canonical"
_FIELD_NAMES = (
    "name",
    "title",
    "research_areas",
    "bio",
    "email",
    "phone",
    "profile_url",
    "external_url",
    "enrollment_pref",
    "publications",
)


def _safe_error(exc: BaseException) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (value or type(exc).__name__)[:2000]


def _resolve_entity(connection: sqlite3.Connection, entity_id: str) -> str:
    seen: set[str] = set()
    current = entity_id
    while True:
        if current in seen:
            raise CatalogError(f"entity merge cycle detected at {current}")
        seen.add(current)
        row = connection.execute(
            "SELECT status, merged_into_id FROM entities WHERE id=?", (current,)
        ).fetchone()
        if row is None:
            raise CatalogError(f"unknown entity: {current}")
        if row["status"] != "merged" or row["merged_into_id"] is None:
            return current
        current = str(row["merged_into_id"])


def _external_identity(value: object) -> str | None:
    url = normalize_url(value)
    if url is None:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    if host in {"orcid.org", "www.orcid.org"}:
        candidate = parts.path.strip("/").upper()
        groups = candidate.split("-")
        if len(groups) == 4 and all(len(group) == 4 for group in groups):
            return "orcid:" + candidate
    if host.endswith("scholar.google.com"):
        user = parse_qs(parts.query).get("user", [None])[0]
        if user:
            return "google_scholar:" + user
    return None


def _claims_for_observation(row: dict[str, Any], rules: CurationRules) -> list[tuple[str, str, str]]:
    payload = json_loads(row["payload_json"], {})
    claims: list[tuple[str, str, str]] = []
    source_url = normalize_url(row["source_url"])
    if source_url and row["source_page_kind"] == "single_profile":
        claims.append(("profile_name_url", f"{source_url}|{row['name_key']}", "strong"))
    external = _external_identity(payload.get("external_link"))
    if external:
        claims.append(("external_identity", external, "strong"))
    if source_url and row["source_page_kind"] in {"multi_profile", "unknown"}:
        claims.append(("listing_name_url", f"{source_url}|{row['name_key']}", "weak"))
    if row["org_unit_source_id"] is not None:
        claims.append(
            (
                "weak_org_name",
                f"{row['university_id']}|{row['org_unit_source_id']}|{row['name_key']}",
                "weak",
            )
        )
    email = normalize_email(payload.get("email"), empty_values=rules.empty_values)
    if email:
        claims.append(("email", email, "weak"))
    return claims


def _claim_targets(
    connection: sqlite3.Connection, claims: list[tuple[str, str, str]], strength: str
) -> set[str]:
    targets: set[str] = set()
    for claim_type, claim_value, claim_strength in claims:
        if claim_strength != strength or (strength == "weak" and claim_type == "email"):
            continue
        rows = connection.execute(
            "SELECT entity_id FROM identity_claims "
            "WHERE claim_type=? AND claim_value=? AND active=1",
            (claim_type, claim_value),
        )
        targets.update(_resolve_entity(connection, str(row["entity_id"])) for row in rows)
    return targets


def _candidate_conflicts(
    connection: sqlite3.Connection,
    entity_id: str,
    claims: list[tuple[str, str, str]],
) -> list[str]:
    conflicts: list[str] = []
    listing = next((value for kind, value, _ in claims if kind == "listing_name_url"), None)
    if listing:
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT claim_value FROM identity_claims "
                "WHERE entity_id=? AND claim_type='listing_name_url' AND active=1",
                (entity_id,),
            )
        }
        if existing and listing not in existing:
            conflicts.append("same_org_name_multiple_profile_urls")
    email = next((value for kind, value, _ in claims if kind == "email"), None)
    if email:
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT claim_value FROM identity_claims "
                "WHERE entity_id=? AND claim_type='email' AND active=1",
                (entity_id,),
            )
        }
        if existing and email not in existing:
            conflicts.append("email_conflict")
    return conflicts


def _insert_finding(
    connection: sqlite3.Connection,
    build_id: str,
    code: str,
    observation_id: str,
    entity_id: str,
    details: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO quality_findings(
          id, build_id, severity, code, entity_id, observation_id, details_json, resolved
        ) VALUES (?, ?, 'warning', ?, ?, ?, ?, 0)
        """,
        (
            finding_id(build_id, code, observation_id),
            build_id,
            code,
            entity_id,
            observation_id,
            json_dumps(details),
        ),
    )


def _assign_observation(
    connection: sqlite3.Connection,
    build_id: str,
    row: dict[str, Any],
    rules: CurationRules,
) -> bool:
    existing = connection.execute(
        "SELECT entity_id FROM entity_observations WHERE build_id=? AND observation_id=?",
        (build_id, row["id"]),
    ).fetchone()
    if existing is not None:
        return False
    claims = _claims_for_observation(row, rules)
    historic = connection.execute(
        "SELECT entity_id FROM entity_observations WHERE observation_id=? "
        "ORDER BY rowid DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    method = "reused"
    finding_code: str | None = None
    finding_details: dict[str, Any] = {}
    if historic is not None:
        entity_id = _resolve_entity(connection, str(historic["entity_id"]))
    else:
        strong_targets = _claim_targets(connection, claims, "strong")
        if len(strong_targets) == 1:
            entity_id = next(iter(strong_targets))
            method = "strong"
        elif len(strong_targets) > 1:
            entity_id = uuid7()
            method = "review"
            finding_code = "strong_claim_collision"
            finding_details = {"candidate_entity_ids": sorted(strong_targets)}
        else:
            weak_targets = _claim_targets(connection, claims, "weak")
            if len(weak_targets) == 1:
                candidate = next(iter(weak_targets))
                conflicts = _candidate_conflicts(connection, candidate, claims)
                if conflicts:
                    entity_id = uuid7()
                    method = "review"
                    finding_code = "identity_conflict"
                    finding_details = {"candidate_entity_id": candidate, "reasons": conflicts}
                else:
                    entity_id = candidate
                    method = "weak"
            elif len(weak_targets) > 1:
                entity_id = uuid7()
                method = "review"
                finding_code = "weak_claim_collision"
                finding_details = {"candidate_entity_ids": sorted(weak_targets)}
            else:
                entity_id = uuid7()
                method = "new"
        now = utcnow_iso()
        connection.execute(
            "INSERT OR IGNORE INTO entities(id, kind, status, merged_into_id, created_at, updated_at) "
            "VALUES (?, 'professor', ?, NULL, ?, ?)",
            (entity_id, "review" if method == "review" else "active", now, now),
        )
    connection.execute(
        "INSERT INTO entity_observations(entity_id, observation_id, match_method, match_score, build_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            entity_id,
            row["id"],
            method,
            {"strong": 1.0, "reused": 1.0, "weak": 0.8, "new": None, "review": 0.0}[method],
            build_id,
        ),
    )
    connection.execute(
        "UPDATE entities SET status='active', updated_at=? WHERE id=? AND status='inactive'",
        (utcnow_iso(), entity_id),
    )
    now = utcnow_iso()
    for claim_type, claim_value, strength in claims:
        if strength == "strong":
            current = connection.execute(
                "SELECT id, entity_id FROM identity_claims "
                "WHERE claim_type=? AND claim_value=? AND active=1",
                (claim_type, claim_value),
            ).fetchone()
            if current is not None and _resolve_entity(connection, str(current["entity_id"])) == entity_id:
                replacement = connection.execute(
                    "SELECT id FROM identity_claims WHERE entity_id=? AND claim_type=? "
                    "AND claim_value=? AND observation_id=?",
                    (entity_id, claim_type, claim_value, row["id"]),
                ).fetchone()
                if replacement is not None and replacement["id"] != current["id"]:
                    connection.execute(
                        "UPDATE identity_claims SET active=0 WHERE id=?", (current["id"],)
                    )
                    connection.execute(
                        "UPDATE identity_claims SET active=1 WHERE id=?", (replacement["id"],)
                    )
                else:
                    connection.execute(
                        "UPDATE identity_claims SET entity_id=?, observation_id=?, active=1 WHERE id=?",
                        (entity_id, row["id"], current["id"]),
                    )
                continue
        connection.execute(
            """
            INSERT OR IGNORE INTO identity_claims(
              entity_id, claim_type, claim_value, strength, observation_id, active, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?)
            """,
            (entity_id, claim_type, claim_value, strength, row["id"], now),
        )
        connection.execute(
            "UPDATE identity_claims SET active=1 WHERE entity_id=? AND claim_type=? "
            "AND claim_value=? AND observation_id=?",
            (entity_id, claim_type, claim_value, row["id"]),
        )
    if finding_code:
        _insert_finding(
            connection, build_id, finding_code, row["id"], entity_id, finding_details
        )
    return True


def _checkpoint(
    connection: sqlite3.Connection,
    build_id: str,
    sink: str,
    partition: str,
    last_key: str,
    rows_written: int,
) -> None:
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id, sink, partition_key, last_key, last_batch_id, rows_written, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(build_id, sink, partition_key) DO UPDATE SET
          last_key=excluded.last_key,
          last_batch_id=excluded.last_batch_id,
          rows_written=sink_checkpoints.rows_written + excluded.rows_written,
          updated_at=excluded.updated_at
        """,
        (
            build_id,
            sink,
            partition,
            last_key,
            hash_parts(build_id, sink, partition, last_key),
            rows_written,
            utcnow_iso(),
        ),
    )


async def _stream_rows(
    catalog_path: Path,
    *,
    query: str,
    params: tuple[Any, ...],
    last_key: str,
    batch_size: int,
    queue_size: int,
    consume: Callable[[list[dict[str, Any]]], Awaitable[None]],
) -> None:
    queue: asyncio.Queue[list[dict[str, Any]] | None] = asyncio.Queue(maxsize=queue_size)

    async def producer() -> None:
        current = last_key
        try:
            with closing(connect_catalog_read_only(catalog_path)) as connection:
                while True:
                    rows = [
                        dict(row)
                        for row in connection.execute(query, params + (current, batch_size))
                    ]
                    if not rows:
                        return
                    current = str(rows[-1]["stream_key"])
                    await queue.put(rows)
        finally:
            await queue.put(None)

    async def consumer() -> None:
        while True:
            rows = await queue.get()
            try:
                if rows is None:
                    return
                await consume(rows)
            finally:
                queue.task_done()

    async with asyncio.TaskGroup() as group:
        group.create_task(producer())
        group.create_task(consumer())


def _last_key(connection: sqlite3.Connection, build_id: str, sink: str, partition: str) -> str:
    row = connection.execute(
        "SELECT last_key FROM sink_checkpoints WHERE build_id=? AND sink=? AND partition_key=?",
        (build_id, sink, partition),
    ).fetchone()
    return str(row[0]) if row is not None and row[0] is not None else ""


def _commit_identity_batch(
    connection: sqlite3.Connection,
    build_id: str,
    university_id: str,
    rows: list[dict[str, Any]],
    rules: CurationRules,
) -> None:
    written = sum(_assign_observation(connection, build_id, row, rules) for row in rows)
    _checkpoint(connection, build_id, _IDENTITY_SINK, university_id, rows[-1]["stream_key"], written)


def _normalized_field_values(payload: dict[str, Any], rules: CurationRules) -> dict[str, tuple[str, ...]]:
    empty = rules.empty_values
    scalar = {
        "name": normalize_text(payload.get("name"), empty_values=empty),
        "title": normalize_text(payload.get("title"), empty_values=empty),
        "bio": normalize_text(payload.get("bio"), empty_values=empty),
        "email": normalize_email(payload.get("email"), empty_values=empty),
        "phone": normalize_text(payload.get("phone"), empty_values=empty),
        "profile_url": normalize_url(payload.get("homepage")),
        "external_url": normalize_url(payload.get("external_link")),
        "enrollment_pref": normalize_text(payload.get("enrollment_pref"), empty_values=empty),
    }
    result = {key: (value,) if value else () for key, value in scalar.items()}
    result["research_areas"] = split_multivalue(payload.get("research_areas"), empty_values=empty)
    result["publications"] = split_multivalue(payload.get("publications"), empty_values=empty)
    return result


def _commit_field_batch(
    connection: sqlite3.Connection,
    build_id: str,
    university_id: str,
    rows: list[dict[str, Any]],
    rules: CurationRules,
) -> None:
    written = 0
    for row in rows:
        entity_id = _resolve_entity(connection, str(row["entity_id"]))
        values = _normalized_field_values(json_loads(row["payload_json"], {}), rules)
        confidence = {"direct": 0.9, "incomplete": 0.6, "legacy_merged": 0.4}[row["provenance_grade"]]
        for field_name, field_values in values.items():
            for value in field_values:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO field_claims(
                      entity_id, field_name, normalized_value, observation_id,
                      confidence, selected, selection_reason, build_id
                    ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?)
                    """,
                    (entity_id, field_name, value, row["observation_id"], confidence, build_id),
                )
                written += max(cursor.rowcount, 0)
    _checkpoint(connection, build_id, _FIELD_SINK, university_id, rows[-1]["stream_key"], written)


def _merge_members(connection: sqlite3.Connection, target_id: str) -> list[str]:
    return [
        str(row[0])
        for row in connection.execute(
            """
            WITH RECURSIVE members(id) AS (
              SELECT ? UNION ALL
              SELECT e.id FROM entities e JOIN members m ON e.merged_into_id=m.id
              WHERE e.status='merged'
            ) SELECT id FROM members ORDER BY id
            """,
            (target_id,),
        )
    ]


def _active_override(
    connection: sqlite3.Connection,
    members: list[str],
    kind: str,
    field_name: str | None,
) -> Any:
    placeholders = ",".join("?" for _ in members)
    condition = "field_name IS NULL" if field_name is None else "field_name=?"
    params: list[Any] = [*members, kind]
    if field_name is not None:
        params.append(field_name)
    row = connection.execute(
        f"SELECT value_json FROM curation_overrides WHERE entity_id IN ({placeholders}) "
        f"AND kind=? AND {condition} AND active=1 ORDER BY version DESC, created_at DESC LIMIT 1",
        params,
    ).fetchone()
    return json.loads(row[0]) if row is not None else None


def _field_rows(
    connection: sqlite3.Connection, build_id: str, members: list[str], field_name: str
) -> list[sqlite3.Row]:
    placeholders = ",".join("?" for _ in members)
    return list(
        connection.execute(
            f"""
            SELECT fc.*, o.provenance_grade, d.fetched_at, b.started_at AS seen_at
            FROM field_claims fc
            JOIN professor_observations o ON o.id=fc.observation_id
            LEFT JOIN source_documents d ON d.id=o.source_document_id
            LEFT JOIN graph_builds b ON b.id=o.last_seen_build
            WHERE fc.build_id=? AND fc.entity_id IN ({placeholders}) AND fc.field_name=?
            ORDER BY CASE o.provenance_grade WHEN 'direct' THEN 3 WHEN 'incomplete' THEN 2 ELSE 1 END DESC,
                     COALESCE(d.fetched_at, b.started_at, '') DESC, fc.observation_id DESC, fc.id DESC
            """,
            (build_id, *members, field_name),
        )
    )


def _select_scalar(
    connection: sqlite3.Connection,
    build_id: str,
    members: list[str],
    field_name: str,
) -> str | None:
    override = _active_override(connection, members, "field", field_name)
    if override is not None:
        return normalize_text(override)
    rows = _field_rows(connection, build_id, members, field_name)
    if not rows:
        return None
    row = rows[0]
    reason = "direct_active" if row["provenance_grade"] == "direct" else (
        "legacy_merged" if row["provenance_grade"] == "legacy_merged" else "latest_nonempty"
    )
    connection.execute(
        "UPDATE field_claims SET selected=1, selection_reason=? WHERE id=?",
        (reason, row["id"]),
    )
    return str(row["normalized_value"])


def _select_union(
    connection: sqlite3.Connection,
    build_id: str,
    members: list[str],
    field_name: str,
) -> str | None:
    override = _active_override(connection, members, "field", field_name)
    if override is not None:
        values = split_multivalue(override)
        return "；".join(values) if values else None
    result: list[str] = []
    seen: set[str] = set()
    for row in _field_rows(connection, build_id, members, field_name):
        value = str(row["normalized_value"])
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        connection.execute(
            "UPDATE field_claims SET selected=1, selection_reason='set_union' WHERE id=?",
            (row["id"],),
        )
    return "；".join(result) if result else None


def _role_context(
    connection: sqlite3.Connection, build_id: str, members: list[str]
) -> tuple[list[str], list[str]]:
    placeholders = ",".join("?" for _ in members)
    contexts: list[str] = []
    enrollment: list[str] = []
    for row in connection.execute(
        f"""
        SELECT o.payload_json, o.source_url, d.title
        FROM entity_observations eo JOIN professor_observations o ON o.id=eo.observation_id
        LEFT JOIN source_documents d ON d.id=o.source_document_id
        WHERE eo.build_id=? AND eo.entity_id IN ({placeholders})
        """,
        (build_id, *members),
    ):
        payload = json_loads(row["payload_json"], {})
        value = normalize_text(payload.get("enrollment_pref"))
        if value:
            enrollment.append(value)
        for candidate in (payload.get("title"), payload.get("org_unit_name"), row["title"], row["source_url"]):
            text = normalize_text(candidate)
            if text:
                contexts.append(text)
        for affiliation in payload.get("affiliations", []):
            text = normalize_text(affiliation.get("org_unit_name"))
            if text:
                contexts.append(text)
    return enrollment, contexts


def _materialize_entity(
    connection: sqlite3.Connection,
    build_id: str,
    target_id: str,
    rules: CurationRules,
) -> None:
    members = _merge_members(connection, target_id)
    placeholders = ",".join("?" for _ in members)
    connection.execute(
        f"UPDATE field_claims SET selected=0, selection_reason=NULL "
        f"WHERE build_id=? AND entity_id IN ({placeholders})",
        (build_id, *members),
    )
    name = _select_scalar(connection, build_id, members, "name")
    if name is None:
        row = connection.execute(
            f"SELECT o.name_raw FROM entity_observations eo "
            f"JOIN professor_observations o ON o.id=eo.observation_id "
            f"WHERE eo.build_id=? AND eo.entity_id IN ({placeholders}) ORDER BY o.id LIMIT 1",
            (build_id, *members),
        ).fetchone()
        if row is None:
            raise CatalogError(f"active entity {target_id} has no name evidence")
        name = str(row[0])
    title = _select_scalar(connection, build_id, members, "title")
    research = _select_union(connection, build_id, members, "research_areas")
    bio = _select_scalar(connection, build_id, members, "bio")
    email = _select_scalar(connection, build_id, members, "email")
    phone = _select_scalar(connection, build_id, members, "phone")
    profile_url = _select_scalar(connection, build_id, members, "profile_url")
    external_url = _select_scalar(connection, build_id, members, "external_url")
    enrollment, contexts = _role_context(connection, build_id, members)
    decision = classify_role(title, enrollment, contexts, rules)
    status = str(connection.execute("SELECT status FROM entities WHERE id=?", (target_id,)).fetchone()[0])
    role_override = _active_override(connection, members, "role", None)
    role_status = str(role_override) if role_override in {"included", "review", "excluded"} else decision.role_status
    reasons = ("manual_role_override",) if role_override in {"included", "review", "excluded"} else decision.reason_codes
    if status == "review" and role_override is None:
        role_status = "review"
        reasons = tuple(dict.fromkeys((*reasons, "identity_review")))
    completeness = round(
        sum(value is not None for value in (name, title, research, bio, email, profile_url)) / 6,
        4,
    )
    connection.execute(
        """
        INSERT INTO canonical_professors(
          entity_id, build_id, name, title_raw, title_family, role_status,
          role_reason_codes, master_eligibility, phd_eligibility,
          research_areas_text, bio, email, phone, profile_url, external_url,
          active, completeness
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(entity_id, build_id) DO UPDATE SET
          name=excluded.name, title_raw=excluded.title_raw,
          title_family=excluded.title_family, role_status=excluded.role_status,
          role_reason_codes=excluded.role_reason_codes,
          master_eligibility=excluded.master_eligibility,
          phd_eligibility=excluded.phd_eligibility,
          research_areas_text=excluded.research_areas_text, bio=excluded.bio,
          email=excluded.email, phone=excluded.phone, profile_url=excluded.profile_url,
          external_url=excluded.external_url, active=1, completeness=excluded.completeness
        """,
        (
            target_id,
            build_id,
            name,
            title,
            decision.title_family,
            role_status,
            json_dumps(list(reasons)),
            decision.master_eligibility,
            decision.phd_eligibility,
            research,
            bio,
            email,
            phone,
            profile_url,
            external_url,
            completeness,
        ),
    )


def _copy_inactive_tombstones(connection: sqlite3.Connection, build_id: str) -> int:
    universities = [
        str(row[0])
        for row in connection.execute(
            "SELECT university_id FROM build_source_tasks WHERE build_id=? ORDER BY ordinal",
            (build_id,),
        )
    ]
    if not universities:
        return 0
    placeholders = ",".join("?" for _ in universities)
    candidates = [
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT DISTINCT ic.entity_id FROM identity_claims ic
            JOIN professor_observations o ON o.id=ic.observation_id
            WHERE o.university_id IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM entity_observations eo
                WHERE eo.build_id=? AND eo.entity_id=ic.entity_id
              )
            ORDER BY ic.entity_id
            """,
            (*universities, build_id),
        )
    ]
    inserted = 0
    for entity_id in candidates:
        previous = connection.execute(
            """
            SELECT cp.* FROM canonical_professors cp JOIN graph_builds b ON b.id=cp.build_id
            WHERE cp.entity_id=? AND cp.build_id<>? ORDER BY b.started_at DESC LIMIT 1
            """,
            (entity_id, build_id),
        ).fetchone()
        if previous is None:
            continue
        columns = [
            "entity_id", "build_id", "name", "title_raw", "title_family", "role_status",
            "role_reason_codes", "master_eligibility", "phd_eligibility",
            "research_areas_text", "bio", "email", "phone", "profile_url",
            "external_url", "active", "completeness",
        ]
        values = [previous[column] for column in columns]
        values[1] = build_id
        values[15] = 0
        cursor = connection.execute(
            f"INSERT OR IGNORE INTO canonical_professors({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        inserted += max(cursor.rowcount, 0)
        entity = connection.execute("SELECT status FROM entities WHERE id=?", (entity_id,)).fetchone()
        if entity is not None and entity[0] not in {"merged", "review"}:
            connection.execute(
                "UPDATE entities SET status='inactive', updated_at=? WHERE id=?",
                (utcnow_iso(), entity_id),
            )
    return inserted


def _commit_canonical_batch(
    connection: sqlite3.Connection,
    build_id: str,
    university_id: str,
    rows: list[dict[str, Any]],
    rules: CurationRules,
) -> None:
    written = 0
    for row in rows:
        target = _resolve_entity(connection, str(row["entity_id"]))
        before = connection.execute(
            "SELECT 1 FROM canonical_professors WHERE build_id=? AND entity_id=?",
            (build_id, target),
        ).fetchone()
        _materialize_entity(connection, build_id, target, rules)
        written += int(before is None)
    _checkpoint(connection, build_id, _CANONICAL_SINK, university_id, rows[-1]["stream_key"], written)


def _override_manifest(connection: sqlite3.Connection) -> str:
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT id, entity_id, kind, field_name, value_json, version "
            "FROM curation_overrides WHERE active=1 ORDER BY id"
        )
    ]
    return canonical_json_hash(rows)


def _prepare_run(connection: sqlite3.Connection, build_id: str, rules: CurationRules) -> str:
    build = connection.execute("SELECT status FROM graph_builds WHERE id=?", (build_id,)).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    existing = connection.execute("SELECT * FROM curation_runs WHERE build_id=?", (build_id,)).fetchone()
    override_hash = _override_manifest(connection)
    if existing is None:
        if build["status"] != "CURATING":
            raise CatalogError(f"build {build_id} in status {build['status']} cannot start curation")
        run_id = hash_parts(build_id, rules.version, rules.normalization_version, rules.manifest_hash, override_hash)
        connection.execute(
            """
            INSERT INTO curation_runs(
              id, build_id, status, curation_version, normalization_version,
              rules_hash, override_manifest_hash, summary_json, started_at
            ) VALUES (?, ?, 'RUNNING', ?, ?, ?, ?, '{}', ?)
            """,
            (
                run_id, build_id, rules.version, rules.normalization_version,
                rules.manifest_hash, override_hash, utcnow_iso(),
            ),
        )
        return run_id
    if (
        existing["curation_version"] != rules.version
        or existing["normalization_version"] != rules.normalization_version
        or existing["rules_hash"] != rules.manifest_hash
        or existing["override_manifest_hash"] != override_hash
    ):
        raise CatalogError("curation rules or override manifest changed; create a new build")
    if existing["status"] == "COMPLETED":
        return str(existing["id"])
    connection.execute(
        "UPDATE curation_runs SET status='RUNNING', last_error=NULL, finished_at=NULL WHERE id=?",
        (existing["id"],),
    )
    connection.execute(
        "UPDATE graph_builds SET status='CURATING', last_error=NULL WHERE id=?", (build_id,)
    )
    return str(existing["id"])


def _deactivate_stale_claims(connection: sqlite3.Connection, build_id: str) -> None:
    universities = [
        str(row[0])
        for row in connection.execute(
            "SELECT university_id FROM build_source_tasks WHERE build_id=?", (build_id,)
        )
    ]
    if not universities:
        return
    placeholders = ",".join("?" for _ in universities)
    connection.execute(
        f"UPDATE identity_claims SET active=0 WHERE observation_id IN ("
        f"SELECT id FROM professor_observations WHERE active=0 AND university_id IN ({placeholders}))",
        universities,
    )


async def _run_identity(
    writer: CatalogWriter, build_id: str, settings: GraphSettings, rules: CurationRules
) -> None:
    tasks = await writer.execute(
        lambda c: [dict(row) for row in c.execute(
            "SELECT university_id FROM build_source_tasks WHERE build_id=? ORDER BY ordinal", (build_id,)
        )], transactional=False
    )
    batch_counter = 0
    for task in tasks:
        university_id = task["university_id"]
        last = await writer.execute(lambda c, u=university_id: _last_key(c, build_id, _IDENTITY_SINK, u), transactional=False)

        async def consume(rows: list[dict[str, Any]], university: str = university_id) -> None:
            nonlocal batch_counter
            await writer.execute(lambda c: _commit_identity_batch(c, build_id, university, rows, rules))
            batch_counter += 1
            limit = os.getenv("DEXT_TEST_KILL_AFTER_CURATION_IDENTITY_BATCHES")
            if limit and batch_counter >= int(limit):
                os._exit(93)

        await _stream_rows(
            writer.path,
            query="SELECT id AS stream_key, * FROM professor_observations "
                  "WHERE university_id=? AND active=1 AND id>? ORDER BY id LIMIT ?",
            params=(university_id,),
            last_key=last,
            batch_size=settings.build_read_batch,
            queue_size=settings.curation_queue,
            consume=consume,
        )


async def _run_fields(
    writer: CatalogWriter, build_id: str, settings: GraphSettings, rules: CurationRules
) -> None:
    tasks = await writer.execute(
        lambda c: [dict(row) for row in c.execute(
            "SELECT university_id FROM build_source_tasks WHERE build_id=? ORDER BY ordinal", (build_id,)
        )], transactional=False
    )
    batch_counter = 0
    for task in tasks:
        university_id = task["university_id"]
        last = await writer.execute(lambda c, u=university_id: _last_key(c, build_id, _FIELD_SINK, u), transactional=False)

        async def consume(rows: list[dict[str, Any]], university: str = university_id) -> None:
            nonlocal batch_counter
            await writer.execute(lambda c: _commit_field_batch(c, build_id, university, rows, rules))
            batch_counter += 1
            limit = os.getenv("DEXT_TEST_KILL_AFTER_CURATION_FIELD_BATCHES")
            if limit and batch_counter >= int(limit):
                os._exit(94)

        await _stream_rows(
            writer.path,
            query="SELECT o.id AS stream_key, eo.entity_id, o.id AS observation_id, "
                  "o.payload_json, o.provenance_grade FROM entity_observations eo "
                  "JOIN professor_observations o ON o.id=eo.observation_id "
                  "WHERE eo.build_id=? AND o.university_id=? AND o.id>? ORDER BY o.id LIMIT ?",
            params=(build_id, university_id),
            last_key=last,
            batch_size=settings.build_read_batch,
            queue_size=settings.curation_queue,
            consume=consume,
        )


async def _run_canonical(
    writer: CatalogWriter, build_id: str, settings: GraphSettings, rules: CurationRules
) -> None:
    tasks = await writer.execute(
        lambda c: [dict(row) for row in c.execute(
            "SELECT university_id FROM build_source_tasks WHERE build_id=? ORDER BY ordinal", (build_id,)
        )], transactional=False
    )
    batch_counter = 0
    for task in tasks:
        university_id = task["university_id"]
        last = await writer.execute(lambda c, u=university_id: _last_key(c, build_id, _CANONICAL_SINK, u), transactional=False)

        async def consume(rows: list[dict[str, Any]], university: str = university_id) -> None:
            nonlocal batch_counter
            await writer.execute(lambda c: _commit_canonical_batch(c, build_id, university, rows, rules))
            batch_counter += 1
            limit = os.getenv("DEXT_TEST_KILL_AFTER_CURATION_CANONICAL_BATCHES")
            if limit and batch_counter >= int(limit):
                os._exit(95)

        await _stream_rows(
            writer.path,
            query="SELECT DISTINCT eo.entity_id AS stream_key, eo.entity_id FROM entity_observations eo "
                  "JOIN professor_observations o ON o.id=eo.observation_id "
                  "WHERE eo.build_id=? AND o.university_id=? AND eo.entity_id>? "
                  "ORDER BY eo.entity_id LIMIT ?",
            params=(build_id, university_id),
            last_key=last,
            batch_size=settings.build_read_batch,
            queue_size=settings.curation_queue,
            consume=consume,
        )
    await writer.execute(lambda c: _copy_inactive_tombstones(c, build_id))


def _finish_run(connection: sqlite3.Connection, build_id: str, run_id: str) -> None:
    summary = {
        "entities": int(connection.execute(
            "SELECT COUNT(DISTINCT entity_id) FROM entity_observations WHERE build_id=?", (build_id,)
        ).fetchone()[0]),
        "canonical_active": int(connection.execute(
            "SELECT COUNT(*) FROM canonical_professors WHERE build_id=? AND active=1", (build_id,)
        ).fetchone()[0]),
        "canonical_inactive": int(connection.execute(
            "SELECT COUNT(*) FROM canonical_professors WHERE build_id=? AND active=0", (build_id,)
        ).fetchone()[0]),
        "identity_findings": int(connection.execute(
            "SELECT COUNT(*) FROM quality_findings WHERE build_id=? AND code IN "
            "('strong_claim_collision','weak_claim_collision','identity_conflict')", (build_id,)
        ).fetchone()[0]),
        "gold_status": "not_evaluated",
    }
    now = utcnow_iso()
    connection.execute(
        "UPDATE curation_runs SET status='COMPLETED', summary_json=?, finished_at=?, last_error=NULL WHERE id=?",
        (json_dumps(summary), now, run_id),
    )
    build_summary = json_loads(connection.execute(
        "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()[0], {})
    build_summary["curation"] = summary
    connection.execute(
        "UPDATE graph_builds SET status='EMBEDDING', summary_json=?, last_error=NULL WHERE id=?",
        (json_dumps(build_summary), build_id),
    )


def _fail_run(connection: sqlite3.Connection, build_id: str, run_id: str, error: str) -> None:
    now = utcnow_iso()
    connection.execute(
        "UPDATE curation_runs SET status='FAILED', last_error=?, finished_at=? WHERE id=?",
        (error, now, run_id),
    )
    connection.execute(
        "UPDATE graph_builds SET status='FAILED', last_error=? WHERE id=?", (error, build_id)
    )


async def run_curation(
    writer: CatalogWriter, build_id: str, settings: GraphSettings
) -> dict[str, Any]:
    rules = load_curation_rules()
    run_id = await writer.execute(lambda c: _prepare_run(c, build_id, rules))
    run = await writer.execute(
        lambda c: dict(c.execute("SELECT * FROM curation_runs WHERE id=?", (run_id,)).fetchone()),
        transactional=False,
    )
    if run["status"] == "COMPLETED":
        from dext_graph.catalog.workflow import build_status

        return build_status(settings.catalog_path, build_id)
    try:
        await _run_identity(writer, build_id, settings, rules)
        await writer.execute(lambda c: _deactivate_stale_claims(c, build_id))
        await _run_fields(writer, build_id, settings, rules)
        await _run_canonical(writer, build_id, settings, rules)
        await writer.execute(lambda c: _finish_run(c, build_id, run_id))
    except Exception as exc:  # noqa: BLE001 -- persist resumable stage failure
        error = _safe_error(exc)
        await writer.execute(lambda c: _fail_run(c, build_id, run_id, error))
    from dext_graph.catalog.workflow import build_status

    return build_status(settings.catalog_path, build_id)


async def curate_build(
    build_id: str, settings: GraphSettings | None = None
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            return await run_curation(writer, build_id, settings)


__all__ = ["curate_build", "run_curation"]
