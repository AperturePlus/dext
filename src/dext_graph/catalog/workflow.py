"""Stage-1 catalog build orchestration, checkpointing, and status reporting."""

from __future__ import annotations

import asyncio
import io
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dext.seed import db_filename, get_university, load_manifest, resolve_abbr
from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    backup_existing_catalog,
    catalog_write_lock,
    connect_catalog_read_only,
    ensure_free_space,
    initialize_catalog,
    json_dumps,
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.ids import (
    canonical_json,
    canonical_json_hash,
    canonical_source_url,
    finding_id,
    hash_parts,
    observation_id,
    sha256_bytes,
    source_document_id,
    uuid7,
)
from dext_graph.catalog.models import BuildSource, CATALOG_SCHEMA_VERSION
from dext_graph.catalog.progress import ProgressCallback, emit_progress
from dext_graph.catalog.source import (
    LegacySourceRow,
    SnapshotInspection,
    create_source_snapshot,
    inspect_snapshot,
    iter_legacy_batches,
)
from dext_graph.config import GraphSettings

CURATION_VERSION = "curation-v1"
GRAPH_SCHEMA_VERSION = 1
VECTOR_SCHEMA_VERSION = 1
_PEAK_RSS_BYTES = 0
_RESUME_SETTING_KEYS = (
    "build_read_batch",
    "build_write_queue",
    "build_max_rss_mb",
    "build_min_free_disk_mb",
    "build_min_source_retention_ratio",
    "curation_queue",
    "embedding_base_url",
    "embedding_model",
    "embedding_dimension",
    "embedding_max_input_tokens",
    "embedding_passage_prefix",
    "tokenizer_model",
    "tokenizer_revision",
    "profile_max_tokens",
    "qdrant_url",
    "bm25_tokenizer_version",
    "taxonomy_path",
    "topic_candidate_top_k",
    "topic_merge_min_score",
    "neo4j_uri",
    "neo4j_database",
    "build_neo4j_batch",
)


@dataclass(frozen=True)
class _DocumentRecord:
    id: str
    university_id: str
    url: str
    content_hash: str
    title: str | None
    text_zstd: bytes | None
    fetched_at: str | None


@dataclass(frozen=True)
class _ObservationRecord:
    id: str
    university_id: str
    source_snapshot_id: str
    source_professor_id: int
    source_url: str | None
    source_content_hash: str | None
    source_document_id: str | None
    org_unit_source_id: int | None
    name_raw: str
    name_key: str
    payload_json: str
    row_hash: str


@dataclass(frozen=True)
class _FindingRecord:
    id: str
    severity: str
    code: str
    details_json: str
    source_snapshot_id: str


@dataclass(frozen=True)
class _PreparedBatch:
    first_key: int
    last_key: int
    source_rows: int
    documents: tuple[_DocumentRecord, ...]
    observations: tuple[_ObservationRecord, ...]
    findings: tuple[_FindingRecord, ...]


def _safe_error(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (text or type(exc).__name__)[:2000]


def _check_rss(settings: GraphSettings) -> int:
    import psutil

    global _PEAK_RSS_BYTES
    rss = int(psutil.Process(os.getpid()).memory_info().rss)
    _PEAK_RSS_BYTES = max(_PEAK_RSS_BYTES, rss)
    limit = settings.build_max_rss_mb * 1024 * 1024
    if rss > limit:
        raise CatalogError(
            f"build RSS {rss / 1024 / 1024:.1f} MiB exceeds configured limit "
            f"{settings.build_max_rss_mb} MiB"
        )
    return rss


def _reset_peak_rss() -> None:
    global _PEAK_RSS_BYTES
    _PEAK_RSS_BYTES = 0


def _normalize_source_time(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _compress_text(value: str | None) -> bytes | None:
    if value is None:
        return None
    import zstandard

    target = io.BytesIO()
    compressor = zstandard.ZstdCompressor(level=3)
    with compressor.stream_writer(target, closefd=False) as stream:
        start = 0
        while start < len(value):
            newline = value.find("\n", start)
            end = len(value) if newline < 0 else newline + 1
            stream.write(value[start:end].encode("utf-8"))
            start = end
    return target.getvalue()


def resolve_build_sources(
    university_names: list[str] | tuple[str, ...], settings: GraphSettings
) -> list[BuildSource]:
    manifest = load_manifest(settings.seed_path)
    requested = list(dict.fromkeys(university_names))
    universities = (
        [get_university(manifest, name) for name in requested]
        if requested
        else list(manifest.universities)
    )
    sources: list[BuildSource] = []
    for university in universities:
        abbr = resolve_abbr(university)
        path = (settings.source_data_dir / db_filename(abbr)).expanduser().resolve()
        if not path.is_file():
            if requested:
                raise CatalogError(
                    f"source DB for university {university.name!r} does not exist: {path}"
                )
            continue
        sources.append(
            BuildSource(
                university_id=f"univ:{abbr}",
                university_name=university.name,
                abbr=abbr,
                source_path=str(path),
                ordinal=len(sources),
            )
        )
    if not sources:
        raise CatalogError("no canonical university databases were selected")
    ids = [source.university_id for source in sources]
    if len(ids) != len(set(ids)):
        raise CatalogError("selected universities resolve to duplicate abbreviations")
    return sources


def _settings_snapshot(settings: GraphSettings) -> dict[str, Any]:
    snapshot = settings.safe_snapshot()
    snapshot["catalog_schema_version"] = CATALOG_SCHEMA_VERSION
    snapshot["curation_version"] = CURATION_VERSION
    snapshot["graph_schema_version"] = GRAPH_SCHEMA_VERSION
    snapshot["vector_schema_version"] = VECTOR_SCHEMA_VERSION
    return snapshot


def _insert_build(
    connection: sqlite3.Connection,
    build_id: str,
    sources: list[BuildSource],
    settings: GraphSettings,
) -> None:
    now = utcnow_iso()
    connection.execute(
        """
        INSERT INTO graph_builds(
          id, status, source_manifest_hash, curation_version, taxonomy_version,
          graph_schema_version, vector_schema_version, embedding_provider,
          embedding_base_url, embedding_model, embedding_fingerprint,
          embedding_dimension, settings_json, summary_json, started_at,
          finished_at, last_error
        ) VALUES (?, 'CREATED', NULL, ?, NULL, ?, ?, 'siliconflow', ?, ?, NULL, ?, ?, '{}', ?, NULL, NULL)
        """,
        (
            build_id,
            CURATION_VERSION,
            GRAPH_SCHEMA_VERSION,
            VECTOR_SCHEMA_VERSION,
            settings.embedding_base_url,
            settings.embedding_model,
            settings.embedding_dimension,
            json_dumps(_settings_snapshot(settings)),
            now,
        ),
    )
    for source in sources:
        connection.execute(
            """
            INSERT INTO build_source_tasks(
              build_id, university_id, university_name, abbr, source_path,
              ordinal, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (
                build_id,
                source.university_id,
                source.university_name,
                source.abbr,
                source.source_path,
                source.ordinal,
                now,
            ),
        )


def _load_build(connection: sqlite3.Connection, build_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM graph_builds WHERE id=?", (build_id,)).fetchone()
    if row is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    value = dict(row)
    value["settings_json"] = json_loads(value["settings_json"], {})
    value["summary_json"] = json_loads(value["summary_json"], {})
    return value


def _load_tasks(connection: sqlite3.Connection, build_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM build_source_tasks WHERE build_id=? ORDER BY ordinal",
            (build_id,),
        )
    ]


def _update_build_status(
    connection: sqlite3.Connection,
    build_id: str,
    status: str,
    *,
    error: str | None = None,
    manifest_hash: str | None = None,
    summary: dict[str, Any] | None = None,
) -> None:
    finished = utcnow_iso() if status == "FAILED" else None
    connection.execute(
        """
        UPDATE graph_builds
        SET status=?, last_error=?, finished_at=?,
            source_manifest_hash=COALESCE(?, source_manifest_hash),
            summary_json=COALESCE(?, summary_json)
        WHERE id=?
        """,
        (
            status,
            error,
            finished,
            manifest_hash,
            json_dumps(summary) if summary is not None else None,
            build_id,
        ),
    )


def _set_task_state(
    connection: sqlite3.Connection,
    build_id: str,
    university_id: str,
    status: str,
    error: str | None = None,
) -> None:
    connection.execute(
        "UPDATE build_source_tasks SET status=?, last_error=?, updated_at=? "
        "WHERE build_id=? AND university_id=?",
        (status, error, utcnow_iso(), build_id, university_id),
    )


def _manifest(connection: sqlite3.Connection, build_id: str) -> tuple[str, list[dict[str, Any]]]:
    rows = connection.execute(
        """
        SELECT t.university_id, s.id, s.file_hash, s.schema_version, s.row_counts_json
        FROM build_source_tasks t
        JOIN source_snapshots s ON s.id=t.source_snapshot_id
        WHERE t.build_id=? ORDER BY t.university_id
        """,
        (build_id,),
    ).fetchall()
    tasks = connection.execute(
        "SELECT COUNT(*) FROM build_source_tasks WHERE build_id=?", (build_id,)
    ).fetchone()[0]
    if len(rows) != tasks:
        raise CatalogError("cannot finalize source manifest before every snapshot is registered")
    manifest = [
        {
            "university_id": row["university_id"],
            "source_snapshot_id": row["id"],
            "file_hash": row["file_hash"],
            "schema_version": row["schema_version"],
            "row_counts": json_loads(row["row_counts_json"], {}),
        }
        for row in rows
    ]
    return canonical_json_hash(manifest), manifest


def _register_snapshot(
    connection: sqlite3.Connection,
    build_id: str,
    task: dict[str, Any],
    result,
) -> None:
    inspection = result.inspection
    connection.execute(
        """
        INSERT INTO source_snapshots(
          id, university_id, source_path, snapshot_path, file_hash,
          schema_version, row_counts_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            result.id,
            task["university_id"],
            task["source_path"],
            str(result.path),
            result.file_hash,
            inspection.schema_version,
            json_dumps(inspection.row_counts),
            utcnow_iso(),
        ),
    )
    existing = connection.execute(
        "SELECT file_hash, snapshot_path FROM source_snapshots WHERE id=?", (result.id,)
    ).fetchone()
    if existing is None or existing["file_hash"] != result.file_hash:
        raise CatalogError("catalog snapshot identity collision")
    connection.execute(
        "INSERT OR IGNORE INTO build_source_snapshots(build_id, source_snapshot_id) VALUES (?, ?)",
        (build_id, result.id),
    )
    connection.execute(
        """
        UPDATE build_source_tasks
        SET source_snapshot_id=?, snapshot_reused=?, status='SNAPSHOTTED',
            last_error=NULL, updated_at=?
        WHERE build_id=? AND university_id=?
        """,
        (
            result.id,
            int(result.reused_file),
            utcnow_iso(),
            build_id,
            task["university_id"],
        ),
    )


def _validate_snapshot_identity(task: dict[str, Any], inspection: SnapshotInspection) -> None:
    if inspection.university_name != task["university_name"]:
        raise CatalogError(
            f"source metadata university {inspection.university_name!r} does not match "
            f"selected university {task['university_name']!r}"
        )
    if inspection.abbreviation and inspection.abbreviation != task["abbr"]:
        raise CatalogError(
            f"source metadata abbreviation {inspection.abbreviation!r} does not match "
            f"selected abbreviation {task['abbr']!r}"
        )


def _prepare_row(
    row: LegacySourceRow,
    *,
    build_id: str,
    university_id: str,
    source_snapshot_id_value: str,
) -> tuple[_DocumentRecord | None, _ObservationRecord]:
    raw_name = row.payload.get("name")
    if raw_name is None or not str(raw_name).strip():
        raise ValueError("source professor name is empty")
    name_raw = str(raw_name)
    from dext_graph.catalog.ids import name_key as make_name_key

    normalized_name = make_name_key(name_raw)
    if not normalized_name:
        raise ValueError("source professor name normalizes to an empty key")
    payload = dict(row.payload)
    payload["affiliations"] = list(row.affiliations)
    payload_json = canonical_json(payload)
    row_hash = sha256_bytes(payload_json.encode("utf-8"))
    source_url = canonical_source_url(row.payload.get("homepage"))
    source_identity = source_url if source_url is not None else str(row.source_id)
    document = None
    document_id = None
    content_hash = row.page_content_hash.strip() if row.page_content_hash else None
    canonical_page_url = canonical_source_url(row.page_url)
    if source_url and canonical_page_url and content_hash:
        document_id = source_document_id(canonical_page_url, content_hash)
        document = _DocumentRecord(
            id=document_id,
            university_id=university_id,
            url=canonical_page_url,
            content_hash=content_hash,
            title=row.page_title,
            text_zstd=_compress_text(row.page_text),
            fetched_at=_normalize_source_time(row.page_fetched_at),
        )
    return document, _ObservationRecord(
        id=observation_id(university_id, source_identity, normalized_name, row_hash),
        university_id=university_id,
        source_snapshot_id=source_snapshot_id_value,
        source_professor_id=row.source_id,
        source_url=source_url,
        source_content_hash=content_hash if document_id else None,
        source_document_id=document_id,
        org_unit_source_id=row.primary_org_unit_id,
        name_raw=name_raw,
        name_key=normalized_name,
        payload_json=payload_json,
        row_hash=row_hash,
    )


def _prepare_batch(
    rows: list[LegacySourceRow],
    *,
    build_id: str,
    university_id: str,
    source_snapshot_id_value: str,
) -> _PreparedBatch:
    documents: list[_DocumentRecord] = []
    observations: list[_ObservationRecord] = []
    findings: list[_FindingRecord] = []
    for row in rows:
        try:
            document, observation = _prepare_row(
                row,
                build_id=build_id,
                university_id=university_id,
                source_snapshot_id_value=source_snapshot_id_value,
            )
        except (TypeError, ValueError, UnicodeError) as exc:
            reference = f"{source_snapshot_id_value}:professors:{row.source_id}"
            findings.append(
                _FindingRecord(
                    id=finding_id(build_id, "legacy_row_unimportable", reference),
                    severity="error",
                    code="legacy_row_unimportable",
                    details_json=json_dumps(
                        {
                            "source_snapshot_id": source_snapshot_id_value,
                            "source_professor_id": row.source_id,
                            "reason": _safe_error(exc),
                        }
                    ),
                    source_snapshot_id=source_snapshot_id_value,
                )
            )
            continue
        if document is not None:
            documents.append(document)
        observations.append(observation)
    return _PreparedBatch(
        first_key=rows[0].source_id,
        last_key=rows[-1].source_id,
        source_rows=len(rows),
        documents=tuple(documents),
        observations=tuple(observations),
        findings=tuple(findings),
    )


def _commit_batch(
    connection: sqlite3.Connection,
    build_id: str,
    university_id: str,
    batch: _PreparedBatch,
) -> dict[str, int]:
    documents_inserted = 0
    observations_inserted = 0
    observations_reused = 0
    findings_inserted = 0
    for document in batch.documents:
        cursor = connection.execute(
            """
            INSERT INTO source_documents(
              id, university_id, url, content_hash, title, text_zstd, fetched_at,
              first_seen_build, last_seen_build
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                document.id,
                document.university_id,
                document.url,
                document.content_hash,
                document.title,
                document.text_zstd,
                document.fetched_at,
                build_id,
                build_id,
            ),
        )
        documents_inserted += max(cursor.rowcount, 0)
        connection.execute(
            """
            UPDATE source_documents
            SET last_seen_build=?, title=COALESCE(title, ?),
                text_zstd=COALESCE(text_zstd, ?), fetched_at=COALESCE(fetched_at, ?)
            WHERE id=?
            """,
            (build_id, document.title, document.text_zstd, document.fetched_at, document.id),
        )
    for observation in batch.observations:
        cursor = connection.execute(
            """
            INSERT INTO professor_observations(
              id, university_id, source_snapshot_id, source_professor_id,
              source_url, source_page_kind, extraction_batch_size,
              source_content_hash, source_document_id, org_unit_source_id,
              name_raw, name_key, payload_json, row_hash, provenance_grade,
              first_seen_build, last_seen_build, active
            ) VALUES (?, ?, ?, ?, ?, 'unknown', NULL, ?, ?, ?, ?, ?, ?, ?,
                      'legacy_merged', ?, ?, 1)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                observation.id,
                observation.university_id,
                observation.source_snapshot_id,
                observation.source_professor_id,
                observation.source_url,
                observation.source_content_hash,
                observation.source_document_id,
                observation.org_unit_source_id,
                observation.name_raw,
                observation.name_key,
                observation.payload_json,
                observation.row_hash,
                build_id,
                build_id,
            ),
        )
        inserted = max(cursor.rowcount, 0)
        observations_inserted += inserted
        observations_reused += int(inserted == 0)
        if inserted == 0:
            connection.execute(
                "UPDATE professor_observations SET last_seen_build=?, active=1 WHERE id=?",
                (build_id, observation.id),
            )
    for finding in batch.findings:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO quality_findings(
              id, build_id, severity, code, entity_id, observation_id,
              details_json, resolved
            ) VALUES (?, ?, ?, ?, NULL, NULL, ?, 0)
            """,
            (
                finding.id,
                build_id,
                finding.severity,
                finding.code,
                finding.details_json,
            ),
        )
        findings_inserted += max(cursor.rowcount, 0)
        if cursor.rowcount > 0:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_snapshot_protections(
                  source_snapshot_id, protection_kind, reference_id, created_at
                ) VALUES (?, 'unresolved_finding', ?, ?)
                """,
                (finding.source_snapshot_id, finding.id, utcnow_iso()),
            )
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id, sink, partition_key, last_key, last_batch_id, rows_written, updated_at
        ) VALUES (?, 'observations', ?, ?, ?, ?, ?)
        ON CONFLICT(build_id, sink, partition_key) DO UPDATE SET
          last_key=excluded.last_key,
          last_batch_id=excluded.last_batch_id,
          rows_written=sink_checkpoints.rows_written + excluded.rows_written,
          updated_at=excluded.updated_at
        """,
        (
            build_id,
            university_id,
            str(batch.last_key),
            hash_parts(build_id, "observations", university_id, batch.first_key, batch.last_key),
            len(batch.observations),
            utcnow_iso(),
        ),
    )
    connection.execute(
        """
        UPDATE build_source_tasks SET
          rows_read=rows_read+?, observations_written=observations_written+?,
          observations_inserted=observations_inserted+?,
          observations_reused=observations_reused+?, documents_seen=documents_seen+?,
          documents_inserted=documents_inserted+?, findings=findings+?,
          rejected_rows=rejected_rows+?, updated_at=?
        WHERE build_id=? AND university_id=?
        """,
        (
            batch.source_rows,
            len(batch.observations),
            observations_inserted,
            observations_reused,
            len(batch.documents),
            documents_inserted,
            findings_inserted,
            len(batch.findings),
            utcnow_iso(),
            build_id,
            university_id,
        ),
    )
    return {
        "documents_inserted": documents_inserted,
        "observations_inserted": observations_inserted,
        "observations_reused": observations_reused,
        "findings_inserted": findings_inserted,
    }


def _record_capability_findings(
    connection: sqlite3.Connection,
    build_id: str,
    task: dict[str, Any],
    inspection: SnapshotInspection,
) -> int:
    inserted = 0
    capabilities = [
        *(f"missing_table:{table}" for table in inspection.missing_optional_tables),
        *(f"incomplete:{item}" for item in inspection.incomplete_capabilities),
    ]
    for capability in capabilities:
        reference = f"{task['source_snapshot_id']}:{capability}"
        finding_key = finding_id(build_id, "source_capability_missing", reference)
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO quality_findings(
              id, build_id, severity, code, entity_id, observation_id,
              details_json, resolved
            ) VALUES (?, ?, 'warning', 'source_capability_missing', NULL, NULL, ?, 0)
            """,
            (
                finding_key,
                build_id,
                json_dumps(
                    {
                        "source_snapshot_id": task["source_snapshot_id"],
                        "university_id": task["university_id"],
                        "capability": capability,
                    }
                ),
            ),
        )
        inserted += max(cursor.rowcount, 0)
        if cursor.rowcount > 0:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_snapshot_protections(
                  source_snapshot_id, protection_kind, reference_id, created_at
                ) VALUES (?, 'unresolved_finding', ?, ?)
                """,
                (task["source_snapshot_id"], finding_key, utcnow_iso()),
            )
    if inserted:
        connection.execute(
            "UPDATE build_source_tasks SET findings=findings+?, updated_at=? "
            "WHERE build_id=? AND university_id=?",
            (inserted, utcnow_iso(), build_id, task["university_id"]),
        )
    return inserted


def _finalize_source(
    connection: sqlite3.Connection,
    build_id: str,
    task: dict[str, Any],
    inspection: SnapshotInspection,
    retention_ratio: float,
) -> dict[str, Any]:
    current = connection.execute(
        "SELECT * FROM build_source_tasks WHERE build_id=? AND university_id=?",
        (build_id, task["university_id"]),
    ).fetchone()
    if current is None:
        raise CatalogError("build source task disappeared during ingest")
    professor_count = int(inspection.row_counts.get("professors") or 0)
    reasons: list[str] = []
    if inspection.crawl_status != "completed":
        reasons.append(f"crawl_status:{inspection.crawl_status}")
    if not inspection.deactivation_state_is_complete():
        reasons.append("crawler_state_incomplete_or_unavailable")
    if int(current["rejected_rows"]) != 0:
        reasons.append("row_rejections")
    if professor_count == 0:
        reasons.append("zero_professors")
    previous = connection.execute(
        """
        SELECT t.rows_read, t.source_snapshot_id, b.started_at
        FROM build_source_tasks t JOIN graph_builds b ON b.id=t.build_id
        WHERE t.university_id=? AND t.build_id<>? AND t.status='COMPLETED'
          AND t.deactivation_eligible=1
        ORDER BY b.started_at DESC LIMIT 1
        """,
        (task["university_id"], build_id),
    ).fetchone()
    observed_ratio: float | None = None
    if previous is not None and int(previous["rows_read"]) > 0:
        observed_ratio = professor_count / int(previous["rows_read"])
        if observed_ratio < retention_ratio:
            reasons.append("source_count_regression")
    eligible = not reasons
    connection.execute(
        """
        UPDATE professor_observations
        SET source_page_kind='multi_profile'
        WHERE university_id=? AND source_page_kind='unknown'
          AND source_document_id IN (
            SELECT source_document_id FROM professor_observations
            WHERE university_id=? AND active=1 AND source_document_id IS NOT NULL
            GROUP BY source_document_id HAVING COUNT(DISTINCT name_key)>1
          )
        """,
        (task["university_id"], task["university_id"]),
    )
    inactivated = 0
    finding_added = 0
    if eligible:
        cursor = connection.execute(
            """
            UPDATE professor_observations SET active=0
            WHERE university_id=? AND active=1 AND last_seen_build<>?
            """,
            (task["university_id"], build_id),
        )
        inactivated = max(cursor.rowcount, 0)
    else:
        reference = f"{task['source_snapshot_id']}:deactivation"
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO quality_findings(
              id, build_id, severity, code, entity_id, observation_id,
              details_json, resolved
            ) VALUES (?, ?, 'warning', 'source_deactivation_blocked', NULL, NULL, ?, 0)
            """,
            (
                finding_id(build_id, "source_deactivation_blocked", reference),
                build_id,
                json_dumps(
                    {
                        "university_id": task["university_id"],
                        "source_snapshot_id": task["source_snapshot_id"],
                        "reasons": reasons,
                        "professor_count": professor_count,
                        "previous_trusted_count": (
                            int(previous["rows_read"]) if previous is not None else None
                        ),
                        "retention_ratio": observed_ratio,
                        "minimum_retention_ratio": retention_ratio,
                    }
                ),
            ),
        )
        finding_added = max(cursor.rowcount, 0)
        if cursor.rowcount > 0:
            connection.execute(
                """
                INSERT OR IGNORE INTO source_snapshot_protections(
                  source_snapshot_id, protection_kind, reference_id, created_at
                ) VALUES (?, 'unresolved_finding', ?, ?)
                """,
                (
                    task["source_snapshot_id"],
                    finding_id(build_id, "source_deactivation_blocked", reference),
                    utcnow_iso(),
                ),
            )
    connection.execute(
        """
        UPDATE build_source_tasks
        SET status='COMPLETED', deactivation_eligible=?, observations_inactivated=?,
            findings=findings+?, last_error=NULL, updated_at=?
        WHERE build_id=? AND university_id=?
        """,
        (
            int(eligible),
            inactivated,
            finding_added,
            utcnow_iso(),
            build_id,
            task["university_id"],
        ),
    )
    return {
        "deactivation_eligible": eligible,
        "inactivated": inactivated,
        "reasons": reasons,
        "retention_ratio": observed_ratio,
    }


async def _ingest_source(
    writer: CatalogWriter,
    build_id: str,
    task: dict[str, Any],
    settings: GraphSettings,
    *,
    progress: ProgressCallback | None = None,
) -> None:
    snapshot_row = await writer.execute(
        lambda connection: connection.execute(
            "SELECT snapshot_path FROM source_snapshots WHERE id=?",
            (task["source_snapshot_id"],),
        ).fetchone(),
        transactional=False,
    )
    if snapshot_row is None:
        raise CatalogError("registered source snapshot is missing from catalog")
    snapshot_path = Path(snapshot_row["snapshot_path"])
    if not snapshot_path.is_file():
        raise CatalogError(f"source snapshot file is missing: {snapshot_path}")
    inspection = inspect_snapshot(snapshot_path)
    _validate_snapshot_identity(task, inspection)
    await writer.execute(
        lambda connection: _record_capability_findings(
            connection, build_id, task, inspection
        )
    )
    checkpoint = await writer.execute(
        lambda connection: connection.execute(
            "SELECT last_key FROM sink_checkpoints "
            "WHERE build_id=? AND sink='observations' AND partition_key=?",
            (build_id, task["university_id"]),
        ).fetchone(),
        transactional=False,
    )
    last_key = int(checkpoint["last_key"]) if checkpoint and checkpoint["last_key"] else 0
    professor_count = int(inspection.row_counts.get("professors") or 0)
    processed_rows = int(task.get("rows_read") or 0)
    emit_progress(
        progress,
        "ingest",
        "started",
        build_id=build_id,
        message=str(task["university_id"]),
        current=processed_rows,
        total=professor_count,
        counters={"university_id": task["university_id"]},
    )
    queue: asyncio.Queue[_PreparedBatch | None] = asyncio.Queue(
        maxsize=settings.build_write_queue
    )

    async def producer() -> None:
        for rows in iter_legacy_batches(
            snapshot_path,
            inspection,
            last_key=last_key,
            batch_size=settings.build_read_batch,
        ):
            _check_rss(settings)
            prepared = _prepare_batch(
                rows,
                build_id=build_id,
                university_id=task["university_id"],
                source_snapshot_id_value=task["source_snapshot_id"],
            )
            _check_rss(settings)
            await queue.put(prepared)
        await queue.put(None)

    async def consumer() -> None:
        nonlocal processed_rows
        commits = 0
        while True:
            prepared = await queue.get()
            try:
                if prepared is None:
                    return
                stats = await writer.execute(
                    lambda connection, batch=prepared: _commit_batch(
                        connection, build_id, task["university_id"], batch
                    )
                )
                processed_rows += prepared.source_rows
                emit_progress(
                    progress,
                    "ingest",
                    "progress",
                    build_id=build_id,
                    message=str(task["university_id"]),
                    current=processed_rows,
                    total=professor_count,
                    counters={
                        "batch_rows": prepared.source_rows,
                        "observations": len(prepared.observations),
                        "documents": len(prepared.documents),
                        "findings": len(prepared.findings),
                        **stats,
                    },
                )
                commits += 1
                _check_rss(settings)
                kill_after = os.getenv("DEXT_TEST_KILL_AFTER_INGEST_COMMITS")
                if kill_after and commits >= int(kill_after):
                    os._exit(92)
            finally:
                queue.task_done()

    async with asyncio.TaskGroup() as group:
        group.create_task(producer())
        group.create_task(consumer())
    finalize = await writer.execute(
        lambda connection: _finalize_source(
            connection,
            build_id,
            task,
            inspection,
            settings.build_min_source_retention_ratio,
        )
    )
    emit_progress(
        progress,
        "ingest",
        "completed",
        build_id=build_id,
        message=str(task["university_id"]),
        current=professor_count,
        total=professor_count,
        counters=finalize,
    )


def _build_summary(connection: sqlite3.Connection, build_id: str, rss: int) -> dict[str, Any]:
    tasks = _load_tasks(connection, build_id)
    return {
        "source_count": len(tasks),
        "source_snapshots_reused": sum(int(task["snapshot_reused"]) for task in tasks),
        "rows_read": sum(int(task["rows_read"]) for task in tasks),
        "observations_written": sum(int(task["observations_written"]) for task in tasks),
        "observations_inserted": sum(int(task["observations_inserted"]) for task in tasks),
        "observations_reused": sum(int(task["observations_reused"]) for task in tasks),
        "documents_seen": sum(int(task["documents_seen"]) for task in tasks),
        "documents_inserted": sum(int(task["documents_inserted"]) for task in tasks),
        "observations_inactivated": sum(
            int(task["observations_inactivated"]) for task in tasks
        ),
        "quality_findings": sum(int(task["findings"]) for task in tasks),
        "deactivation_eligible_sources": sum(
            int(task["deactivation_eligible"]) for task in tasks
        ),
        "peak_observed_rss_bytes": rss,
        "sources": [
            {
                "university_id": task["university_id"],
                "status": task["status"],
                "rows_read": task["rows_read"],
                "findings": task["findings"],
                "deactivation_eligible": bool(task["deactivation_eligible"]),
                "last_error": task["last_error"],
            }
            for task in tasks
        ],
    }


async def _run_build(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    build = await writer.execute(
        lambda connection: _load_build(connection, build_id), transactional=False
    )
    if build["status"] == "CURATING":
        emit_progress(
            progress,
            "build",
            "completed",
            build_id=build_id,
            message="stage 1 already completed",
            counters={"status": build["status"]},
        )
        return build_status(settings.catalog_path, build_id)
    if build["status"] not in {"CREATED", "SNAPSHOTTING", "INGESTING", "FAILED"}:
        raise CatalogError(
            f"build {build_id} in status {build['status']} cannot be resumed by stage 1"
        )
    tasks = await writer.execute(
        lambda connection: _load_tasks(connection, build_id), transactional=False
    )
    emit_progress(
        progress,
        "build",
        "started",
        build_id=build_id,
        message="stage 1 source snapshot and ingest",
        total=len(tasks),
        counters={"status": build["status"], "sources": len(tasks)},
    )
    if any(task["source_snapshot_id"] is None for task in tasks):
        await writer.execute(
            lambda connection: _update_build_status(
                connection, build_id, "SNAPSHOTTING", error=None
            )
        )
        snapshot_errors: list[str] = []
        for task in tasks:
            if task["source_snapshot_id"] is not None:
                continue
            emit_progress(
                progress,
                "snapshot",
                "started",
                build_id=build_id,
                message=str(task["university_id"]),
                counters={
                    "university_id": task["university_id"],
                    "source_path": task["source_path"],
                },
            )
            await writer.execute(
                lambda connection, current=task: _set_task_state(
                    connection,
                    build_id,
                    current["university_id"],
                    "SNAPSHOTTING",
                )
            )
            chunks = 0

            def progress_hook(status: int, remaining: int, total: int) -> None:
                nonlocal chunks
                chunks += 1
                _check_rss(settings)
                emit_progress(
                    progress,
                    "snapshot",
                    "progress",
                    build_id=build_id,
                    message=str(task["university_id"]),
                    current=max(total - remaining, 0),
                    total=total,
                    counters={
                        "sqlite_status": status,
                        "chunks": chunks,
                    },
                )
                kill_after = os.getenv("DEXT_TEST_KILL_AFTER_BACKUP_CHUNKS")
                if kill_after and chunks >= int(kill_after):
                    os._exit(91)

            try:
                result = await create_source_snapshot(
                    task["source_path"],
                    settings.catalog_path.parent / "source-snapshots",
                    university_id=task["university_id"],
                    abbr=task["abbr"],
                    build_id=build_id,
                    progress_hook=progress_hook,
                )
                _validate_snapshot_identity(task, result.inspection)
                await writer.execute(
                    lambda connection, current=task, snapshot=result: _register_snapshot(
                        connection, build_id, current, snapshot
                    )
                )
                emit_progress(
                    progress,
                    "snapshot",
                    "completed",
                    build_id=build_id,
                    message=str(task["university_id"]),
                    counters={"snapshot_id": result.id, "reused": result.reused_file},
                )
            except Exception as exc:  # noqa: BLE001 -- persist per-source failure
                error = _safe_error(exc)
                snapshot_errors.append(f"{task['university_id']}: {error}")
                emit_progress(
                    progress,
                    "snapshot",
                    "failed",
                    build_id=build_id,
                    message=str(task["university_id"]),
                    counters={"error": error},
                )
                await writer.execute(
                    lambda connection, current=task, message=error: _set_task_state(
                        connection,
                        build_id,
                        current["university_id"],
                        "FAILED",
                        message,
                    )
                )
        if snapshot_errors:
            message = "; ".join(snapshot_errors)[:2000]
            await writer.execute(
                lambda connection: _update_build_status(
                    connection, build_id, "FAILED", error=message
                )
            )
            return build_status(settings.catalog_path, build_id)
    manifest_hash, manifest = await writer.execute(
        lambda connection: _manifest(connection, build_id), transactional=False
    )
    await writer.execute(
        lambda connection: _update_build_status(
            connection,
            build_id,
            "INGESTING",
            error=None,
            manifest_hash=manifest_hash,
            summary={"source_manifest": manifest},
        )
    )
    tasks = await writer.execute(
        lambda connection: _load_tasks(connection, build_id), transactional=False
    )
    ingest_errors: list[str] = []
    for task in tasks:
        if task["status"] == "COMPLETED":
            continue
        await writer.execute(
            lambda connection, current=task: _set_task_state(
                connection, build_id, current["university_id"], "INGESTING"
            )
        )
        try:
            await _ingest_source(
                writer, build_id, task, settings, progress=progress
            )
        except Exception as exc:  # noqa: BLE001 -- continue other universities
            error = _safe_error(exc)
            ingest_errors.append(f"{task['university_id']}: {error}")
            emit_progress(
                progress,
                "ingest",
                "failed",
                build_id=build_id,
                message=str(task["university_id"]),
                counters={"error": error},
            )
            await writer.execute(
                lambda connection, current=task, message=error: _set_task_state(
                    connection,
                    build_id,
                    current["university_id"],
                    "FAILED",
                    message,
                )
            )
    rss = max(_check_rss(settings), _PEAK_RSS_BYTES)
    summary = await writer.execute(
        lambda connection: _build_summary(connection, build_id, rss),
        transactional=False,
    )
    summary["source_manifest"] = manifest
    if ingest_errors:
        message = "; ".join(ingest_errors)[:2000]
        await writer.execute(
            lambda connection: _update_build_status(
                connection, build_id, "FAILED", error=message, summary=summary
            )
        )
        emit_progress(
            progress,
            "build",
            "failed",
            build_id=build_id,
            message=message,
            counters=_progress_summary(summary),
        )
    else:
        await writer.execute(
            lambda connection: _update_build_status(
                connection, build_id, "CURATING", error=None, summary=summary
            )
        )
        emit_progress(
            progress,
            "build",
            "completed",
            build_id=build_id,
            message="stage 1 completed",
            counters=_progress_summary(summary),
        )
    return build_status(settings.catalog_path, build_id)


def _backup_progress(
    settings: GraphSettings,
    progress_callback: ProgressCallback | None = None,
    *,
    build_id: str | None = None,
    stage: str = "catalog_backup",
    message: str = "catalog backup",
):
    def progress(status: int, remaining: int, total: int) -> None:
        _check_rss(settings)
        emit_progress(
            progress_callback,
            stage,
            "progress",
            build_id=build_id,
            message=message,
            current=max(total - remaining, 0),
            total=total,
            counters={"sqlite_status": status},
        )

    return progress


def _preflight_build_storage(
    sources: list[BuildSource], settings: GraphSettings
) -> None:
    catalog_path = Path(settings.catalog_path).expanduser().resolve()
    catalog_bytes = catalog_path.stat().st_size if catalog_path.is_file() else 0
    try:
        source_bytes = sum(Path(source.source_path).stat().st_size for source in sources)
    except OSError as exc:
        raise CatalogError(f"failed to inspect source database size: {exc}") from exc
    reserve_bytes = settings.build_min_free_disk_mb * 1024 * 1024
    # One source-sized allowance is for immutable snapshots; the second bounds
    # catalog growth while ingesting them. The existing catalog allowance covers
    # the mandatory pre-mutation backup.
    required_bytes = catalog_bytes + (2 * source_bytes) + reserve_bytes
    ensure_free_space(
        catalog_path.parent,
        required_bytes,
        operation="graph build",
    )


def _progress_summary(summary: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source_count",
        "rows_read",
        "observations_written",
        "observations_inserted",
        "observations_reused",
        "documents_seen",
        "quality_findings",
        "peak_observed_rss_bytes",
    )
    return {key: summary[key] for key in keys if key in summary}


async def _run_topic_and_vector(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if os.getenv("DEXT_TEST_SKIP_TOPICS") != "1":
        from dext_graph.catalog.topic_workflow import run_topic_stage

        result = await run_topic_stage(
            writer, build_id, settings, progress=progress
        )
        if result["build"]["status"] != "WRITING_VECTOR":
            return result
    from dext_graph.catalog.vector_workflow import run_vector_stage

    return await run_vector_stage(writer, build_id, settings, progress=progress)


async def create_build(
    university_names: list[str] | tuple[str, ...],
    settings: GraphSettings | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    _reset_peak_rss()
    sources = resolve_build_sources(university_names, settings)
    build_id = uuid7()
    emit_progress(
        progress,
        "build",
        "started",
        build_id=build_id,
        message="create build",
        total=len(sources),
        counters={"sources": len(sources)},
    )
    with catalog_write_lock(settings.catalog_path):
        _preflight_build_storage(sources, settings)
        emit_progress(
            progress,
            "catalog_backup",
            "started",
            build_id=build_id,
            message="catalog backup",
        )
        backup_existing_catalog(
            settings.catalog_path,
            retention=settings.catalog_backup_retention,
            progress_hook=_backup_progress(
                settings,
                progress,
                build_id=build_id,
                stage="catalog_backup",
                message="catalog backup",
            ),
        )
        emit_progress(
            progress,
            "catalog_backup",
            "completed",
            build_id=build_id,
            message="catalog backup",
        )
        path = initialize_catalog(settings.catalog_path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            await writer.execute(
                lambda connection: _insert_build(connection, build_id, sources, settings)
            )
            result = await _run_build(writer, build_id, settings, progress=progress)
            if os.getenv("DEXT_TEST_STOP_AFTER_INGEST") == "1":
                return result
            if result["build"]["status"] == "CURATING":
                from dext_graph.catalog.curation import run_curation

                result = await run_curation(
                    writer, build_id, settings, progress=progress
                )
            if result["build"]["status"] == "EMBEDDING":
                from dext_graph.catalog.graph_workflow import run_graph_stage

                result = await run_graph_stage(
                    writer, build_id, settings, progress=progress
                )
            if (
                result["build"]["status"] == "WRITING_VECTOR"
                and os.getenv("DEXT_TEST_SKIP_VECTOR") != "1"
            ):
                return await _run_topic_and_vector(
                    writer, build_id, settings, progress=progress
                )
            return result


def _assert_resume_compatible(build: dict[str, Any], settings: GraphSettings) -> None:
    current = _settings_snapshot(settings)
    frozen = build["settings_json"]
    differences = [
        key
        for key in _RESUME_SETTING_KEYS
        if key in frozen and frozen.get(key) != current.get(key)
    ]
    if differences:
        raise CatalogError(
            "resume settings are incompatible with the frozen build: "
            + ", ".join(differences)
        )
    if frozen.get("catalog_schema_version") not in {
        1,
        2,
        3,
        4,
        5,
        CATALOG_SCHEMA_VERSION,
    }:
        raise CatalogError("build catalog schema version is incompatible")


async def resume_build(
    build_id: str,
    settings: GraphSettings | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    _reset_peak_rss()
    path = Path(settings.catalog_path).expanduser().resolve()
    with closing(connect_catalog_read_only(path)) as connection:
        build = _load_build(connection, build_id)
        catalog_user_version = int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        )
        export_pruned = False
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_runs'"
        ).fetchone() is not None:
            row = connection.execute(
                "SELECT summary_json FROM graph_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            export_pruned = bool(
                row is not None
                and json_loads(row["summary_json"], {}).get("export_pruned")
            )
    needs_catalog_backup = catalog_user_version != CATALOG_SCHEMA_VERSION
    _assert_resume_compatible(build, settings)
    if build["status"] in {"VALIDATING", "FAILED_VALIDATION", "READY", "ACTIVE"}:
        return build_status(path, build_id)
    if (
        build["status"] == "WRITING_VECTOR"
        and os.getenv("DEXT_TEST_SKIP_VECTOR") == "1"
        and not export_pruned
    ):
        return build_status(path, build_id)
    with catalog_write_lock(path):
        if needs_catalog_backup:
            emit_progress(
                progress,
                "catalog_backup",
                "started",
                build_id=build_id,
                message="catalog backup",
            )
            backup_existing_catalog(
                path,
                retention=settings.catalog_backup_retention,
                progress_hook=_backup_progress(
                    settings,
                    progress,
                    build_id=build_id,
                    stage="catalog_backup",
                    message="catalog backup",
                ),
            )
            emit_progress(
                progress,
                "catalog_backup",
                "completed",
                build_id=build_id,
                message="catalog backup",
            )
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            has_curation_run = await writer.execute(
                lambda connection: connection.execute(
                    "SELECT 1 FROM curation_runs WHERE build_id=?", (build_id,)
                ).fetchone()
                is not None,
                transactional=False,
            )
            has_graph_run = await writer.execute(
                lambda connection: connection.execute(
                    "SELECT 1 FROM graph_runs WHERE build_id=?", (build_id,)
                ).fetchone()
                is not None,
                transactional=False,
            )
            has_topic_run = await writer.execute(
                lambda connection: connection.execute(
                    "SELECT 1 FROM topic_runs WHERE build_id=?", (build_id,)
                ).fetchone()
                is not None,
                transactional=False,
            )
            has_vector_run = await writer.execute(
                lambda connection: connection.execute(
                    "SELECT 1 FROM vector_runs WHERE build_id=?", (build_id,)
                ).fetchone()
                is not None,
                transactional=False,
            )
            if build["status"] == "WRITING_VECTOR" or (
                build["status"] == "FAILED" and (has_topic_run or has_vector_run)
            ):
                if export_pruned:
                    from dext_graph.catalog.evidence import ensure_graph_exports_available

                    await ensure_graph_exports_available(
                        writer, build_id, settings, progress=progress
                    )
                if os.getenv("DEXT_TEST_SKIP_VECTOR") == "1":
                    return build_status(path, build_id)
                return await _run_topic_and_vector(
                    writer, build_id, settings, progress=progress
                )
            if build["status"] in {"EMBEDDING", "WRITING_GRAPH"} or (
                build["status"] == "FAILED" and has_graph_run
            ):
                from dext_graph.catalog.graph_workflow import run_graph_stage

                result = await run_graph_stage(
                    writer, build_id, settings, progress=progress
                )
                if (
                    result["build"]["status"] == "WRITING_VECTOR"
                    and os.getenv("DEXT_TEST_SKIP_VECTOR") != "1"
                ):
                    return await _run_topic_and_vector(
                        writer, build_id, settings, progress=progress
                    )
                return result
            if build["status"] == "CURATING" or (
                build["status"] == "FAILED" and has_curation_run and not has_graph_run
            ):
                from dext_graph.catalog.curation import run_curation

                result = await run_curation(
                    writer, build_id, settings, progress=progress
                )
                if result["build"]["status"] == "EMBEDDING":
                    from dext_graph.catalog.graph_workflow import run_graph_stage

                    result = await run_graph_stage(
                        writer, build_id, settings, progress=progress
                    )
                    if (
                        result["build"]["status"] == "WRITING_VECTOR"
                        and os.getenv("DEXT_TEST_SKIP_VECTOR") != "1"
                    ):
                        return await _run_topic_and_vector(
                            writer, build_id, settings, progress=progress
                        )
                    return result
                return result
            result = await _run_build(writer, build_id, settings, progress=progress)
            if result["build"]["status"] == "CURATING":
                from dext_graph.catalog.curation import run_curation

                result = await run_curation(
                    writer, build_id, settings, progress=progress
                )
            if result["build"]["status"] == "EMBEDDING":
                from dext_graph.catalog.graph_workflow import run_graph_stage

                result = await run_graph_stage(
                    writer, build_id, settings, progress=progress
                )
                if (
                    result["build"]["status"] == "WRITING_VECTOR"
                    and os.getenv("DEXT_TEST_SKIP_VECTOR") != "1"
                ):
                    return await _run_topic_and_vector(
                        writer, build_id, settings, progress=progress
                    )
                return result
            return result


def _serializable_row(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for key in (
        "settings_json",
        "summary_json",
        "row_counts_json",
        "details_json",
        "manifest_json",
    ):
        if key in value:
            value[key] = json_loads(value[key], {})
    for key in (
        "deactivation_eligible",
        "resolved",
        "active",
        "neo4j_done",
        "qdrant_done",
        "readback_done",
    ):
        if key in value:
            value[key] = bool(value[key])
    return value


def build_status(catalog_path: str | Path, build_id: str | None = None) -> dict[str, Any]:
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {1, 2, 3, 4, 5, CATALOG_SCHEMA_VERSION}:
            raise CatalogError(
                f"catalog schema version {version} is incompatible with "
                f"{CATALOG_SCHEMA_VERSION}"
            )
        if build_id is None:
            builds = [
                _serializable_row(row)
                for row in connection.execute(
                    "SELECT * FROM graph_builds ORDER BY started_at DESC LIMIT 20"
                )
            ]
            return {"catalog_path": str(Path(catalog_path).resolve()), "builds": builds}
        build = connection.execute(
            "SELECT * FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()
        if build is None:
            raise CatalogError(f"unknown build ID: {build_id}")
        tasks = [
            _serializable_row(row)
            for row in connection.execute(
                "SELECT * FROM build_source_tasks WHERE build_id=? ORDER BY ordinal",
                (build_id,),
            )
        ]
        checkpoints = [
            _serializable_row(row)
            for row in connection.execute(
                "SELECT * FROM sink_checkpoints WHERE build_id=? ORDER BY sink, partition_key",
                (build_id,),
            )
        ]
        finding_counts = {
            row["severity"]: int(row["count"])
            for row in connection.execute(
                "SELECT severity, COUNT(*) AS count FROM quality_findings "
                "WHERE build_id=? AND resolved=0 GROUP BY severity",
                (build_id,),
            )
        }
        curation_run = None
        has_curation_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='curation_runs'"
        ).fetchone()
        if has_curation_table is not None:
            row = connection.execute(
                "SELECT * FROM curation_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if row is not None:
                curation_run = _serializable_row(row)
        graph_run = None
        has_graph_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_runs'"
        ).fetchone()
        if has_graph_table is not None:
            row = connection.execute(
                "SELECT * FROM graph_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if row is not None:
                graph_run = _serializable_row(row)
        vector_run = None
        has_vector_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vector_runs'"
        ).fetchone()
        if has_vector_table is not None:
            row = connection.execute(
                "SELECT * FROM vector_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if row is not None:
                vector_run = _serializable_row(row)
        topic_run = None
        has_topic_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_runs'"
        ).fetchone()
        if has_topic_table is not None:
            row = connection.execute(
                "SELECT * FROM topic_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if row is not None:
                topic_run = _serializable_row(row)
        validation_run = None
        promotion_run = None
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='validation_runs'"
        ).fetchone() is not None:
            row = connection.execute(
                "SELECT * FROM validation_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if row is not None:
                validation_run = _serializable_row(row)
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='promotion_runs'"
        ).fetchone() is not None:
            row = connection.execute(
                "SELECT * FROM promotion_runs WHERE build_id=?", (build_id,)
            ).fetchone()
            if row is not None:
                promotion_run = _serializable_row(row)
        return {
            "catalog_path": str(Path(catalog_path).resolve()),
            "build": _serializable_row(build),
            "sources": tasks,
            "checkpoints": checkpoints,
            "unresolved_findings": finding_counts,
            "curation": curation_run,
            "graph": graph_run,
            "topics": topic_run,
            "vector": vector_run,
            "validation": validation_run,
            "promotion": promotion_run,
        }


def get_status(
    build_id: str | None = None, settings: GraphSettings | None = None
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    return build_status(settings.catalog_path, build_id)


__all__ = [
    "CURATION_VERSION",
    "build_status",
    "create_build",
    "get_status",
    "resolve_build_sources",
    "resume_build",
]
