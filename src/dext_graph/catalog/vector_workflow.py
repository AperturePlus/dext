"""Stage-4 professor semantic projection workflow."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

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
from dext_graph.catalog.semantic import (
    build_semantic_profile,
    load_embedding_cache,
    mark_embedding_job,
    sparse_bm25_vector,
    store_embedding_cache,
)
from dext_graph.catalog.vector_sink import (
    ProfessorQdrant,
    ProfessorVectorPoint,
    professor_collection_name,
)
from dext_graph.config import GraphSettings
from dext_graph.embeddings import EmbeddingClient
from dext_graph.models import RequestMetric
from dext_graph.profiles import TransformersTokenizer, prefixed_input

VECTOR_RUN_VERSION = "semantic-vector-v1"
PROFILE_TEMPLATE_VERSION = "baseline-v1"


def _safe_error(exc: BaseException) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (value or type(exc).__name__)[:2000]


def _embedding_fingerprint(settings: GraphSettings, tokenizer_identity: str) -> str:
    import hashlib

    material = json_dumps(
        {
            "version": VECTOR_RUN_VERSION,
            "provider": "siliconflow",
            "base_url": settings.embedding_base_url,
            "model": settings.embedding_model,
            "dimension": settings.embedding_dimension,
            "passage_prefix": settings.embedding_passage_prefix,
            "tokenizer": tokenizer_identity,
            "sparse_tokenizer_version": settings.bm25_tokenizer_version,
        }
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _prepare_run(
    connection: sqlite3.Connection,
    build_id: str,
    settings: GraphSettings,
    *,
    tokenizer_identity: str,
) -> str:
    build = connection.execute(
        "SELECT status FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    if build["status"] not in {"WRITING_VECTOR", "FAILED"}:
        raise CatalogError(
            f"build {build_id} cannot enter vector stage from {build['status']}"
        )
    run_id = f"{build_id}:{VECTOR_RUN_VERSION}"
    collection = professor_collection_name(build_id)
    fingerprint = _embedding_fingerprint(settings, tokenizer_identity)
    existing = connection.execute(
        "SELECT * FROM vector_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO vector_runs(
              id, build_id, status, profile_template_version,
              tokenizer_identity, sparse_tokenizer_version, embedding_fingerprint,
              collection_name, summary_json, started_at
            ) VALUES (?, ?, 'RUNNING', ?, ?, ?, ?, ?, '{}', ?)
            """,
            (
                run_id,
                build_id,
                PROFILE_TEMPLATE_VERSION,
                tokenizer_identity,
                settings.bm25_tokenizer_version,
                fingerprint,
                collection,
                utcnow_iso(),
            ),
        )
    else:
        run_id = str(existing["id"])
        if existing["profile_template_version"] != PROFILE_TEMPLATE_VERSION:
            raise CatalogError("profile template version changed; create a new build")
        if existing["tokenizer_identity"] != tokenizer_identity:
            raise CatalogError("tokenizer identity changed; create a new build")
        if existing["sparse_tokenizer_version"] != settings.bm25_tokenizer_version:
            raise CatalogError("sparse tokenizer version changed; create a new build")
        if existing["embedding_fingerprint"] != fingerprint:
            raise CatalogError("embedding fingerprint changed; create a new build")
        if existing["status"] != "COMPLETED":
            connection.execute(
                "UPDATE vector_runs SET status='RUNNING', finished_at=NULL, last_error=NULL WHERE id=?",
                (run_id,),
            )
    connection.execute(
        "UPDATE graph_builds SET status='WRITING_VECTOR', embedding_fingerprint=?, last_error=NULL WHERE id=?",
        (fingerprint, build_id),
    )
    return run_id


def _source_context(connection: sqlite3.Connection, build_id: str) -> dict[str, dict[str, Any]]:
    tasks = {
        str(row["university_id"]): dict(row)
        for row in connection.execute(
            """
            SELECT t.university_id, t.university_name, s.snapshot_path
            FROM build_source_tasks t
            JOIN source_snapshots s ON s.id=t.source_snapshot_id
            WHERE t.build_id=?
            """,
            (build_id,),
        )
    }
    result: dict[str, dict[str, Any]] = {}
    for university_id, task in tasks.items():
        city = None
        path = Path(task["snapshot_path"]).resolve()
        source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        try:
            columns = {
                str(row[1]) for row in source.execute("PRAGMA table_info(university_meta)")
            }
            if "location" in columns:
                meta = source.execute("SELECT location FROM university_meta LIMIT 1").fetchone()
                city = meta["location"] if meta is not None else None
        finally:
            source.close()
        result[university_id] = {
            "university": task["university_name"],
            "city": city,
        }
    return result


def _last_uploaded_key(connection: sqlite3.Connection, build_id: str) -> str:
    row = connection.execute(
        "SELECT last_key FROM sink_checkpoints "
        "WHERE build_id=? AND sink='qdrant' AND partition_key='professors'",
        (build_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _checkpoint(
    connection: sqlite3.Connection, build_id: str, last_key: str, rows: int
) -> None:
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id, sink, partition_key, last_key, last_batch_id, rows_written, updated_at
        ) VALUES (?, 'qdrant', 'professors', ?, NULL, ?, ?)
        ON CONFLICT(build_id, sink, partition_key) DO UPDATE SET
          last_key=excluded.last_key,
          rows_written=sink_checkpoints.rows_written + excluded.rows_written,
          updated_at=excluded.updated_at
        """,
        (build_id, last_key, rows, utcnow_iso()),
    )


def _eligible_rows(
    connection: sqlite3.Connection, build_id: str, *, after_entity_id: str, limit: int
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT cp.*, o.university_id, o.payload_json
            FROM canonical_professors cp
            JOIN entity_observations eo ON eo.entity_id=cp.entity_id AND eo.build_id=cp.build_id
            JOIN professor_observations o ON o.id=eo.observation_id AND o.active=1
            WHERE cp.build_id=? AND cp.active=1 AND cp.role_status!='excluded'
              AND cp.entity_id>?
            GROUP BY cp.entity_id
            ORDER BY cp.entity_id
            LIMIT ?
            """,
            (build_id, after_entity_id, limit),
        )
    ]


def _entity_evidence(
    connection: sqlite3.Connection, build_id: str, entity_id: str
) -> tuple[list[str], list[str]]:
    statements = [
        str(row[0])
        for row in connection.execute(
            "SELECT normalized_text FROM research_statements "
            "WHERE build_id=? AND entity_id=? ORDER BY id",
            (build_id, entity_id),
        )
    ]
    mentions = [
        str(row[0])
        for row in connection.execute(
            "SELECT normalized_text FROM publication_mentions "
            "WHERE build_id=? AND entity_id=? ORDER BY id, observation_id",
            (build_id, entity_id),
        )
    ]
    return statements, mentions


def _org_units(payload: dict[str, Any]) -> list[str]:
    affiliations = payload.get("affiliations") or []
    values = [
        str(item.get("org_unit_name"))
        for item in affiliations
        if isinstance(item, dict) and item.get("org_unit_name")
    ]
    if values:
        return list(dict.fromkeys(values))
    value = payload.get("org_unit_name")
    return [str(value)] if value else []


async def _build_points(
    writer: CatalogWriter,
    *,
    build_id: str,
    rows: list[dict[str, Any]],
    contexts: dict[str, dict[str, Any]],
    tokenizer: Any,
    settings: GraphSettings,
    embedding_client: Any,
    fingerprint: str,
) -> list[ProfessorVectorPoint]:
    profiles: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
    for row in rows:
        payload = json_loads(row.get("payload_json"), {})
        university_id = str(row["university_id"])
        context = contexts.get(university_id, {"university": university_id, "city": None})
        statements, mentions = await writer.execute(
            lambda connection, entity=str(row["entity_id"]): _entity_evidence(
                connection, build_id, entity
            ),
            transactional=False,
        )
        role_reason_codes = json_loads(row["role_reason_codes"], [])
        profile = build_semantic_profile(
            {
                "entity_id": str(row["entity_id"]),
                "university": context["university"],
                "org_units": _org_units(payload),
                "title": row["title_raw"],
            },
            research_statements=statements,
            publication_mentions=mentions,
            approved_topics=[],
            bio=row["bio"],
            template_name=PROFILE_TEMPLATE_VERSION,
            tokenizer=tokenizer,
            max_tokens=settings.profile_max_tokens,
        )
        point_payload = {
            "build_id": build_id,
            "entity_id": str(row["entity_id"]),
            "university_id": university_id,
            "org_unit_ids": [],
            "role_status": row["role_status"],
            "role_reason_codes": role_reason_codes,
            "master_eligibility": row["master_eligibility"],
            "phd_eligibility": row["phd_eligibility"],
            "title_family": row["title_family"],
            "city": context.get("city"),
            "topic_ids": [],
            "method_topic_ids": [],
            "application_domain_topic_ids": [],
            "task_topic_ids": [],
            "profile_hash": profile.profile_hash,
            "embedding_provider": "siliconflow",
            "embedding_model": settings.embedding_model,
            "embedding_fingerprint": fingerprint,
            "provenance_ref": f"catalog:entity:{row['entity_id']}",
        }
        await writer.execute(
            lambda connection, p=profile, payload_json=json_dumps(point_payload): connection.execute(
                """
                INSERT INTO professor_profiles(
                  build_id, entity_id, profile_hash, template_version,
                  tokenizer_identity, normalized_profile, token_count,
                  payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(build_id, entity_id) DO UPDATE SET
                  profile_hash=excluded.profile_hash,
                  template_version=excluded.template_version,
                  tokenizer_identity=excluded.tokenizer_identity,
                  normalized_profile=excluded.normalized_profile,
                  token_count=excluded.token_count,
                  payload_json=excluded.payload_json
                """,
                (
                    build_id,
                    p.entity_id,
                    p.profile_hash,
                    p.template_version,
                    p.tokenizer_identity,
                    p.normalized_profile,
                    p.token_count,
                    payload_json,
                    utcnow_iso(),
                ),
            )
        )
        profiles.append((row, profile, point_payload))

    missing: list[tuple[dict[str, Any], Any, dict[str, Any]]] = []
    cached_by_hash: dict[str, Any] = {}
    for row, profile, payload in profiles:
        cached = await writer.execute(
            lambda connection, h=profile.profile_hash: load_embedding_cache(
                connection,
                profile_hash=h,
                embedding_fingerprint=fingerprint,
                dimension=settings.embedding_dimension,
            ),
            transactional=False,
        )
        if cached is None:
            missing.append((row, profile, payload))
            await writer.execute(
                lambda connection, r=row, p=profile: mark_embedding_job(
                    connection,
                    build_id=build_id,
                    entity_id=str(r["entity_id"]),
                    profile_hash=p.profile_hash,
                    status="pending",
                )
            )
        else:
            cached_by_hash[profile.profile_hash] = cached

    if missing:
        inputs = [
            prefixed_input(
                profile.normalized_profile,
                settings.embedding_passage_prefix,
                tokenizer,
                max_tokens=settings.embedding_max_input_tokens,
            )
            for _, profile, _ in missing
        ]
        result = await embedding_client.embed(inputs, purpose="professor_profiles")
        for (row, profile, _payload), dense in zip(missing, result.vectors, strict=True):
            sparse = sparse_bm25_vector(
                profile.normalized_profile,
                tokenizer_version=settings.bm25_tokenizer_version,
            )
            checksum = await writer.execute(
                lambda connection, p=profile, d=dense, s=sparse: store_embedding_cache(
                    connection,
                    profile_hash=p.profile_hash,
                    embedding_fingerprint=fingerprint,
                    dense_vector=d,
                    sparse_vector=s,
                )
            )
            await writer.execute(
                lambda connection, r=row, p=profile, c=checksum: mark_embedding_job(
                    connection,
                    build_id=build_id,
                    entity_id=str(r["entity_id"]),
                    profile_hash=p.profile_hash,
                    status="succeeded",
                    vector_checksum=c,
                )
            )
            cached_by_hash[profile.profile_hash] = await writer.execute(
                lambda connection, p=profile: load_embedding_cache(
                    connection,
                    profile_hash=p.profile_hash,
                    embedding_fingerprint=fingerprint,
                    dimension=settings.embedding_dimension,
                ),
                transactional=False,
            )

    points: list[ProfessorVectorPoint] = []
    for row, profile, payload in profiles:
        cached = cached_by_hash[profile.profile_hash]
        points.append(
            ProfessorVectorPoint(
                entity_id=str(row["entity_id"]),
                dense=list(cached.dense),
                sparse=dict(cached.sparse),
                payload=payload,
            )
        )
    return points


def _finish(
    connection: sqlite3.Connection,
    build_id: str,
    run_id: str,
    *,
    expected_count: int,
    collection_count: int,
) -> None:
    if expected_count != collection_count:
        raise CatalogError(
            f"Qdrant count mismatch: expected {expected_count}, found {collection_count}"
        )
    summary = {
        "vector_run_version": VECTOR_RUN_VERSION,
        "collection_name": professor_collection_name(build_id),
        "eligible_professors": expected_count,
        "qdrant_count": collection_count,
    }
    now = utcnow_iso()
    connection.execute(
        "UPDATE vector_runs SET status='COMPLETED', summary_json=?, finished_at=?, last_error=NULL WHERE id=?",
        (json_dumps(summary), now, run_id),
    )
    build_summary = json_loads(
        connection.execute(
            "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()[0],
        {},
    )
    build_summary["vector"] = summary
    connection.execute(
        "UPDATE graph_builds SET status='VALIDATING', summary_json=?, last_error=NULL WHERE id=?",
        (json_dumps(build_summary), build_id),
    )


def _fail(connection: sqlite3.Connection, build_id: str, run_id: str, error: str) -> None:
    now = utcnow_iso()
    connection.execute(
        "UPDATE vector_runs SET status='FAILED', last_error=?, finished_at=? WHERE id=?",
        (error, now, run_id),
    )
    connection.execute(
        "UPDATE graph_builds SET status='FAILED', last_error=? WHERE id=?",
        (error, build_id),
    )


async def run_vector_stage(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    embedding_client: Any | None = None,
    qdrant_sink: Any | None = None,
    tokenizer: Any | None = None,
) -> dict[str, Any]:
    tokenizer = tokenizer or TransformersTokenizer(
        settings.tokenizer_model, settings.tokenizer_revision
    )
    run_id = await writer.execute(
        lambda connection: _prepare_run(
            connection, build_id, settings, tokenizer_identity=tokenizer.identity
        )
    )
    run = await writer.execute(
        lambda connection: dict(
            connection.execute("SELECT * FROM vector_runs WHERE id=?", (run_id,)).fetchone()
        ),
        transactional=False,
    )
    if run["status"] == "COMPLETED":
        from dext_graph.catalog.workflow import build_status

        return build_status(writer.path, build_id)

    owns_embedding = embedding_client is None
    owns_qdrant = qdrant_sink is None
    metrics: list[dict[str, Any]] = []

    def metric_sink(metric: RequestMetric) -> None:
        metrics.append(metric.asdict())

    if embedding_client is None:
        embedding_client = EmbeddingClient(settings, metric_sink)
    if qdrant_sink is None:
        qdrant_sink = ProfessorQdrant(settings.qdrant_url)

    try:
        collection_name = professor_collection_name(build_id)
        await qdrant_sink.create_collection(
            collection_name, dimension=settings.embedding_dimension
        )
        contexts = await writer.execute(
            lambda connection: _source_context(connection, build_id),
            transactional=False,
        )
        fingerprint = _embedding_fingerprint(settings, tokenizer.identity)
        uploaded = 0
        batches = 0
        while True:
            last = await writer.execute(
                lambda connection: _last_uploaded_key(connection, build_id),
                transactional=False,
            )
            rows = await writer.execute(
                lambda connection, after=last: _eligible_rows(
                    connection,
                    build_id,
                    after_entity_id=after,
                    limit=settings.qdrant_upsert_batch,
                ),
                transactional=False,
            )
            if not rows:
                break
            points = await _build_points(
                writer,
                build_id=build_id,
                rows=rows,
                contexts=contexts,
                tokenizer=tokenizer,
                settings=settings,
                embedding_client=embedding_client,
                fingerprint=fingerprint,
            )
            await qdrant_sink.upsert(collection_name, points)
            await writer.execute(
                lambda connection, key=str(rows[-1]["entity_id"]), count=len(rows): _checkpoint(
                    connection, build_id, key, count
                )
            )
            uploaded += len(rows)
            batches += 1
            limit = os.getenv("DEXT_TEST_KILL_AFTER_QDRANT_BATCHES")
            if limit and batches >= int(limit):
                os._exit(97)
        expected = await writer.execute(
            lambda connection: connection.execute(
                """
                SELECT COUNT(*) FROM canonical_professors
                WHERE build_id=? AND active=1 AND role_status!='excluded'
                """,
                (build_id,),
            ).fetchone()[0],
            transactional=False,
        )
        count = await qdrant_sink.count(collection_name)
        await writer.execute(
            lambda connection: _finish(
                connection,
                build_id,
                run_id,
                expected_count=int(expected),
                collection_count=count,
            )
        )
    except Exception as exc:  # noqa: BLE001 - persist resumable vector failure
        await writer.execute(
            lambda connection: _fail(connection, build_id, run_id, _safe_error(exc))
        )
    finally:
        if owns_embedding:
            await embedding_client.close()
        if owns_qdrant:
            await qdrant_sink.close()

    from dext_graph.catalog.workflow import build_status

    return build_status(writer.path, build_id)


async def vector_build(build_id: str, settings: GraphSettings | None = None) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            return await run_vector_stage(writer, build_id, settings)


__all__ = ["run_vector_stage", "vector_build"]
