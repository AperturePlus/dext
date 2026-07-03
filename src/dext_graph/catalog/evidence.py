"""Stage-3 evidence extraction and deterministic catalog graph exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    connect_catalog_read_only,
    json_dumps,
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.ids import hash_parts
from dext_graph.catalog.org_units import (
    iter_resolved_affiliations,
    load_org_records,
)
from dext_graph.catalog.progress import ProgressCallback, emit_progress
from dext_graph.config import GraphSettings

EVIDENCE_VERSION = "evidence-v1"
EXPORT_VERSION = "neo4j-export-v1"

_RESEARCH_SPLIT = re.compile(r"[；;、\n]+")
_PUBLICATION_SPLIT = re.compile(r"[；\n]+")
_WS = re.compile(r"\s+")
_FIELD_LABEL = re.compile(r"^(?:研究方向|研究领域|主要研究方向|research\s+(?:areas?|interests?))\s*[:：]\s*", re.I)
_LEADING_MARKER = re.compile(
    r"^\s*(?:[（(]?\d+[）)]|[一二三四五六七八九十]+[、.．)]|\[\d+\]|\d+[、.．:)）])\s*"
)
_DOI = re.compile(r"(?i)(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)?(10\.\d{4,9}/[-._;()/:a-z0-9]+)")
_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_EDGE_CONFIDENCE = {"direct": 1.0, "legacy_merged": 0.8, "incomplete": 0.5}


@dataclass(frozen=True)
class ResearchPart:
    raw_text: str
    normalized_text: str
    language: str
    statement_hash: str


@dataclass(frozen=True)
class PublicationPart:
    raw_text: str
    normalized_text: str
    doi: str | None
    year: int | None
    confidence: float
    needs_review: bool


@dataclass(frozen=True)
class ExportRow:
    partition_key: str
    row_key: str
    row_kind: str
    label_or_type: str
    payload: dict[str, Any]
    provenance_ref: str
    start_graph_key: str | None = None
    end_graph_key: str | None = None


def _text(value: object) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", str(value))).strip()


def detect_language(value: str) -> str:
    has_han = _HAN.search(value) is not None
    has_latin = _LATIN.search(value) is not None
    if has_han and has_latin:
        return "mixed"
    if has_han:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"


def split_research_statements(value: object) -> tuple[ResearchPart, ...]:
    if value is None:
        return ()
    parts: list[ResearchPart] = []
    seen: set[str] = set()
    for segment in _RESEARCH_SPLIT.split(str(value)):
        raw = segment.strip()
        if not raw:
            continue
        normalized = _FIELD_LABEL.sub("", _text(raw))
        normalized = _LEADING_MARKER.sub("", normalized).strip(" -—–:：;；、,.，。·•")
        if not normalized or not any(character.isalnum() for character in normalized):
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(
            ResearchPart(
                raw_text=raw,
                normalized_text=normalized,
                language=detect_language(normalized),
                statement_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(parts)


def _normalized_dois(value: str) -> tuple[str, ...]:
    values: list[str] = []
    for match in _DOI.finditer(value):
        candidate = match.group(1).strip().rstrip(".,;:，。；：])}）").casefold()
        if candidate and candidate not in values:
            values.append(candidate)
    return tuple(values)


def split_publication_mentions(value: object) -> tuple[PublicationPart, ...]:
    if value is None:
        return ()
    parts: list[PublicationPart] = []
    seen: set[str] = set()
    for segment in _PUBLICATION_SPLIT.split(str(value)):
        raw = segment.strip()
        if not raw:
            continue
        normalized = _LEADING_MARKER.sub("", _text(raw)).strip(" -—–")
        if not normalized or not any(character.isalnum() for character in normalized):
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        dois = _normalized_dois(normalized)
        years = sorted({int(value) for value in _YEAR.findall(normalized)})
        parts.append(
            PublicationPart(
                raw_text=raw,
                normalized_text=normalized,
                doi=dois[0] if len(dois) == 1 else None,
                year=years[0] if len(years) == 1 else None,
                confidence=1.0,
                needs_review=len(dois) > 1 or len(years) > 1,
            )
        )
    return tuple(parts)


def _checkpoint(
    connection: sqlite3.Connection,
    build_id: str,
    sink: str,
    partition: str,
    last_key: str,
    rows: int,
) -> None:
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id, sink, partition_key, last_key, last_batch_id, rows_written, updated_at
        ) VALUES (?, ?, ?, ?, NULL, ?, ?)
        ON CONFLICT(build_id, sink, partition_key) DO UPDATE SET
          last_key=excluded.last_key,
          rows_written=sink_checkpoints.rows_written + excluded.rows_written,
          updated_at=excluded.updated_at
        """,
        (build_id, sink, partition, last_key, rows, utcnow_iso()),
    )


def _last_key(connection: sqlite3.Connection, build_id: str, sink: str, partition: str) -> str:
    row = connection.execute(
        "SELECT last_key FROM sink_checkpoints WHERE build_id=? AND sink=? AND partition_key=?",
        (build_id, sink, partition),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _insert_evidence_batch(
    connection: sqlite3.Connection, build_id: str, rows: list[dict[str, Any]]
) -> int:
    written = 0
    for row in rows:
        payload = json_loads(row["payload_json"], {})
        entity_id = str(row["entity_id"])
        observation_id = str(row["observation_id"])
        for statement in split_research_statements(payload.get("research_areas")):
            statement_id = hash_parts(entity_id, observation_id, statement.normalized_text)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO research_statements(
                  id, build_id, entity_id, observation_id, raw_text,
                  normalized_text, language, statement_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    statement_id,
                    build_id,
                    entity_id,
                    observation_id,
                    statement.raw_text,
                    statement.normalized_text,
                    statement.language,
                    statement.statement_hash,
                ),
            )
            written += max(cursor.rowcount, 0)
        for mention in split_publication_mentions(payload.get("publications")):
            mention_id = hash_parts(entity_id, mention.doi or mention.normalized_text.casefold())
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO publication_mentions(
                  id, build_id, entity_id, observation_id, raw_text,
                  normalized_text, doi, year, confidence, needs_review
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mention_id,
                    build_id,
                    entity_id,
                    observation_id,
                    mention.raw_text,
                    mention.normalized_text,
                    mention.doi,
                    mention.year,
                    mention.confidence,
                    int(mention.needs_review),
                ),
            )
            written += max(cursor.rowcount, 0)
    _checkpoint(connection, build_id, "graph_evidence", "observations", rows[-1]["observation_id"], written)
    return written


async def materialize_evidence(
    writer: CatalogWriter, build_id: str, settings: GraphSettings
) -> int:
    total = 0
    batches = 0
    while True:
        last = await writer.execute(
            lambda connection: _last_key(connection, build_id, "graph_evidence", "observations"),
            transactional=False,
        )
        with closing(connect_catalog_read_only(writer.path)) as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT o.id AS observation_id, eo.entity_id, o.payload_json
                    FROM professor_observations o
                    JOIN entity_observations eo
                      ON eo.observation_id=o.id AND eo.build_id=?
                    JOIN canonical_professors cp
                      ON cp.entity_id=eo.entity_id AND cp.build_id=? AND cp.active=1
                    WHERE o.active=1 AND o.id>?
                    ORDER BY o.id LIMIT ?
                    """,
                    (build_id, build_id, last, settings.build_read_batch),
                )
            ]
        if not rows:
            return total
        total += await writer.execute(lambda connection: _insert_evidence_batch(connection, build_id, rows))
        batches += 1
        limit = os.getenv("DEXT_TEST_KILL_AFTER_EVIDENCE_BATCHES")
        if limit and batches >= int(limit):
            os._exit(97)


def _graph_key(build_id: str, logical_id: str) -> str:
    return f"{build_id}:{logical_id}"


def _export_row(
    build_id: str,
    partition: str,
    row_key: str,
    kind: str,
    label_or_type: str,
    payload: dict[str, Any],
    provenance: str,
    start: str | None = None,
    end: str | None = None,
) -> ExportRow:
    payload = dict(payload)
    payload.setdefault("build_id", build_id)
    payload.setdefault("provenance_ref", provenance)
    return ExportRow(partition, row_key, kind, label_or_type, payload, provenance, start, end)


def _source_tasks(connection: sqlite3.Connection, build_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT t.university_id, t.university_name, t.source_snapshot_id,
                   s.snapshot_path, s.file_hash
            FROM build_source_tasks t
            JOIN source_snapshots s ON s.id=t.source_snapshot_id
            WHERE t.build_id=? AND t.status='COMPLETED'
            ORDER BY t.university_id
            """,
            (build_id,),
        )
    ]


def _snapshot_meta(task: dict[str, Any]) -> dict[str, Any]:
    path = Path(task["snapshot_path"]).resolve()
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(university_meta)")
        }
        location = "location" if "location" in columns else "NULL AS location"
        row = connection.execute(
            f"SELECT name, {location} FROM university_meta LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else {"name": task["university_name"], "location": None}
    finally:
        connection.close()


def _org_records(connection: sqlite3.Connection, build_id: str) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        key: {
            "logical_id": record.logical_id,
            "graph_key": record.graph_key,
            "university_id": record.university_id,
            "source_id": record.source_id,
            "name": record.name,
            "url": record.url,
            "kind": record.kind,
        }
        for key, record in load_org_records(connection, build_id).items()
    }


def _node_rows(connection: sqlite3.Connection, build_id: str, partition: str) -> Iterator[ExportRow]:
    label = partition.removeprefix("node:")
    if label == "Build":
        build = dict(connection.execute("SELECT * FROM graph_builds WHERE id=?", (build_id,)).fetchone())
        logical_id = build_id
        provenance = f"catalog:build:{build_id}"
        yield _export_row(
            build_id,
            partition,
            logical_id,
            "node",
            label,
            {
                "id": build_id,
                "graph_key": _graph_key(build_id, logical_id),
                "status": "WRITING_VECTOR",
                "source_manifest_hash": build["source_manifest_hash"],
                "graph_schema_version": build["graph_schema_version"],
                "vector_schema_version": build["vector_schema_version"],
            },
            provenance,
        )
        return
    if label == "University":
        for task in _source_tasks(connection, build_id):
            meta = _snapshot_meta(task)
            logical_id = str(task["university_id"])
            provenance = f"catalog:source-snapshot:{task['source_snapshot_id']}"
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": _graph_key(build_id, logical_id),
                    "name": meta.get("name") or task["university_name"],
                    "city": meta.get("location"),
                    "province": None,
                    "active": True,
                }, provenance,
            )
        return
    if label == "OrgUnit":
        for record in sorted(_org_records(connection, build_id).values(), key=lambda value: value["logical_id"]):
            logical_id = record["logical_id"]
            provenance = (
                f"catalog:org-unit:{build_id}:{record['university_id']}:{record['source_id']}"
            )
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": record["graph_key"],
                    "university_id": record["university_id"],
                    "name": record["name"],
                    "kind": record["kind"],
                    "url": record["url"],
                }, provenance,
            )
        return
    if label == "Professor":
        for row in connection.execute(
            """
            SELECT cp.*,pp.profile_hash
            FROM canonical_professors cp
            LEFT JOIN professor_profiles pp
              ON pp.build_id=cp.build_id AND pp.entity_id=cp.entity_id
            WHERE cp.build_id=? AND cp.active=1
            ORDER BY cp.entity_id
            """,
            (build_id,),
        ):
            logical_id = str(row["entity_id"])
            provenance = f"catalog:entity:{logical_id}:build:{build_id}"
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": _graph_key(build_id, logical_id),
                    "name": row["name"],
                    "title": row["title_raw"],
                    "title_family": row["title_family"],
                    "role_status": row["role_status"],
                    "role_reason_codes": json_loads(row["role_reason_codes"], []),
                    "master_eligibility": row["master_eligibility"],
                    "phd_eligibility": row["phd_eligibility"],
                    "active": True,
                    "profile_hash": row["profile_hash"],
                }, provenance,
            )
        return
    if label == "ResearchStatement":
        for row in connection.execute(
            "SELECT * FROM research_statements WHERE build_id=? ORDER BY id", (build_id,)
        ):
            logical_id = str(row["id"])
            provenance = f"catalog:research-statement:{build_id}:{logical_id}"
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": _graph_key(build_id, logical_id),
                    "raw_text": row["raw_text"],
                    "language": row["language"],
                    "statement_hash": row["statement_hash"],
                }, provenance,
            )
        return
    if label == "Topic":
        query = """
            SELECT * FROM topics
            WHERE taxonomy_version=(SELECT taxonomy_version FROM graph_builds WHERE id=?)
              AND status='active' ORDER BY id
        """
        for row in connection.execute(query, (build_id,)):
            logical_id = str(row["id"])
            provenance = f"taxonomy:{row['taxonomy_version']}:topic:{logical_id}"
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": _graph_key(build_id, logical_id),
                    "canonical_name": row["canonical_name"],
                    "normalized_name": row["normalized_name"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "taxonomy_version": row["taxonomy_version"],
                }, provenance,
            )
        return
    if label == "PublicationMention":
        query = """
            SELECT id, entity_id, MIN(raw_text) AS raw_text, MIN(normalized_text) AS normalized_text,
                   MIN(doi) AS doi,
                   CASE WHEN COUNT(DISTINCT year)=1 THEN MIN(year) ELSE NULL END AS year,
                   MIN(confidence) AS confidence, COUNT(DISTINCT observation_id) AS evidence_count,
                   MAX(needs_review) AS needs_review
            FROM publication_mentions WHERE build_id=?
            GROUP BY id, entity_id ORDER BY id
        """
        for row in connection.execute(query, (build_id,)):
            logical_id = str(row["id"])
            provenance = f"catalog:publication-mention:{build_id}:{logical_id}"
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": _graph_key(build_id, logical_id),
                    "raw_text": row["raw_text"],
                    "doi": row["doi"],
                    "year": row["year"],
                    "confidence": row["confidence"],
                    "evidence_count": row["evidence_count"],
                    "needs_review": bool(row["needs_review"]),
                }, provenance,
            )
        return
    if label == "SourceDocument":
        query = """
            SELECT DISTINCT d.* FROM source_documents d
            JOIN professor_observations o ON o.source_document_id=d.id
            JOIN entity_observations eo ON eo.observation_id=o.id AND eo.build_id=?
            JOIN canonical_professors cp ON cp.entity_id=eo.entity_id AND cp.build_id=? AND cp.active=1
            WHERE o.active=1 ORDER BY d.id
        """
        for row in connection.execute(query, (build_id, build_id)):
            logical_id = str(row["id"])
            provenance = f"catalog:source-document:{logical_id}"
            yield _export_row(
                build_id, partition, logical_id, "node", label,
                {
                    "logical_id": logical_id,
                    "graph_key": _graph_key(build_id, logical_id),
                    "url": row["url"],
                    "content_hash": row["content_hash"],
                    "title": row["title"],
                    "fetched_at": row["fetched_at"],
                }, provenance,
            )
        return
    raise CatalogError(f"unknown node export partition: {partition}")


def _relationship(
    build_id: str,
    partition: str,
    start: str,
    end: str,
    evidence_count: int,
    confidence: float,
    provenance: str,
    method: str | None = None,
) -> ExportRow:
    relationship_type = partition.removeprefix("rel:")
    row_key = f"{start}|{end}"
    graph_key = _graph_key(build_id, hash_parts(relationship_type, start, end))
    return _export_row(
        build_id, partition, row_key, "relationship", relationship_type,
        {
            "graph_key": graph_key,
            "confidence": confidence,
            "evidence_count": evidence_count,
            "evidence_lookup": provenance,
            "provenance_ref": provenance,
            **({"method": method} if method is not None else {}),
        }, provenance, start, end,
    )


def _relationship_rows(
    connection: sqlite3.Connection, build_id: str, partition: str
) -> Iterator[ExportRow]:
    relationship_type = partition.removeprefix("rel:")
    orgs = _org_records(connection, build_id) if relationship_type == "PART_OF" else {}
    if relationship_type == "PART_OF":
        for record in sorted(orgs.values(), key=lambda value: value["graph_key"]):
            start = record["graph_key"]
            end = _graph_key(build_id, record["university_id"])
            provenance = f"catalog:org-unit:{build_id}:{record['university_id']}:{record['source_id']}"
            yield _relationship(build_id, partition, start, end, 1, 1.0, provenance)
        return
    if relationship_type == "AFFILIATED_WITH":
        current_entity = ""
        grouped: dict[str, tuple[int, float, str]] = {}

        def emit(entity_id: str) -> Iterator[ExportRow]:
            for org_key in sorted(grouped):
                count, confidence, lookup = grouped[org_key]
                yield _relationship(
                    build_id, partition, _graph_key(build_id, entity_id), org_key,
                    count, confidence, lookup,
                )

        for affiliation in iter_resolved_affiliations(connection, build_id):
            entity_id = affiliation.entity_id
            if current_entity and entity_id != current_entity:
                yield from emit(current_entity)
                grouped = {}
            current_entity = entity_id
            record = affiliation.org_unit
            org_key = record.graph_key
            confidence = _EDGE_CONFIDENCE.get(affiliation.provenance_grade, 0.5)
            old_count, old_confidence, _ = grouped.get(org_key, (0, 1.0, ""))
            lookup = f"catalog:affiliation:{build_id}:{entity_id}:{record.logical_id}"
            grouped[org_key] = (old_count + 1, min(old_confidence, confidence), lookup)
        if current_entity:
            yield from emit(current_entity)
        return
    if relationship_type == "HAS_RESEARCH_STATEMENT":
        query = """
            SELECT s.entity_id, s.id, o.provenance_grade
            FROM research_statements s JOIN professor_observations o ON o.id=s.observation_id
            WHERE s.build_id=? ORDER BY s.entity_id, s.id
        """
        for row in connection.execute(query, (build_id,)):
            provenance = f"catalog:research-statement:{build_id}:{row['id']}"
            yield _relationship(
                build_id, partition, _graph_key(build_id, str(row["entity_id"])),
                _graph_key(build_id, str(row["id"])), 1,
                _EDGE_CONFIDENCE.get(str(row["provenance_grade"]), 0.5), provenance,
            )
        return
    if relationship_type in {
        "PRIMARY_TOPIC", "USES_METHOD", "APPLIED_TO", "TARGETS_TASK", "STUDIES"
    }:
        query = """
            SELECT l.* FROM statement_topic_links l
            JOIN topics t ON t.taxonomy_version=l.taxonomy_version AND t.id=l.topic_id
            WHERE l.build_id=? AND l.relation_type=? AND l.review_status='approved'
              AND t.status='active'
            ORDER BY l.statement_id,l.topic_id
        """
        for row in connection.execute(query, (build_id, relationship_type)):
            start = _graph_key(build_id, str(row["statement_id"]))
            end = _graph_key(build_id, str(row["topic_id"]))
            yield _relationship(
                build_id, partition, start, end, 1, float(row["confidence"]),
                str(row["provenance_ref"]), str(row["method"]),
            )
        return
    if relationship_type == "SUBTOPIC_OF":
        query = """
            SELECT r.* FROM topic_relations r
            JOIN topics child ON child.taxonomy_version=r.taxonomy_version
              AND child.id=r.from_topic_id
            JOIN topics parent ON parent.taxonomy_version=r.taxonomy_version
              AND parent.id=r.to_topic_id
            WHERE r.build_id=? AND r.relation_type='SUBTOPIC_OF'
              AND child.status='active' AND parent.status='active'
            ORDER BY r.from_topic_id,r.to_topic_id
        """
        for row in connection.execute(query, (build_id,)):
            start = _graph_key(build_id, str(row["from_topic_id"]))
            end = _graph_key(build_id, str(row["to_topic_id"]))
            yield _relationship(
                build_id, partition, start, end, 1, float(row["confidence"]),
                str(row["provenance_ref"]), str(row["method"]),
            )
        return
    if relationship_type == "HAS_PUBLICATION_MENTION":
        query = """
            SELECT m.entity_id, m.id, COUNT(DISTINCT m.observation_id) AS evidence_count,
                   MIN(CASE o.provenance_grade WHEN 'direct' THEN 1.0
                       WHEN 'legacy_merged' THEN 0.8 ELSE 0.5 END) AS confidence
            FROM publication_mentions m JOIN professor_observations o ON o.id=m.observation_id
            WHERE m.build_id=? GROUP BY m.entity_id, m.id ORDER BY m.entity_id, m.id
        """
        for row in connection.execute(query, (build_id,)):
            provenance = f"catalog:publication-mention:{build_id}:{row['id']}"
            yield _relationship(
                build_id, partition, _graph_key(build_id, str(row["entity_id"])),
                _graph_key(build_id, str(row["id"])), int(row["evidence_count"]),
                float(row["confidence"]), provenance,
            )
        return
    if relationship_type == "OBSERVED_IN":
        query = """
            SELECT eo.entity_id, o.source_document_id, COUNT(*) AS evidence_count,
                   MIN(CASE o.provenance_grade WHEN 'direct' THEN 1.0
                       WHEN 'legacy_merged' THEN 0.8 ELSE 0.5 END) AS confidence
            FROM entity_observations eo JOIN professor_observations o ON o.id=eo.observation_id
            JOIN canonical_professors cp ON cp.entity_id=eo.entity_id AND cp.build_id=? AND cp.active=1
            WHERE eo.build_id=? AND o.active=1 AND o.source_document_id IS NOT NULL
            GROUP BY eo.entity_id, o.source_document_id ORDER BY eo.entity_id, o.source_document_id
        """
        for row in connection.execute(query, (build_id, build_id)):
            provenance = f"catalog:observation-document:{build_id}:{row['entity_id']}:{row['source_document_id']}"
            yield _relationship(
                build_id, partition, _graph_key(build_id, str(row["entity_id"])),
                _graph_key(build_id, str(row["source_document_id"])), int(row["evidence_count"]),
                float(row["confidence"]), provenance,
            )
        return
    if relationship_type == "FROM_UNIVERSITY":
        query = """
            SELECT d.id, d.university_id, COUNT(DISTINCT o.id) AS evidence_count
            FROM source_documents d JOIN professor_observations o ON o.source_document_id=d.id
            JOIN entity_observations eo ON eo.observation_id=o.id AND eo.build_id=?
            JOIN canonical_professors cp ON cp.entity_id=eo.entity_id AND cp.build_id=? AND cp.active=1
            WHERE o.active=1 GROUP BY d.id, d.university_id ORDER BY d.id
        """
        for row in connection.execute(query, (build_id, build_id)):
            provenance = f"catalog:source-document:{row['id']}"
            yield _relationship(
                build_id, partition, _graph_key(build_id, str(row["id"])),
                _graph_key(build_id, str(row["university_id"])), int(row["evidence_count"]),
                1.0, provenance,
            )
        return
    raise CatalogError(f"unknown relationship export partition: {partition}")


PARTITIONS = (
    "node:Build",
    "node:University",
    "node:OrgUnit",
    "node:Professor",
    "node:ResearchStatement",
    "node:Topic",
    "node:PublicationMention",
    "node:SourceDocument",
    "rel:PART_OF",
    "rel:AFFILIATED_WITH",
    "rel:HAS_RESEARCH_STATEMENT",
    "rel:PRIMARY_TOPIC",
    "rel:USES_METHOD",
    "rel:APPLIED_TO",
    "rel:TARGETS_TASK",
    "rel:STUDIES",
    "rel:SUBTOPIC_OF",
    "rel:HAS_PUBLICATION_MENTION",
    "rel:OBSERVED_IN",
    "rel:FROM_UNIVERSITY",
)


def _insert_export_batch(
    connection: sqlite3.Connection, build_id: str, partition: str, rows: list[ExportRow]
) -> int:
    for row in rows:
        payload_json = json_dumps(row.payload)
        checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO graph_export_rows(
              build_id, partition_key, row_key, row_kind, label_or_type,
              start_graph_key, end_graph_key, payload_json, provenance_ref, row_checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(build_id, partition_key, row_key) DO UPDATE SET
              row_kind=excluded.row_kind, label_or_type=excluded.label_or_type,
              start_graph_key=excluded.start_graph_key, end_graph_key=excluded.end_graph_key,
              payload_json=excluded.payload_json, provenance_ref=excluded.provenance_ref,
              row_checksum=excluded.row_checksum
            """,
            (
                build_id, partition, row.row_key, row.row_kind, row.label_or_type,
                row.start_graph_key, row.end_graph_key, payload_json,
                row.provenance_ref, checksum,
            ),
        )
    _checkpoint(connection, build_id, "graph_export", partition, rows[-1].row_key, len(rows))
    return len(rows)


def _finalize_partition(connection: sqlite3.Connection, build_id: str, partition: str) -> None:
    digest = hashlib.sha256()
    count = 0
    minimum: str | None = None
    maximum: str | None = None
    kind = ""
    label = ""
    for row in connection.execute(
        """
        SELECT row_key, row_checksum, row_kind, label_or_type
        FROM graph_export_rows WHERE build_id=? AND partition_key=? ORDER BY row_key
        """,
        (build_id, partition),
    ):
        key = str(row["row_key"])
        minimum = minimum or key
        maximum = key
        kind = str(row["row_kind"])
        label = str(row["label_or_type"])
        digest.update(str(row["row_checksum"]).encode("ascii"))
        digest.update(b"\n")
        count += 1
    if count == 0:
        kind = "node" if partition.startswith("node:") else "relationship"
        label = partition.split(":", 1)[1]
    connection.execute(
        """
        INSERT INTO graph_export_partitions(
          build_id, partition_key, row_kind, label_or_type, row_count,
          min_key, max_key, checksum, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(build_id, partition_key) DO UPDATE SET
          row_kind=excluded.row_kind, label_or_type=excluded.label_or_type,
          row_count=excluded.row_count, min_key=excluded.min_key, max_key=excluded.max_key,
          checksum=excluded.checksum, generated_at=excluded.generated_at
        """,
        (build_id, partition, kind, label, count, minimum, maximum, digest.hexdigest(), utcnow_iso()),
    )


async def freeze_graph_exports(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    partitions: tuple[str, ...] | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    selected = partitions or PARTITIONS
    unknown = set(selected) - set(PARTITIONS)
    if unknown:
        raise CatalogError(f"unknown graph export partitions: {sorted(unknown)}")
    counts: dict[str, int] = {}
    batches = 0
    for partition in selected:
        emit_progress(
            progress,
            "graph_export",
            "started",
            build_id=build_id,
            message=partition,
            counters={"partition": partition},
        )
        last = await writer.execute(
            lambda connection, value=partition: _last_key(
                connection, build_id, "graph_export", value
            ),
            transactional=False,
        )
        batch: list[ExportRow] = []
        with closing(connect_catalog_read_only(writer.path)) as connection:
            iterator = (
                _node_rows(connection, build_id, partition)
                if partition.startswith("node:")
                else _relationship_rows(connection, build_id, partition)
            )
            for row in iterator:
                if last and row.row_key <= last:
                    continue
                batch.append(row)
                if len(batch) >= settings.build_neo4j_batch:
                    await writer.execute(
                        lambda catalog, value=list(batch), key=partition: _insert_export_batch(
                            catalog, build_id, key, value
                        )
                    )
                    batches += 1
                    emit_progress(
                        progress,
                        "graph_export",
                        "progress",
                        build_id=build_id,
                        message=partition,
                        counters={
                            "partition": partition,
                            "batch_rows": len(batch),
                            "batches": batches,
                            "last_key": batch[-1].row_key,
                        },
                    )
                    limit = os.getenv("DEXT_TEST_KILL_AFTER_EXPORT_BATCHES")
                    if limit and batches >= int(limit):
                        os._exit(98)
                    batch.clear()
            if batch:
                await writer.execute(
                    lambda catalog, value=list(batch), key=partition: _insert_export_batch(
                        catalog, build_id, key, value
                    )
                )
                batches += 1
                emit_progress(
                    progress,
                    "graph_export",
                    "progress",
                    build_id=build_id,
                    message=partition,
                    counters={
                        "partition": partition,
                        "batch_rows": len(batch),
                        "batches": batches,
                        "last_key": batch[-1].row_key,
                    },
                )
                limit = os.getenv("DEXT_TEST_KILL_AFTER_EXPORT_BATCHES")
                if limit and batches >= int(limit):
                    os._exit(98)
        await writer.execute(
            lambda connection, value=partition: _finalize_partition(connection, build_id, value)
        )
        counts[partition] = await writer.execute(
            lambda connection, value=partition: int(
                connection.execute(
                    "SELECT row_count FROM graph_export_partitions WHERE build_id=? AND partition_key=?",
                    (build_id, value),
                ).fetchone()[0]
            ),
            transactional=False,
        )
        emit_progress(
            progress,
            "graph_export",
            "completed",
            build_id=build_id,
            message=partition,
            current=counts[partition],
            total=counts[partition],
            counters={"partition": partition, "rows": counts[partition]},
        )
    if partitions is None:
        await writer.execute(
            lambda connection: set_graph_export_pruned(connection, build_id, False)
        )
    return counts


def _reset_graph_partitions(
    connection: sqlite3.Connection, build_id: str, partitions: tuple[str, ...]
) -> None:
    if not partitions:
        return
    placeholders = ",".join("?" for _ in partitions)
    params = (build_id, *partitions)
    connection.execute(
        f"DELETE FROM graph_export_rows WHERE build_id=? AND partition_key IN ({placeholders})",
        params,
    )
    connection.execute(
        f"DELETE FROM graph_export_partitions WHERE build_id=? AND partition_key IN ({placeholders})",
        params,
    )
    connection.execute(
        f"DELETE FROM sink_checkpoints WHERE build_id=? AND sink IN ('graph_export','neo4j') "
        f"AND partition_key IN ({placeholders})",
        params,
    )


def reset_graph_exports(connection: sqlite3.Connection, build_id: str) -> None:
    connection.execute("DELETE FROM graph_export_rows WHERE build_id=?", (build_id,))
    connection.execute("DELETE FROM graph_export_partitions WHERE build_id=?", (build_id,))
    connection.execute(
        "DELETE FROM sink_checkpoints WHERE build_id=? AND sink IN ('graph_export','neo4j')",
        (build_id,),
    )


def graph_export_pruned(connection: sqlite3.Connection, build_id: str) -> bool:
    row = connection.execute(
        "SELECT summary_json FROM graph_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if row is None:
        return False
    return bool(json_loads(row["summary_json"], {}).get("export_pruned"))


def _partition_summary(connection: sqlite3.Connection, build_id: str) -> dict[str, Any]:
    return {
        row["partition_key"]: {
            "row_count": int(row["row_count"]),
            "checksum": row["checksum"],
            "min_key": row["min_key"],
            "max_key": row["max_key"],
        }
        for row in connection.execute(
            "SELECT * FROM graph_export_partitions WHERE build_id=? ORDER BY partition_key",
            (build_id,),
        )
    }


def set_graph_export_pruned(
    connection: sqlite3.Connection, build_id: str, pruned: bool
) -> None:
    row = connection.execute(
        "SELECT summary_json FROM graph_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if row is None:
        return
    graph_summary = json_loads(row["summary_json"], {})
    if pruned:
        graph_summary["export_pruned"] = True
        graph_summary["partitions"] = {}
    else:
        graph_summary.pop("export_pruned", None)
        graph_summary["partitions"] = _partition_summary(connection, build_id)
    connection.execute(
        "UPDATE graph_runs SET summary_json=? WHERE build_id=?",
        (json_dumps(graph_summary), build_id),
    )
    build_row = connection.execute(
        "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build_row is not None:
        build_summary = json_loads(build_row["summary_json"], {})
        build_summary["graph"] = graph_summary
        connection.execute(
            "UPDATE graph_builds SET summary_json=? WHERE id=?",
            (json_dumps(build_summary), build_id),
        )


async def ensure_graph_exports_available(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    progress: ProgressCallback | None = None,
) -> bool:
    pruned = await writer.execute(
        lambda connection: graph_export_pruned(connection, build_id),
        transactional=False,
    )
    if not pruned:
        return False
    await writer.execute(lambda connection: reset_graph_exports(connection, build_id))
    await freeze_graph_exports(writer, build_id, settings, progress=progress)
    return True


async def rebuild_graph_partitions(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    partitions: tuple[str, ...],
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, int]:
    unknown = set(partitions) - set(PARTITIONS)
    if unknown:
        raise CatalogError(f"unknown graph export partitions: {sorted(unknown)}")
    await writer.execute(
        lambda connection: _reset_graph_partitions(connection, build_id, partitions)
    )
    return await freeze_graph_exports(
        writer, build_id, settings, partitions=partitions, progress=progress
    )


__all__ = [
    "EVIDENCE_VERSION",
    "EXPORT_VERSION",
    "PARTITIONS",
    "detect_language",
    "ensure_graph_exports_available",
    "freeze_graph_exports",
    "graph_export_pruned",
    "materialize_evidence",
    "rebuild_graph_partitions",
    "reset_graph_exports",
    "set_graph_export_pruned",
    "split_publication_mentions",
    "split_research_statements",
]
