"""Recoverable stage-5 Topic taxonomy and Statement linking workflow."""

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
    initialize_catalog,
    json_dumps,
    json_loads,
    utcnow_iso,
)
from dext_graph.catalog.evidence import freeze_graph_exports
from dext_graph.catalog.neo4j_sink import write_neo4j_exports
from dext_graph.catalog.normalization import normalize_text
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
from dext_graph.profiles import TransformersTokenizer, prefixed_input

TOPIC_RUN_VERSION = "topic-dag-v1"


def _safe_error(exc: BaseException) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ").strip()
    return (value or type(exc).__name__)[:2000]


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
    taxonomy_version: str,
    fingerprint: str,
    settings: GraphSettings,
    tokenizer: Any,
    embedding_client: Any,
    topic_sink: Any,
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


def _last_statement_key(connection: sqlite3.Connection, build_id: str) -> str:
    row = connection.execute(
        "SELECT last_key FROM sink_checkpoints WHERE build_id=? "
        "AND sink='topic_link' AND partition_key='statements'",
        (build_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] is not None else ""


def _checkpoint_statement(
    connection: sqlite3.Connection, build_id: str, statement_id: str
) -> None:
    connection.execute(
        """
        INSERT INTO sink_checkpoints(
          build_id,sink,partition_key,last_key,last_batch_id,rows_written,updated_at
        ) VALUES (?,'topic_link','statements',?,NULL,1,?)
        ON CONFLICT(build_id,sink,partition_key) DO UPDATE SET
          last_key=excluded.last_key,
          rows_written=sink_checkpoints.rows_written + 1,
          updated_at=excluded.updated_at
        """,
        (build_id, statement_id, utcnow_iso()),
    )


def _next_statement(
    connection: sqlite3.Connection, build_id: str, after_statement_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT s.id,s.raw_text FROM research_statements s
        LEFT JOIN topic_link_jobs j ON j.build_id=s.build_id AND j.statement_id=s.id
        WHERE s.build_id=? AND s.id>? AND (j.status IS NULL OR j.status!='succeeded')
        ORDER BY s.id LIMIT 1
        """,
        (build_id, after_statement_id),
    ).fetchone()
    return dict(row) if row is not None else None


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


async def _semantic_choices(
    concepts: list[TopicConcept],
    *,
    taxonomy_version: str,
    collection_name: str,
    writer: CatalogWriter,
    tokenizer: Any,
    settings: GraphSettings,
    embedding_client: Any,
    topic_sink: Any,
    llm_client: Any,
) -> tuple[dict[str, tuple[str, float] | str], list[dict[str, Any]]]:
    unresolved: list[TopicConcept] = []
    for concept in concepts:
        exact = await writer.execute(
            lambda connection, key=topic_alias_key(concept.canonical_name): connection.execute(
                "SELECT 1 FROM topic_aliases WHERE taxonomy_version=? AND alias_key=?",
                (taxonomy_version, key),
            ).fetchone()
            is not None,
            transactional=False,
        )
        if not exact:
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
        choices[concept.evidence_span] = await llm_client.select(concept, candidates)
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
) -> int:
    completed = 0
    last_statement_id = await writer.execute(
        lambda connection: _last_statement_key(connection, build_id),
        transactional=False,
    )
    while True:
        statement = await writer.execute(
            lambda connection: _next_statement(
                connection, build_id, last_statement_id
            ),
            transactional=False,
        )
        if statement is None:
            return completed
        statement_id = str(statement["id"])
        if normalize_text(statement["raw_text"]) is None:
            await writer.execute(
                lambda connection: _mark_job(
                    connection,
                    build_id=build_id,
                    statement_id=statement_id,
                    status="terminal-invalid-input",
                    error="empty_or_punctuation_only_statement",
                )
            )
            await writer.execute(
                lambda connection: _checkpoint_statement(
                    connection, build_id, statement_id
                )
            )
            last_statement_id = statement_id
            continue
        await writer.execute(
            lambda connection: _mark_job(
                connection,
                build_id=build_id,
                statement_id=statement_id,
                status="running",
            )
        )
        try:
            concepts = await llm_client.extract(str(statement["raw_text"]))
            rejected = int(getattr(llm_client, "last_rejected_count", 0) or 0)
            if rejected:
                await writer.execute(
                    lambda connection: record_topic_finding(
                        connection,
                        build_id=build_id,
                        code="topic_concept_rejected",
                        reference=statement_id,
                        details={
                            "statement_id": statement_id,
                            "rejected_concepts": rejected,
                            "reason": "invalid_evidence_kind_or_relation",
                        },
                    )
                )
            choices, candidates = await _semantic_choices(
                concepts,
                taxonomy_version=taxonomy_version,
                collection_name=collection_name,
                writer=writer,
                tokenizer=tokenizer,
                settings=settings,
                embedding_client=embedding_client,
                topic_sink=topic_sink,
                llm_client=llm_client,
            )
            await writer.execute(
                lambda connection: apply_statement_concepts(
                    connection,
                    build_id=build_id,
                    statement_id=statement_id,
                    raw_text=str(statement["raw_text"]),
                    concepts=[
                        {
                            "evidence_span": concept.evidence_span,
                            "canonical_name": concept.canonical_name,
                            "kind": concept.kind,
                            "relation_type": concept.relation_type,
                        }
                        for concept in concepts
                    ],
                    semantic_choices=choices,
                )
            )
            await writer.execute(
                lambda connection: _mark_job(
                    connection,
                    build_id=build_id,
                    statement_id=statement_id,
                    status="succeeded",
                    candidates=candidates,
                )
            )
            await writer.execute(
                lambda connection: _checkpoint_statement(
                    connection, build_id, statement_id
                )
            )
            last_statement_id = statement_id
            completed += 1
            limit = os.getenv("DEXT_TEST_KILL_AFTER_TOPIC_JOBS")
            if limit and completed >= int(limit):
                os._exit(95)
        except Exception as exc:
            error = _safe_error(exc)
            await writer.execute(
                lambda connection: _mark_job(
                    connection,
                    build_id=build_id,
                    statement_id=statement_id,
                    status="retry",
                    error=error,
                )
            )
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
    if embedding_client is None:
        embedding_client = EmbeddingClient(settings, lambda _metric: None)
    if topic_sink is None:
        topic_sink = TopicQdrant(settings.qdrant_url)
    if llm_client is None:
        llm_client = TopicLLMClient(settings)
    neo4j_writer = neo4j_writer or write_neo4j_exports
    try:
        relation_count = await writer.execute(
            lambda connection: materialize_taxonomy_relations(
                connection, build_id=build_id, manifest=manifest
            )
        )
        collection_name, topic_count = await _build_candidate_collection(
            writer,
            taxonomy_version=manifest.version,
            fingerprint=fingerprint,
            settings=settings,
            tokenizer=tokenizer,
            embedding_client=embedding_client,
            topic_sink=topic_sink,
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
        )
        await freeze_graph_exports(writer, build_id, settings)
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
    finally:
        if owns_embedding:
            await embedding_client.close()
        if owns_sink:
            await topic_sink.close()
        if owns_llm:
            await llm_client.close()

    from dext_graph.catalog.workflow import build_status

    return build_status(writer.path, build_id)


async def topic_build(build_id: str, settings: GraphSettings | None = None) -> dict[str, Any]:
    settings = settings or GraphSettings()
    path = Path(settings.catalog_path).expanduser().resolve()
    with catalog_write_lock(path):
        backup_existing_catalog(path)
        initialize_catalog(path)
        async with CatalogWriter(path, max_queue=settings.build_write_queue) as writer:
            return await run_topic_stage(writer, build_id, settings)


__all__ = ["TOPIC_RUN_VERSION", "run_topic_stage", "topic_build"]
