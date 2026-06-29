"""Pure pooled-retrieval metrics and service/stability summaries."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import mean
from typing import Any

from dext_graph.models import ValueValidationError


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("cosine vectors must be non-empty and have equal dimensions")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("cosine is undefined for a zero vector")
    return dot / (left_norm * right_norm)


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def retrieval_metrics(
    candidates: list[dict[str, Any]],
    judgments: dict[tuple[str, str], int],
) -> dict[str, Any]:
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pool_grades: dict[str, list[int]] = defaultdict(list)
    for (query_id, _source_key), grade in judgments.items():
        pool_grades[query_id].append(grade)
    for row in candidates:
        by_query[str(row["query_id"])].append(row)

    per_query: list[dict[str, Any]] = []
    for query_id in sorted(by_query):
        ranked = sorted(by_query[query_id], key=lambda item: int(item["rank"]))
        grades = [
            judgments[(query_id, str(row["source_row_key"]))]
            for row in ranked[:20]
        ]
        top10 = grades[:10]
        ideal = sorted(pool_grades[query_id], reverse=True)[:10]
        ideal_dcg = _dcg(ideal)
        relevant_pool = sum(1 for grade in pool_grades[query_id] if grade > 0)
        relevant_retrieved = sum(1 for grade in grades if grade > 0)
        per_query.append(
            {
                "query_id": query_id,
                "ndcg_at_10": _dcg(top10) / ideal_dcg if ideal_dcg else 0.0,
                "pooled_recall_at_20": (
                    relevant_retrieved / relevant_pool if relevant_pool else 0.0
                ),
                "top_10_has_no_relevant": not any(grade > 0 for grade in top10),
                "relevant_in_pool": relevant_pool,
                "relevant_retrieved_at_20": relevant_retrieved,
            }
        )
    if not per_query:
        raise ValueValidationError("experiment has no candidate queries")
    return {
        "ndcg_at_10": mean(row["ndcg_at_10"] for row in per_query),
        "pooled_recall_at_20": mean(row["pooled_recall_at_20"] for row in per_query),
        "top_10_no_relevant_rate": mean(
            1.0 if row["top_10_has_no_relevant"] else 0.0 for row in per_query
        ),
        "query_count": len(per_query),
        "per_query": per_query,
        "recall_denominator": "union of labeled top-20 candidates from compared experiments",
    }


def request_summary(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["latency_ms"]) for row in metrics]
    statuses = [row.get("status_code") for row in metrics]
    attempts = len(metrics)
    succeeded = sum(1 for row in metrics if row.get("succeeded"))
    return {
        "attempt_count": attempts,
        "successful_logical_requests": succeeded,
        "latency_ms_p50": percentile(latencies, 0.50),
        "latency_ms_p95": percentile(latencies, 0.95),
        "http_429_rate_per_attempt": (
            sum(1 for status in statuses if status == 429) / attempts if attempts else 0.0
        ),
        "http_5xx_rate_per_attempt": (
            sum(1 for status in statuses if isinstance(status, int) and 500 <= status <= 599)
            / attempts
            if attempts
            else 0.0
        ),
        "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in metrics),
        "total_tokens": sum(int(row.get("total_tokens") or 0) for row in metrics),
    }


def sentinel_stability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[int, list[float]]] = defaultdict(dict)
    for row in rows:
        grouped[str(row["sentinel_id"])][int(row["repeat"])] = [
            float(value) for value in row["vector"]
        ]
    values: list[float] = []
    per_sentinel: dict[str, float] = {}
    for sentinel_id, repeats in sorted(grouped.items()):
        if set(repeats) != {1, 2}:
            raise ValueValidationError(f"sentinel {sentinel_id} does not have repeats 1 and 2")
        value = cosine(repeats[1], repeats[2])
        values.append(value)
        per_sentinel[sentinel_id] = value
    return {
        "count": len(values),
        "cosine_min": min(values) if values else None,
        "cosine_mean": mean(values) if values else None,
        "per_sentinel": per_sentinel,
    }


__all__ = [
    "cosine",
    "percentile",
    "request_summary",
    "retrieval_metrics",
    "sentinel_stability",
]
