"""Recoverable stage-5 Topic taxonomy and Statement linking workflow."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from contextlib import suppress
from pathlib import Path
from typing import Any

from dext_graph.catalog.db import (
    CatalogError,
    CatalogWriter,
    backup_existing_catalog,
    catalog_write_lock,
    initialize_catalog,
    json_dumps,
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.evidence import freeze_graph_exports
from dext_graph.catalog.neo4j_sink import write_neo4j_exports
from dext_graph.catalog.normalization import normalize_text
from dext_graph.catalog.progress import ProgressCallback, emit_progress
from dext_graph.catalog.topic_llm import TopicLLMClient
from dext_graph.catalog.topic_sink import TopicQdrant, TopicVectorPoint, topic_collection_name
from dext_graph.catalog.topics import (
    TopicConcept,
    TaxonomyManifest,
    apply_statement_concepts,
    import_taxonomy,
    load_taxonomy,
    materialize_taxonomy_relations,
    record_topic_finding,
    topic_alias_key,
)
from dext_graph.catalog.vector_workflow import _embedding_fingerprint
from dext_graph.config import GraphSettings
from dext_graph.embeddings import EmbeddingClient
from dext_graph.models import ValueValidationError
from dext_graph.profiles import TransformersTokenizer, prefixed_input

TOPIC_RUN_VERSION = "topic-dag-v1"
_TERMINAL_TOPIC_LINK_STATUSES = frozenset({"succeeded", "terminal-invalid-input"})


class TopicLinkDeferredRetryError(RuntimeError):
    """Raised after draining work when some statements were deferred to retry."""


class _TopicLLMOutputError(RuntimeError):
    """A row-scoped malformed LLM response that should not stop peer workers."""


def _safe_error(exc: BaseException) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (value or type(exc).__name__)[:2000]


def _has_pending_topic_link_work(connection: sqlite3.Connection, build_id: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM research_statements AS statement
            WHERE statement.build_id=?
              AND NOT EXISTS (
                SELECT 1
                FROM topic_link_jobs AS job
                WHERE job.build_id=statement.build_id
                  AND job.statement_id=statement.id
                  AND job.status IN ('succeeded','terminal-invalid-input')
              )
            LIMIT 1
            """,
            (build_id,),
        ).fetchone()
        is not None
    )


async def _preflight_topic_llm(llm_client: Any) -> None:
    preflight = getattr(llm_client, "preflight", None)
    if preflight is not None:
        await preflight()


def _prepare_run(
    connection: sqlite3.Connection,
    build_id: str,
    settings: GraphSettings,
    *,
    tokenizer_identity: str,
) -> tuple[str, TaxonomyManifest, str]:
    build = connection.execute(
        "SELECT status,taxonomy_version FROM graph_builds WHERE id=?", (build_id,)
    ).fetchone()
    if build is None:
        raise CatalogError(f"unknown build ID: {build_id}")
    if str(build["status"]) not in {"WRITING_VECTOR", "VALIDATING", "FAILED"}:
        raise CatalogError(
            f"build {build_id} cannot enter Topic stage from {build['status']}"
        )
    manifest = load_taxonomy(settings.taxonomy_path)
    import_taxonomy(connection, manifest)
    if build["taxonomy_version"] not in {None, manifest.version}:
        raise CatalogError("build taxonomy version differs from configured taxonomy")
    fingerprint = _embedding_fingerprint(settings, tokenizer_identity)
    run_id = f"{build_id}:{TOPIC_RUN_VERSION}"
    existing = connection.execute(
        "SELECT * FROM topic_runs WHERE build_id=?", (build_id,)
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO topic_runs(
              id,build_id,taxonomy_version,manifest_hash,status,summary_json,started_at
            ) VALUES (?,?,?,?,'RUNNING','{}',?)
            """,
            (run_id, build_id, manifest.version, manifest.manifest_hash, utcnow_iso()),
        )
    else:
        run_id = str(existing["id"])
        if str(existing["taxonomy_version"]) != manifest.version:
            raise CatalogError("Topic run taxonomy version changed; create a new build")
        if str(existing["manifest_hash"]) != manifest.manifest_hash:
            raise CatalogError("Topic taxonomy manifest changed; create a new version")
        if str(existing["status"]) != "COMPLETED":
            connection.execute(
                "UPDATE topic_runs SET status='RUNNING',finished_at=NULL,last_error=NULL WHERE id=?",
                (run_id,),
            )
    connection.execute(
        """
        UPDATE graph_builds SET status='WRITING_VECTOR',taxonomy_version=?,
          embedding_fingerprint=?,last_error=NULL WHERE id=?
        """,
        (manifest.version, fingerprint, build_id),
    )
    return run_id, manifest, fingerprint


def _active_topics(connection: sqlite3.Connection, taxonomy_version: str) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT id,canonical_name,kind FROM topics
        WHERE taxonomy_version=? AND status='active' ORDER BY id
        """,
        (taxonomy_version,),
    ):
        aliases = [
            str(item[0])
            for item in connection.execute(
                "SELECT alias_text FROM topic_aliases WHERE taxonomy_version=? AND topic_id=? "
                "ORDER BY alias_key",
                (taxonomy_version, row["id"]),
            )
        ]
        topics.append({**dict(row), "aliases": aliases})
    return topics


async def _build_candidate_collection(
    writer: CatalogWriter,
    *,
    build_id: str,
    taxonomy_version: str,
    fingerprint: str,
    settings: GraphSettings,
    tokenizer: Any,
    embedding_client: Any,
    topic_sink: Any,
    progress: ProgressCallback | None = None,
) -> tuple[str, int]:
    name = topic_collection_name(taxonomy_version, fingerprint)
    await topic_sink.create_collection(name, dimension=settings.embedding_dimension)
    topics = await writer.execute(
        lambda connection: _active_topics(connection, taxonomy_version),
        transactional=False,
    )
    existing = await writer.execute(
        lambda connection: connection.execute(
            """
            SELECT status,point_count,collection_name FROM topic_candidate_collections
            WHERE taxonomy_version=? AND embedding_fingerprint=?
            """,
            (taxonomy_version, fingerprint),
        ).fetchone(),
        transactional=False,
    )
    if existing is not None and str(existing["status"]) == "READY":
        count = await topic_sink.count(name)
        if count == len(topics) == int(existing["point_count"]):
            emit_progress(
                progress,
                "topics",
                "completed",
                build_id=build_id,
                message="candidate collection already ready",
                current=count,
                total=len(topics),
                counters={"collection": name, "topics": count},
            )
            return name, count
    now = utcnow_iso()
    await writer.execute(
        lambda connection: connection.execute(
            """
            INSERT INTO topic_candidate_collections(
              taxonomy_version,embedding_fingerprint,collection_name,
              status,point_count,created_at,updated_at
            ) VALUES (?,?,?,'BUILDING',0,?,?)
            ON CONFLICT(taxonomy_version,embedding_fingerprint) DO UPDATE SET
              collection_name=excluded.collection_name,status='BUILDING',
              point_count=0,updated_at=excluded.updated_at
            """,
            (taxonomy_version, fingerprint, name, now, now),
        )
    )
    for offset in range(0, len(topics), settings.embedding_request_batch):
        batch = topics[offset : offset + settings.embedding_request_batch]
        inputs = [
            prefixed_input(
                "\n".join(topic["aliases"]),
                settings.embedding_passage_prefix,
                tokenizer,
                max_tokens=settings.embedding_max_input_tokens,
            )
            for topic in batch
        ]
        embedded = await embedding_client.embed(inputs, purpose="topic_candidates")
        points = [
            TopicVectorPoint(
                topic_id=str(topic["id"]),
                dense=list(vector),
                payload={
                    "taxonomy_version": taxonomy_version,
                    "canonical_name": topic["canonical_name"],
                    "kind": topic["kind"],
                    "status": "active",
                    "alias_keys": [topic_alias_key(alias) for alias in topic["aliases"]],
                    "embedding_fingerprint": fingerprint,
                },
            )
            for topic, vector in zip(batch, embedded.vectors, strict=True)
        ]
        await topic_sink.upsert(name, points)
        emit_progress(
            progress,
            "topics",
            "progress",
            build_id=build_id,
            message="candidate collection",
            current=min(offset + len(batch), len(topics)),
            total=len(topics),
            counters={
                "collection": name,
                "batch_rows": len(batch),
                "offset": offset,
            },
        )
    count = await topic_sink.count(name)
    if count != len(topics):
        raise CatalogError(f"Topic Qdrant count mismatch: expected {len(topics)}, found {count}")
    now = utcnow_iso()
    await writer.execute(
        lambda connection: connection.execute(
            """
            INSERT INTO topic_candidate_collections(
              taxonomy_version,embedding_fingerprint,collection_name,
              status,point_count,created_at,updated_at
            ) VALUES (?,?,?,'READY',?,?,?)
            ON CONFLICT(taxonomy_version,embedding_fingerprint) DO UPDATE SET
              collection_name=excluded.collection_name,status='READY',
              point_count=excluded.point_count,updated_at=excluded.updated_at
            """,
            (taxonomy_version, fingerprint, name, count, now, now),
        )
    )
    return name, count


def _checkpoint_statement(
    connection: sqlite3.Connection,
    build_id: str,
    statement_id: str,
    *,
    rows_delta: int,
) -> None:
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id,sink,partition_key,last_key,last_batch_id,rows_written,updated_at
        ) VALUES (?,'topic_link','statements',?,NULL,?,?)
        ON CONFLICT(build_id,sink,partition_key) DO UPDATE SET
          last_key=CASE
            WHEN sink_checkpoints.last_key IS NULL
              OR sink_checkpoints.last_key < excluded.last_key
            THEN excluded.last_key
            ELSE sink_checkpoints.last_key
          END,
          rows_written=sink_checkpoints.rows_written + excluded.rows_written,
          updated_at=excluded.updated_at
        """,
        (build_id, statement_id, rows_delta, utcnow_iso()),
    )


def _mark_job(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    statement_id: str,
    status: str,
    candidates: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO topic_link_jobs(
          build_id,statement_id,status,candidate_ids_json,attempt_count,last_error,updated_at
        ) VALUES (?,?,?, ?,CASE WHEN ?='running' THEN 1 ELSE 0 END,?,?)
        ON CONFLICT(build_id,statement_id) DO UPDATE SET
          status=excluded.status,
          candidate_ids_json=CASE WHEN excluded.candidate_ids_json='[]'
            THEN topic_link_jobs.candidate_ids_json ELSE excluded.candidate_ids_json END,
          attempt_count=topic_link_jobs.attempt_count +
            CASE WHEN excluded.status='running' THEN 1 ELSE 0 END,
          last_error=excluded.last_error,updated_at=excluded.updated_at
        """,
        (
            build_id,
            statement_id,
            status,
            json_dumps(candidates or []),
            status,
            error,
            utcnow_iso(),
        ),
    )


def _reset_running_jobs(connection: sqlite3.Connection, build_id: str) -> int:
    cursor = connection.execute(
        """
        UPDATE topic_link_jobs
        SET status='retry',last_error=?,updated_at=?
        WHERE build_id=? AND status='running'
        """,
        ("interrupted_run_reset", utcnow_iso(), build_id),
    )
    return int(cursor.rowcount)


def _ensure_topic_link_jobs(connection: sqlite3.Connection, build_id: str) -> int:
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO topic_link_jobs(
          build_id,statement_id,status,candidate_ids_json,attempt_count,last_error,updated_at
        )
        SELECT s.build_id,s.id,'pending','[]',0,NULL,?
        FROM research_statements s
        WHERE s.build_id=?
        """,
        (utcnow_iso(), build_id),
    )
    return int(cursor.rowcount)


def _load_topic_alias_keys(
    connection: sqlite3.Connection, taxonomy_version: str
) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT alias_key FROM topic_aliases WHERE taxonomy_version=?",
            (taxonomy_version,),
        )
    )


def _claim_statement_batch(
    connection: sqlite3.Connection,
    build_id: str,
    *,
    limit: int,
    deferred_statement_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("claim batch limit must be positive")
    deferred = tuple(sorted(deferred_statement_ids or ()))
    exclusion_sql = ""
    exclusion_params: tuple[Any, ...] = ()
    if deferred:
        placeholders = ",".join("?" for _ in deferred)
        exclusion_sql = f" AND j.statement_id NOT IN ({placeholders})"
        exclusion_params = deferred
    statements: list[dict[str, Any]] = []
    for status in ("retry", "pending"):
        remaining = limit - len(statements)
        if remaining <= 0:
            break
        rows = connection.execute(
            f"""
            SELECT j.statement_id AS id,s.raw_text
            FROM topic_link_jobs j
            JOIN research_statements s
              ON s.build_id=j.build_id AND s.id=j.statement_id
            WHERE j.build_id=? AND j.status=?
            {exclusion_sql}
            ORDER BY j.statement_id LIMIT ?
            """,
            (build_id, status, *exclusion_params, remaining),
        ).fetchall()
        statements.extend(
            {"id": str(row[0]), "raw_text": str(row[1])} for row in rows
        )
    for statement in statements:
        _mark_job(
            connection,
            build_id=build_id,
            statement_id=str(statement["id"]),
            status="running",
        )
    return statements


def _job_enters_terminal(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    statement_id: str,
    status: str,
) -> bool:
    if status not in _TERMINAL_TOPIC_LINK_STATUSES:
        return False
    row = connection.execute(
        "SELECT status FROM topic_link_jobs WHERE build_id=? AND statement_id=?",
        (build_id, statement_id),
    ).fetchone()
    return row is None or str(row["status"]) not in _TERMINAL_TOPIC_LINK_STATUSES


def _mark_job_and_checkpoint(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    statement_id: str,
    status: str,
    candidates: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> bool:
    entered_terminal = _job_enters_terminal(
        connection, build_id=build_id, statement_id=statement_id, status=status
    )
    _mark_job(
        connection,
        build_id=build_id,
        statement_id=statement_id,
        status=status,
        candidates=candidates,
        error=error,
    )
    if status in _TERMINAL_TOPIC_LINK_STATUSES:
        _checkpoint_statement(
            connection,
            build_id,
            statement_id,
            rows_delta=1 if entered_terminal else 0,
        )
    return entered_terminal


def _complete_invalid_statement(
    connection: sqlite3.Connection, *, build_id: str, statement_id: str
) -> bool:
    return _mark_job_and_checkpoint(
        connection,
        build_id=build_id,
        statement_id=statement_id,
        status="terminal-invalid-input",
        error="empty_or_punctuation_only_statement",
    )


def _complete_linked_statement(
    connection: sqlite3.Connection,
    *,
    build_id: str,
    statement_id: str,
    raw_text: str,
    concepts: list[TopicConcept],
    rejected_count: int,
    semantic_choices: dict[str, tuple[str, float] | str],
    candidates: list[dict[str, Any]],
) -> bool:
    if rejected_count:
        record_topic_finding(
            connection,
            build_id=build_id,
            code="topic_concept_rejected",
            reference=statement_id,
            details={
                "statement_id": statement_id,
                "rejected_concepts": rejected_count,
                "reason": "invalid_evidence_kind_or_relation",
            },
        )
    apply_statement_concepts(
        connection,
        build_id=build_id,
        statement_id=statement_id,
        raw_text=raw_text,
        concepts=[
            {
                "evidence_span": concept.evidence_span,
                "canonical_name": concept.canonical_name,
                "kind": concept.kind,
                "relation_type": concept.relation_type,
            }
            for concept in concepts
        ],
        semantic_choices=semantic_choices,
    )
    return _mark_job_and_checkpoint(
        connection,
        build_id=build_id,
        statement_id=statement_id,
        status="succeeded",
        candidates=candidates,
    )


async def _extract_statement_concepts(
    llm_client: Any, raw_text: str
) -> tuple[list[TopicConcept], int]:
    extractor = getattr(llm_client, "extract_with_diagnostics", None)
    try:
        if extractor is not None:
            concepts, rejected_count = await extractor(raw_text)
            return list(concepts), int(rejected_count)
        concepts = await llm_client.extract(raw_text)
        return list(concepts), int(getattr(llm_client, "last_rejected_count", 0) or 0)
    except ValueValidationError as exc:
        raise _TopicLLMOutputError(_safe_error(exc)) from exc


async def _semantic_choices(
    concepts: list[TopicConcept],
    *,
    taxonomy_version: str,
    collection_name: str,
    topic_alias_keys: frozenset[str],
    tokenizer: Any,
    settings: GraphSettings,
    embedding_client: Any,
    topic_sink: Any,
    llm_client: Any,
) -> tuple[dict[str, tuple[str, float] | str], list[dict[str, Any]]]:
    unresolved: list[TopicConcept] = []
    for concept in concepts:
        if topic_alias_key(concept.canonical_name) not in topic_alias_keys:
            unresolved.append(concept)
    if not unresolved:
        return {}, []
    inputs = [
        prefixed_input(
            concept.canonical_name,
            settings.embedding_query_prefix,
            tokenizer,
            max_tokens=settings.embedding_max_input_tokens,
        )
        for concept in unresolved
    ]
    result = await embedding_client.embed(inputs, purpose="topic_link_queries")
    choices: dict[str, tuple[str, float] | str] = {}
    diagnostics: list[dict[str, Any]] = []
    for concept, vector in zip(unresolved, result.vectors, strict=True):
        candidates = await topic_sink.query(
            collection_name,
            list(vector),
            taxonomy_version=taxonomy_version,
            kind=concept.kind,
            limit=settings.topic_candidate_top_k,
        )
        diagnostics.append(
            {"evidence_span": concept.evidence_span, "candidates": candidates}
        )
        if not candidates:
            choices[concept.evidence_span] = "new_topic"
            continue
        try:
            choices[concept.evidence_span] = await llm_client.select(concept, candidates)
        except ValueValidationError as exc:
            raise _TopicLLMOutputError(_safe_error(exc)) from exc
    return choices, diagnostics


async def _link_statements(
    writer: CatalogWriter,
    *,
    build_id: str,
    taxonomy_version: str,
    collection_name: str,
    tokenizer: Any,
    settings: GraphSettings,
    embedding_client: Any,
    topic_sink: Any,
    llm_client: Any,
    progress: ProgressCallback | None = None,
) -> int:
    completed = 0
    deferred_statement_ids: set[str] = set()
    await writer.execute(lambda connection: _reset_running_jobs(connection, build_id))
    await writer.execute(lambda connection: _ensure_topic_link_jobs(connection, build_id))
    total = await writer.execute(
        lambda connection: int(
            connection.execute(
                "SELECT COUNT(*) FROM research_statements WHERE build_id=?",
                (build_id,),
            ).fetchone()[0]
        ),
        transactional=False,
    )
    already_terminal = await writer.execute(
        lambda connection: int(
            connection.execute(
                "SELECT COUNT(*) FROM topic_link_jobs WHERE build_id=? "
                "AND status IN ('succeeded','terminal-invalid-input')",
                (build_id,),
            ).fetchone()[0]
        ),
        transactional=False,
    )
    processed = already_terminal
    emit_progress(
        progress,
        "topics",
        "started",
        build_id=build_id,
        message="statement linking",
        current=already_terminal,
        total=total,
    )
    topic_alias_keys = await writer.execute(
        lambda connection: _load_topic_alias_keys(connection, taxonomy_version),
        transactional=False,
    )
    claim_batch_size = max(settings.topic_link_concurrency * 2, 64)
    statement_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
        maxsize=claim_batch_size
    )
    claim_lock = asyncio.Lock()
    claim_exhausted = False

    async def mark_retry(statement_id: str, error: str) -> None:
        await writer.execute(
            lambda connection, sid=statement_id, err=error: _mark_job(
                connection,
                build_id=build_id,
                statement_id=sid,
                status="retry",
                error=err,
            )
        )

    async def next_statement() -> dict[str, Any] | None:
        nonlocal claim_exhausted
        try:
            return statement_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        async with claim_lock:
            try:
                return statement_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            if claim_exhausted:
                return None
            deferred = set(deferred_statement_ids)
            batch = await writer.execute(
                lambda connection, deferred=deferred: _claim_statement_batch(
                    connection,
                    build_id,
                    limit=claim_batch_size,
                    deferred_statement_ids=deferred,
                )
            )
            if not batch:
                claim_exhausted = True
                return None
            for statement in batch[1:]:
                statement_queue.put_nowait(statement)
            return batch[0]

    async def worker(_worker_id: int) -> None:
        nonlocal completed, processed
        current_statement_id: str | None = None
        while True:
            statement = await next_statement()
            if statement is None:
                return
            statement_id = str(statement["id"])
            current_statement_id = statement_id
            raw_text = str(statement["raw_text"])
            try:
                if normalize_text(raw_text) is None:
                    entered_terminal = await writer.execute(
                        lambda connection, sid=statement_id: _complete_invalid_statement(
                            connection, build_id=build_id, statement_id=sid
                        )
                    )
                    current_statement_id = None
                    if entered_terminal:
                        processed += 1
                    emit_progress(
                        progress,
                        "topics",
                        "progress",
                        build_id=build_id,
                        message="statement linking",
                        current=min(processed, total),
                        total=total,
                        counters={
                            "statement_id": statement_id,
                            "status": "terminal-invalid-input",
                        },
                    )
                    continue
                concepts, rejected = await _extract_statement_concepts(
                    llm_client, raw_text
                )
                choices, candidates = await _semantic_choices(
                    concepts,
                    taxonomy_version=taxonomy_version,
                    collection_name=collection_name,
                    topic_alias_keys=topic_alias_keys,
                    tokenizer=tokenizer,
                    settings=settings,
                    embedding_client=embedding_client,
                    topic_sink=topic_sink,
                    llm_client=llm_client,
                )
                entered_terminal = await writer.execute(
                    lambda connection, sid=statement_id: _complete_linked_statement(
                        connection,
                        build_id=build_id,
                        statement_id=sid,
                        raw_text=raw_text,
                        concepts=concepts,
                        rejected_count=rejected,
                        semantic_choices=choices,
                        candidates=candidates,
                    )
                )
                current_statement_id = None
                if entered_terminal:
                    completed += 1
                    processed += 1
                emit_progress(
                    progress,
                    "topics",
                    "progress",
                    build_id=build_id,
                    message="statement linking",
                    current=min(processed, total),
                    total=total,
                    counters={
                        "statement_id": statement_id,
                        "concepts": len(concepts),
                        "candidates": len(candidates),
                    },
                )
                limit = os.getenv("DEXT_TEST_KILL_AFTER_TOPIC_JOBS")
                if limit and completed >= int(limit):
                    os._exit(95)
            except asyncio.CancelledError:
                if current_statement_id is not None:
                    with suppress(Exception):
                        await asyncio.shield(
                            mark_retry(current_statement_id, "cancelled")
                        )
                raise
            except _TopicLLMOutputError as exc:
                error = _safe_error(exc)
                deferred_statement_ids.add(statement_id)
                await mark_retry(statement_id, error)
                current_statement_id = None
                emit_progress(
                    progress,
                    "topics",
                    "progress",
                    build_id=build_id,
                    message="statement linking",
                    current=min(processed, total),
                    total=total,
                    counters={
                        "statement_id": statement_id,
                        "status": "retry",
                        "error": error,
                    },
                )
                continue
            except Exception as exc:
                if current_statement_id is not None:
                    await mark_retry(current_statement_id, _safe_error(exc))
                    current_statement_id = None
                raise

    tasks = [
        asyncio.create_task(worker(index), name=f"topic-link-worker-{index}")
        for index in range(settings.topic_link_concurrency)
    ]
    pending: set[asyncio.Task[None]] = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_EXCEPTION
            )
            for task in done:
                exc = task.exception()
                if exc is not None:
                    for pending_task in pending:
                        pending_task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise exc
        if deferred_statement_ids:
            raise TopicLinkDeferredRetryError(
                "topic linking deferred "
                f"{len(deferred_statement_ids)} retryable statement(s)"
            )
        return completed
    except asyncio.CancelledError:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        raise


def _finish(
    connection: sqlite3.Connection,
    build_id: str,
    run_id: str,
    *,
    topic_count: int,
    relation_count: int,
) -> None:
    linked_statements = int(
        connection.execute(
            "SELECT COUNT(*) FROM topic_link_jobs WHERE build_id=? AND status='succeeded'",
            (build_id,),
        ).fetchone()[0]
    )
    approved = int(
        connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links WHERE build_id=? AND review_status='approved'",
            (build_id,),
        ).fetchone()[0]
    )
    review = int(
        connection.execute(
            "SELECT COUNT(*) FROM statement_topic_links WHERE build_id=? AND review_status='review'",
            (build_id,),
        ).fetchone()[0]
    )
    summary = {
        "topic_run_version": TOPIC_RUN_VERSION,
        "active_topics": topic_count,
        "linked_statements": linked_statements,
        "approved_links": approved,
        "review_links": review,
        "topic_relations": relation_count,
        "gold_status": "not_evaluated",
    }
    connection.execute(
        "UPDATE topic_runs SET status='COMPLETED',summary_json=?,finished_at=?,last_error=NULL WHERE id=?",
        (json_dumps(summary), utcnow_iso(), run_id),
    )
    build_summary = json_loads(
        connection.execute(
            "SELECT summary_json FROM graph_builds WHERE id=?", (build_id,)
        ).fetchone()[0],
        {},
    )
    build_summary["topics"] = summary
    connection.execute(
        "UPDATE graph_builds SET status='WRITING_VECTOR',summary_json=?,last_error=NULL WHERE id=?",
        (json_dumps(build_summary), build_id),
    )
    if connection.execute(
        "SELECT 1 FROM vector_runs WHERE build_id=?", (build_id,)
    ).fetchone() is not None:
        connection.execute(
            "UPDATE vector_runs SET status='PENDING',finished_at=NULL,last_error=NULL WHERE build_id=?",
            (build_id,),
        )
        connection.execute(
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink='qdrant' "
            "AND partition_key='professors'",
            (build_id,),
        )


async def run_topic_stage(
    writer: CatalogWriter,
    build_id: str,
    settings: GraphSettings,
    *,
    progress: ProgressCallback | None = None,
    embedding_client: Any | None = None,
    topic_sink: Any | None = None,
    llm_client: Any | None = None,
    tokenizer: Any | None = None,
    neo4j_writer: Any | None = None,
) -> dict[str, Any]:
    tokenizer = tokenizer or TransformersTokenizer(
        settings.tokenizer_model, settings.tokenizer_revision
    )
    run_id, manifest, fingerprint = await writer.execute(
        lambda connection: _prepare_run(
            connection, build_id, settings, tokenizer_identity=tokenizer.identity
        )
    )
    run = await writer.execute(
        lambda connection: dict(
            connection.execute("SELECT * FROM topic_runs WHERE id=?", (run_id,)).fetchone()
        ),
        transactional=False,
    )
    if run["status"] == "COMPLETED":
        from dext_graph.catalog.workflow import build_status

        return build_status(writer.path, build_id)

    owns_embedding = embedding_client is None
    owns_sink = topic_sink is None
    owns_llm = llm_client is None
    neo4j_writer = neo4j_writer or write_neo4j_exports
    try:
        emit_progress(
            progress,
            "topics",
            "started",
            build_id=build_id,
            message="topic taxonomy/linking stage",
        )
        needs_llm = await writer.execute(
            lambda connection: _has_pending_topic_link_work(connection, build_id),
            transactional=False,
        )
        if needs_llm:
            if llm_client is None:
                llm_client = TopicLLMClient(settings)
            await _preflight_topic_llm(llm_client)
        if embedding_client is None:
            embedding_client = EmbeddingClient(settings, lambda _metric: None)
        if topic_sink is None:
            topic_sink = TopicQdrant(settings.qdrant_url)
        relation_count = await writer.execute(
            lambda connection: materialize_taxonomy_relations(
                connection, build_id=build_id, manifest=manifest
            )
        )
        emit_progress(
            progress,
            "topics",
            "progress",
            build_id=build_id,
            message="taxonomy relations",
            counters={"relations": relation_count},
        )
        collection_name, topic_count = await _build_candidate_collection(
            writer,
            build_id=build_id,
            taxonomy_version=manifest.version,
            fingerprint=fingerprint,
            settings=settings,
            tokenizer=tokenizer,
            embedding_client=embedding_client,
            topic_sink=topic_sink,
            progress=progress,
        )
        await _link_statements(
            writer,
            build_id=build_id,
            taxonomy_version=manifest.version,
            collection_name=collection_name,
            tokenizer=tokenizer,
            settings=settings,
            embedding_client=embedding_client,
            topic_sink=topic_sink,
            llm_client=llm_client,
            progress=progress,
        )
        await freeze_graph_exports(writer, build_id, settings, progress=progress)
        if neo4j_writer is write_neo4j_exports:
            await neo4j_writer(writer, build_id, settings, progress=progress)
        else:
            await neo4j_writer(writer, build_id, settings)
        await writer.execute(
            lambda connection: _finish(
                connection,
                build_id,
                run_id,
                topic_count=topic_count,
                relation_count=relation_count,
            )
        )
        emit_progress(
            progress,
            "topics",
            "completed",
            build_id=build_id,
            message="topic taxonomy/linking stage",
            current=topic_count,
            total=topic_count,
            counters={"topics": topic_count, "relations": relation_count},
        )
    except Exception as exc:
        error = _safe_error(exc)
        await writer.execute(
            lambda connection: (
                connection.execute(
                    "UPDATE topic_runs SET status='FAILED',last_error=?,finished_at=? WHERE id=?",
                    (error, utcnow_iso(), run_id),
                ),
                connection.execute(
                    "UPDATE graph_builds SET status='FAILED',last_error=? WHERE id=?",
                    (error, build_id),
                ),
                connection.execute(
                    "UPDATE topic_candidate_collections SET status='FAILED',updated_at=? "
                    "WHERE taxonomy_version=? AND embedding_fingerprint=? AND status='BUILDING'",
                    (utcnow_iso(), manifest.version, fingerprint),
                ),
            )
        )
        emit_progress(
            progress,
            "topics",
            "failed",
            build_id=build_id,
            message="topic taxonomy/linking stage",
            counters={"error": error},
        )
    finally:
        if owns_embedding and embedding_client is not None:
            await embedding_client.close()
        if owns_sink and topic_sink is not None:
            await topic_sink.close()
        if owns_llm and llm_client is not None:
            await llm_client.close()

    from dext_graph.catalog.workflow import build_status

    return build_status(writer.path, build_id)


async def topic_build(
    build_id: str,
    settings: GraphSettings | None = None,
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        from dext_graph.catalog.workflow import _backup_progress

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
            return await run_topic_stage(
                writer, build_id, settings, progress=progress
            )


__all__ = ["TOPIC_RUN_VERSION", "run_topic_stage", "topic_build"]
