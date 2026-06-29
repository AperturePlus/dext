"""End-to-end orchestration for the temporary semantic value-validation slice."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from dext_graph.artifacts import (
    JsonlWriter,
    atomic_write_json,
    atomic_write_text,
    code_version,
    collection_name,
    iter_jsonl,
    new_artifact_dir,
    read_json,
    read_jsonl,
    timestamp_id,
    utcnow,
    vector_checksum,
)
from dext_graph.assets import canonical_hash, load_queries, load_sentinels
from dext_graph.config import GraphSettings
from dext_graph.embeddings import EmbeddingClient
from dext_graph.evaluation import (
    cosine,
    percentile,
    request_summary,
    retrieval_metrics,
    sentinel_stability,
)
from dext_graph.models import ProfileRecord, QueryRecord, RequestMetric, SourceInfo, ValueValidationError
from dext_graph.profiles import TransformersTokenizer, build_profile, prefixed_input
from dext_graph.source import file_sha256, inspect_source, iter_professor_batches
from dext_graph.vector_store import TemporaryQdrant


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _manifest_path(directory: Path) -> Path:
    return directory / "manifest.json"


def _load_experiment(directory: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(directory).expanduser().resolve()
    manifest_path = _manifest_path(path)
    if not manifest_path.is_file():
        raise ValueValidationError(f"experiment manifest does not exist: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("artifact_class") != "temporary_value_validation_experiment":
        raise ValueValidationError(f"not a value-validation experiment: {path}")
    if manifest.get("status") != "completed":
        raise ValueValidationError(f"experiment is not completed: {path}")
    return path, manifest


def _new_tokenizer(settings: GraphSettings) -> TransformersTokenizer:
    return TransformersTokenizer(settings.tokenizer_model, settings.tokenizer_revision)


def _source_warnings(source: SourceInfo) -> list[str]:
    if source.crawl_status == "completed":
        return []
    return [
        f"crawl_status={source.crawl_status!r} was accepted because the source DB was explicitly selected"
    ]


def _profile_inputs(
    records: list[ProfileRecord],
    tokenizer: TransformersTokenizer,
    settings: GraphSettings,
) -> list[str]:
    return [
        prefixed_input(
            record.normalized_profile,
            settings.embedding_passage_prefix,
            tokenizer,
            max_tokens=settings.embedding_max_input_tokens,
        )
        for record in records
    ]


def dry_run(source_db: str | Path, template: str, settings: GraphSettings) -> dict[str, Any]:
    source = inspect_source(source_db)
    query_version, queries, query_hash = load_queries()
    sentinel_version, sentinels, sentinel_hash = load_sentinels()
    tokenizer = _new_tokenizer(settings)
    profile_count = 0
    total_profile_tokens = 0
    total_provider_profile_tokens = 0
    max_profile_tokens = 0
    skipped = 0
    for batch in iter_professor_batches(
        source_db, source, batch_size=settings.source_read_batch
    ):
        for professor in batch:
            profile = build_profile(
                professor, template, tokenizer, max_tokens=settings.profile_max_tokens
            )
            if profile is None:
                skipped += 1
                continue
            provider_input = prefixed_input(
                profile.normalized_profile,
                settings.embedding_passage_prefix,
                tokenizer,
                max_tokens=settings.embedding_max_input_tokens,
            )
            profile_count += 1
            total_profile_tokens += profile.token_count
            total_provider_profile_tokens += len(
                tokenizer.encode(provider_input, add_special_tokens=True)
            )
            max_profile_tokens = max(max_profile_tokens, profile.token_count)
    query_tokens = sum(
        len(
            tokenizer.encode(
                prefixed_input(
                    query.text,
                    settings.embedding_query_prefix,
                    tokenizer,
                    max_tokens=settings.embedding_max_input_tokens,
                ),
                add_special_tokens=True,
            )
        )
        for query in queries
    )
    sentinel_tokens = sum(
        len(
            tokenizer.encode(
                prefixed_input(
                    item["text"],
                    settings.embedding_passage_prefix,
                    tokenizer,
                    max_tokens=settings.embedding_max_input_tokens,
                ),
                add_special_tokens=True,
            )
        )
        for item in sentinels
    ) * 2
    request_count = (
        math.ceil(profile_count / settings.embedding_request_batch)
        + math.ceil(len(queries) / settings.embedding_request_batch)
        + 2 * math.ceil(len(sentinels) / settings.embedding_request_batch)
    )
    return {
        "mode": "dry_run",
        "source": source.asdict(),
        "source_warnings": _source_warnings(source),
        "template": template,
        "tokenizer": tokenizer.identity,
        "query_set": {"version": query_version, "hash": query_hash, "count": len(queries)},
        "sentinel_set": {
            "version": sentinel_version,
            "hash": sentinel_hash,
            "count": len(sentinels),
        },
        "profiles": {
            "generated": profile_count,
            "skipped_without_semantic_content": skipped,
            "total_canonical_tokens": total_profile_tokens,
            "max_canonical_tokens": max_profile_tokens,
        },
        "estimated_provider_input_tokens": (
            total_provider_profile_tokens + query_tokens + sentinel_tokens
        ),
        "estimated_provider_requests": request_count,
    }


async def _embed_and_upsert_profiles(
    records: list[ProfileRecord],
    tokenizer: TransformersTokenizer,
    settings: GraphSettings,
    client: EmbeddingClient,
    qdrant: TemporaryQdrant,
    collection: str,
) -> None:
    chunks = _chunks(records, settings.embedding_request_batch)
    for window in _chunks(chunks, settings.embedding_max_concurrency):
        tasks = [
            client.embed(_profile_inputs(chunk, tokenizer, settings), purpose="professor_profiles")
            for chunk in window
        ]
        results = await asyncio.gather(*tasks)
        for chunk, result in zip(window, results, strict=True):
            await qdrant.upsert(collection, chunk, result.vectors)


async def _embed_sentinels(
    client: EmbeddingClient,
    sentinels: list[dict[str, str]],
    tokenizer: TransformersTokenizer,
    settings: GraphSettings,
    *,
    repeat: int,
) -> list[dict[str, Any]]:
    inputs = [
        prefixed_input(
            item["text"],
            settings.embedding_passage_prefix,
            tokenizer,
            max_tokens=settings.embedding_max_input_tokens,
        )
        for item in sentinels
    ]
    result = await client.embed(
        inputs, purpose=f"sentinel_repeat_{repeat}"
    )
    return [
        {
            "sentinel_id": item["id"],
            "text": item["text"],
            "repeat": repeat,
            "vector": vector,
            "checksum": vector_checksum(vector),
        }
        for item, vector in zip(sentinels, result.vectors, strict=True)
    ]


async def run_live(
    source_db: str | Path,
    template: str,
    settings: GraphSettings,
) -> dict[str, Any]:
    if not settings.embedding_api_key:
        raise ValueValidationError("DEXT_EMBEDDING_API_KEY is not set")
    source = inspect_source(source_db)
    query_version, queries, query_hash = load_queries()
    sentinel_version, sentinels, sentinel_hash = load_sentinels()
    tokenizer = _new_tokenizer(settings)
    experiment_id = f"{timestamp_id()}_{template}"
    experiment_dir = new_artifact_dir(
        settings.value_validation_root, "experiments", experiment_id
    ).resolve()
    collection = collection_name(experiment_id, settings.embedding_model, template)
    manifest: dict[str, Any] = {
        "artifact_class": "temporary_value_validation_experiment",
        "schema_version": 1,
        "experiment_id": experiment_id,
        "status": "running",
        "started_at": utcnow(),
        "source": source.asdict(),
        "source_selection": {
            "mode": "explicit_path",
            "non_completed_status_accepted": source.crawl_status != "completed",
            "warnings": _source_warnings(source),
        },
        "template": template,
        "tokenizer": tokenizer.identity,
        "query_set": {"version": query_version, "hash": query_hash, "count": len(queries)},
        "sentinel_set": {
            "version": sentinel_version,
            "hash": sentinel_hash,
            "count": len(sentinels),
        },
        "embedding": settings.safe_snapshot(),
        "collection_name": collection,
        "code": code_version(Path.cwd()),
    }
    atomic_write_json(_manifest_path(experiment_dir), manifest)
    request_metrics: list[dict[str, Any]] = []
    metric_writer = JsonlWriter(experiment_dir / "requests.jsonl")

    def metric_sink(metric: RequestMetric) -> None:
        row = metric.asdict()
        request_metrics.append(row)
        metric_writer.write(row)

    collection_created = False
    profile_count = 0
    skipped = 0
    try:
        async with EmbeddingClient(settings, metric_sink) as embedding_client:
            async with TemporaryQdrant(settings.qdrant_url) as qdrant:
                await qdrant.create(collection, settings.embedding_dimension)
                collection_created = True
                sentinel_rows = await _embed_sentinels(
                    embedding_client, sentinels, tokenizer, settings, repeat=1
                )

                with JsonlWriter(experiment_dir / "profiles.jsonl") as profile_writer:
                    for source_batch in iter_professor_batches(
                        source_db, source, batch_size=settings.source_read_batch
                    ):
                        records: list[ProfileRecord] = []
                        for professor in source_batch:
                            profile = build_profile(
                                professor,
                                template,
                                tokenizer,
                                max_tokens=settings.profile_max_tokens,
                            )
                            if profile is None:
                                skipped += 1
                                continue
                            profile_writer.write(profile.asdict())
                            records.append(profile)
                            profile_count += 1
                        await _embed_and_upsert_profiles(
                            records,
                            tokenizer,
                            settings,
                            embedding_client,
                            qdrant,
                            collection,
                        )

                qdrant_count = await qdrant.count(collection)
                if qdrant_count != profile_count:
                    raise ValueValidationError(
                        f"Qdrant count mismatch: expected {profile_count}, found {qdrant_count}"
                    )
                sentinel_rows.extend(
                    await _embed_sentinels(
                        embedding_client, sentinels, tokenizer, settings, repeat=2
                    )
                )
                with JsonlWriter(experiment_dir / "sentinels.jsonl") as writer:
                    for row in sentinel_rows:
                        writer.write(row)

                # The full profile JSONL stays streamed on disk. Only the bounded
                # top-20 evaluation pool is retained to enrich labeling context.
                bare_candidates: list[dict[str, Any]] = []
                for query_chunk in _chunks(queries, settings.embedding_request_batch):
                    query_inputs = [
                        prefixed_input(
                            query.text,
                            settings.embedding_query_prefix,
                            tokenizer,
                            max_tokens=settings.embedding_max_input_tokens,
                        )
                        for query in query_chunk
                    ]
                    query_result = await embedding_client.embed(
                        query_inputs, purpose="evaluation_queries"
                    )
                    for query, vector in zip(query_chunk, query_result.vectors, strict=True):
                        candidates = await qdrant.query(collection, vector, limit=20)
                        for rank, candidate in enumerate(candidates, start=1):
                            payload = candidate["payload"]
                            bare_candidates.append(
                                {
                                    "experiment_id": experiment_id,
                                    "query_id": query.query_id,
                                    "query_text": query.text,
                                    "query_categories": list(query.categories),
                                    "query_disciplines": list(query.disciplines),
                                    "rank": rank,
                                    "score": candidate["score"],
                                    "point_id": candidate["point_id"],
                                    "source_row_key": str(payload["source_row_key"]),
                                    "profile_hash": payload["profile_hash"],
                                }
                            )
                candidate_keys = {row["source_row_key"] for row in bare_candidates}
                profile_index: dict[str, dict[str, Any]] = {}
                for profile in iter_jsonl(experiment_dir / "profiles.jsonl"):
                    source_key = str(profile["source_row_key"])
                    if source_key in candidate_keys:
                        profile_index[source_key] = profile
                if set(profile_index) != candidate_keys:
                    raise ValueValidationError(
                        "one or more Qdrant candidates have unknown source keys"
                    )
                with JsonlWriter(experiment_dir / "candidates.jsonl") as candidate_writer:
                    for candidate in bare_candidates:
                        profile = profile_index[candidate["source_row_key"]]
                        candidate_writer.write(
                            {
                                **candidate,
                                "professor_name": profile["name"],
                                "university": profile["university"],
                                "org_units": profile["org_units"],
                                "title": profile["title"],
                                "research_areas": profile["research_areas"],
                                "publications": profile["publications"],
                                "bio": profile["bio"],
                            }
                        )

        metric_writer.close()
        fingerprint = hashlib.sha256(
            "\n".join(row["checksum"] for row in sentinel_rows).encode("ascii")
        ).hexdigest()
        manifest.update(
            {
                "status": "completed",
                "completed_at": utcnow(),
                "profiles": {
                    "generated": profile_count,
                    "skipped_without_semantic_content": skipped,
                    "qdrant_count": profile_count,
                },
                "request_summary": request_summary(request_metrics),
                "embedding_fingerprint": fingerprint,
                "artifacts": {
                    name: {"sha256": file_sha256(experiment_dir / name)}
                    for name in (
                        "profiles.jsonl",
                        "requests.jsonl",
                        "sentinels.jsonl",
                        "candidates.jsonl",
                    )
                },
            }
        )
        atomic_write_json(_manifest_path(experiment_dir), manifest)
        return {
            "mode": "live",
            "experiment_dir": str(experiment_dir),
            "experiment_id": experiment_id,
            "collection_name": collection,
            "profiles": profile_count,
        }
    except Exception as exc:
        metric_writer.close()
        manifest.update(
            {
                "status": "failed",
                "failed_at": utcnow(),
                "failure": {
                    "type": type(exc).__name__,
                    "message": str(exc) if isinstance(exc, ValueValidationError) else type(exc).__name__,
                },
                "collection_created": collection_created,
                "request_summary": request_summary(request_metrics),
            }
        )
        atomic_write_json(_manifest_path(experiment_dir), manifest)
        raise


def pool_experiments(
    experiment_directories: list[str | Path], settings: GraphSettings
) -> dict[str, Any]:
    if len(experiment_directories) < 2:
        raise ValueValidationError("pool requires at least two completed experiments")
    experiments = [_load_experiment(path) for path in experiment_directories]
    source_hashes = {manifest["source"]["sha256"] for _, manifest in experiments}
    query_hashes = {manifest["query_set"]["hash"] for _, manifest in experiments}
    if len(source_hashes) != 1 or len(query_hashes) != 1:
        raise ValueValidationError("pooled experiments must use the same source and query set")

    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    for directory, manifest in experiments:
        for row in read_jsonl(directory / "candidates.jsonl"):
            key = (str(row["query_id"]), str(row["source_row_key"]))
            if key not in pooled:
                pooled[key] = {
                    "query_id": row["query_id"],
                    "query_text": row["query_text"],
                    "source_row_key": row["source_row_key"],
                    "professor_name": row["professor_name"],
                    "university": row["university"],
                    "org_units": row["org_units"],
                    "title": row["title"],
                    "research_areas": row["research_areas"],
                    "publications": row["publications"],
                    "bio": row["bio"],
                    "appearances": [],
                    "judgment": None,
                    "note": "",
                }
            pooled[key]["appearances"].append(
                {
                    "experiment_id": manifest["experiment_id"],
                    "profile_hash": row["profile_hash"],
                    "rank": row["rank"],
                    "score": row["score"],
                }
            )
    rows = [pooled[key] for key in sorted(pooled)]
    for row in rows:
        row["appearances"].sort(key=lambda item: item["experiment_id"])
    pool_dir = new_artifact_dir(settings.value_validation_root, "pools").resolve()
    judgments_path = pool_dir / "judgments.jsonl"
    with JsonlWriter(judgments_path) as writer:
        for row in rows:
            writer.write(row)
    manifest = {
        "artifact_class": "temporary_value_validation_pool",
        "schema_version": 1,
        "created_at": utcnow(),
        "source_sha256": next(iter(source_hashes)),
        "query_set_hash": next(iter(query_hashes)),
        "experiment_ids": [manifest["experiment_id"] for _, manifest in experiments],
        "candidate_count": len(rows),
        "judgments_sha256_before_annotation": file_sha256(judgments_path),
    }
    atomic_write_json(pool_dir / "manifest.json", manifest)
    return {"pool_dir": str(pool_dir), "judgments": str(judgments_path), "candidates": len(rows)}


def _load_judgments(path: str | Path) -> tuple[list[dict[str, Any]], dict[tuple[str, str], int]]:
    rows = read_jsonl(path)
    values: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows, start=1):
        judgment = row.get("judgment")
        if type(judgment) is not int or judgment not in {0, 1, 2}:
            raise ValueValidationError(
                f"judgment row {index} must contain integer judgment 0, 1, or 2"
            )
        key = (str(row["query_id"]), str(row["source_row_key"]))
        if key in values:
            raise ValueValidationError(f"duplicate judgment for query/source pair: {key}")
        values[key] = judgment
    return rows, values


def _adjacent_sentinel_cosines(
    experiments: list[tuple[Path, dict[str, Any]]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for (left_dir, left), (right_dir, right) in zip(experiments, experiments[1:]):
        left_rows = {
            row["sentinel_id"]: row
            for row in read_jsonl(left_dir / "sentinels.jsonl")
            if row["repeat"] == 1
        }
        right_rows = {
            row["sentinel_id"]: row
            for row in read_jsonl(right_dir / "sentinels.jsonl")
            if row["repeat"] == 1
        }
        for sentinel_id in sorted(set(left_rows) & set(right_rows)):
            output.append(
                {
                    "left_experiment_id": left["experiment_id"],
                    "right_experiment_id": right["experiment_id"],
                    "sentinel_id": sentinel_id,
                    "cosine": cosine(
                        left_rows[sentinel_id]["vector"], right_rows[sentinel_id]["vector"]
                    ),
                }
            )
    return output


def compare_experiments(
    experiment_directories: list[str | Path],
    judgments_path: str | Path,
    settings: GraphSettings,
) -> dict[str, Any]:
    if len(experiment_directories) < 2:
        raise ValueValidationError("compare requires at least two completed experiments")
    experiments = [_load_experiment(path) for path in experiment_directories]
    source_hashes = {manifest["source"]["sha256"] for _, manifest in experiments}
    query_hashes = {manifest["query_set"]["hash"] for _, manifest in experiments}
    if len(source_hashes) != 1 or len(query_hashes) != 1:
        raise ValueValidationError("compared experiments must use the same source and query set")
    judgment_rows, judgments = _load_judgments(judgments_path)

    experiment_results: list[dict[str, Any]] = []
    for directory, manifest in experiments:
        candidates = read_jsonl(directory / "candidates.jsonl")
        missing = [
            (row["query_id"], row["source_row_key"])
            for row in candidates
            if (str(row["query_id"]), str(row["source_row_key"])) not in judgments
        ]
        if missing:
            raise ValueValidationError(
                f"judgments do not cover {len(missing)} candidates from {manifest['experiment_id']}"
            )
        experiment_results.append(
            {
                "experiment_id": manifest["experiment_id"],
                "template": manifest["template"],
                "collection_name": manifest["collection_name"],
                "retrieval": retrieval_metrics(candidates, judgments),
                "service": request_summary(read_jsonl(directory / "requests.jsonl")),
                "sentinel_repeat_stability": sentinel_stability(
                    read_jsonl(directory / "sentinels.jsonl")
                ),
            }
        )
    adjacent = _adjacent_sentinel_cosines(experiments)
    adjacent_values = [float(row["cosine"]) for row in adjacent]
    comparison_dir = new_artifact_dir(settings.value_validation_root, "comparisons").resolve()
    results = {
        "artifact_class": "temporary_value_validation_comparison_results",
        "schema_version": 1,
        "created_at": utcnow(),
        "source_sha256": next(iter(source_hashes)),
        "query_set_hash": next(iter(query_hashes)),
        "judgments_sha256": file_sha256(Path(judgments_path)),
        "judgment_count": len(judgment_rows),
        "experiments": experiment_results,
        "adjacent_experiment_sentinel_cosines": adjacent,
        "adjacent_experiment_sentinel_summary": {
            "count": len(adjacent_values),
            "cosine_min": min(adjacent_values) if adjacent_values else None,
            "cosine_mean": (
                sum(adjacent_values) / len(adjacent_values) if adjacent_values else None
            ),
            "cosine_p50": percentile(adjacent_values, 0.50),
            "cosine_p95": percentile(adjacent_values, 0.95),
        },
    }
    atomic_write_json(comparison_dir / "results.json", results)
    report_lines = [
        "# 阶段 0 语义价值验证报告",
        "",
        "> 状态：等待产品结论",
        "",
        "Recall@20 为参与比较实验 top-20 联合标注池上的 pooled Recall@20，不代表全库穷举 recall。",
        "",
        "| 实验 | 模板 | nDCG@10 | pooled Recall@20 | top-10 无相关率 | p50 ms | p95 ms | 429率 | 5xx率 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in experiment_results:
        retrieval = row["retrieval"]
        service = row["service"]
        report_lines.append(
            f"| {row['experiment_id']} | {row['template']} | "
            f"{retrieval['ndcg_at_10']:.4f} | {retrieval['pooled_recall_at_20']:.4f} | "
            f"{retrieval['top_10_no_relevant_rate']:.4f} | "
            f"{service['latency_ms_p50'] or 0:.1f} | {service['latency_ms_p95'] or 0:.1f} | "
            f"{service['http_429_rate_per_attempt']:.4f} | {service['http_5xx_rate_per_attempt']:.4f} |"
        )
    report_lines.extend(["", "## 产品结论", "", "尚未 finalize。"])
    atomic_write_text(comparison_dir / "report.md", "\n".join(report_lines) + "\n")
    manifest = {
        "artifact_class": "temporary_value_validation_comparison",
        "schema_version": 1,
        "created_at": utcnow(),
        "finalized": False,
        "experiment_ids": [manifest["experiment_id"] for _, manifest in experiments],
        "collection_names": [manifest["collection_name"] for _, manifest in experiments],
        "source_sha256": next(iter(source_hashes)),
        "query_set_hash": next(iter(query_hashes)),
        "results_sha256": file_sha256(comparison_dir / "results.json"),
        "report_sha256": file_sha256(comparison_dir / "report.md"),
    }
    atomic_write_json(comparison_dir / "manifest.json", manifest)
    return {"comparison_dir": str(comparison_dir), "experiments": len(experiments)}


def finalize_comparison(
    comparison_directory: str | Path,
    decision: str,
    rationale: str,
) -> dict[str, Any]:
    if decision not in {"accept", "iterate", "stop"}:
        raise ValueError("decision must be accept, iterate, or stop")
    rationale = rationale.strip()
    if not rationale:
        raise ValueValidationError("finalization rationale must not be empty")
    directory = Path(comparison_directory).expanduser().resolve()
    manifest_path = directory / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("artifact_class") != "temporary_value_validation_comparison":
        raise ValueValidationError(f"not a value-validation comparison: {directory}")
    if manifest.get("finalized"):
        raise ValueValidationError("comparison is already finalized")
    report_path = directory / "report.md"
    report = report_path.read_text(encoding="utf-8")
    report = report.replace(
        "> 状态：等待产品结论", f"> 状态：已定稿（{decision}）"
    ).replace("尚未 finalize。", f"决定：`{decision}`\n\n{rationale}")
    atomic_write_text(report_path, report)
    manifest.update(
        {
            "finalized": True,
            "finalized_at": utcnow(),
            "decision": decision,
            "rationale": rationale,
            "report_sha256": file_sha256(report_path),
        }
    )
    atomic_write_json(manifest_path, manifest)
    return {"comparison_dir": str(directory), "decision": decision, "finalized": True}


async def cleanup_experiment(
    experiment_directory: str | Path,
    comparison_directory: str | Path,
    settings: GraphSettings,
) -> dict[str, Any]:
    experiment_dir, experiment = _load_experiment(experiment_directory)
    comparison_dir = Path(comparison_directory).expanduser().resolve()
    comparison = read_json(comparison_dir / "manifest.json")
    if comparison.get("artifact_class") != "temporary_value_validation_comparison":
        raise ValueValidationError("cleanup requires a value-validation comparison")
    if not comparison.get("finalized"):
        raise ValueValidationError("cleanup requires a finalized comparison")
    if experiment["experiment_id"] not in comparison.get("experiment_ids", []):
        raise ValueValidationError("finalized comparison does not reference this experiment")
    async with TemporaryQdrant(settings.qdrant_url) as qdrant:
        await qdrant.delete(experiment["collection_name"])
    experiment["collection_deleted_at"] = utcnow()
    experiment["collection_deleted_after_comparison"] = str(comparison_dir)
    atomic_write_json(_manifest_path(experiment_dir), experiment)
    return {
        "experiment_dir": str(experiment_dir),
        "collection_name": experiment["collection_name"],
        "deleted": True,
    }


__all__ = [
    "cleanup_experiment",
    "compare_experiments",
    "dry_run",
    "finalize_comparison",
    "pool_experiments",
    "run_live",
]
