import pytest

from dext_graph.evaluation import cosine, request_summary, retrieval_metrics


def test_retrieval_metrics_use_graded_ndcg_and_pooled_recall():
    candidates = [
        {"query_id": "q1", "source_row_key": "b", "rank": 1},
        {"query_id": "q1", "source_row_key": "a", "rank": 2},
    ]
    judgments = {("q1", "a"): 2, ("q1", "b"): 0, ("q1", "c"): 1}
    metrics = retrieval_metrics(candidates, judgments)
    assert metrics["pooled_recall_at_20"] == 0.5
    assert 0 < metrics["ndcg_at_10"] < 1
    assert metrics["top_10_no_relevant_rate"] == 0


def test_request_summary_counts_attempt_rates_and_usage():
    rows = [
        {"latency_ms": 10, "status_code": 429, "succeeded": False},
        {"latency_ms": 30, "status_code": 503, "succeeded": False},
        {
            "latency_ms": 20,
            "status_code": 200,
            "succeeded": True,
            "prompt_tokens": 9,
            "total_tokens": 9,
        },
    ]
    summary = request_summary(rows)
    assert summary["attempt_count"] == 3
    assert summary["http_429_rate_per_attempt"] == pytest.approx(1 / 3)
    assert summary["http_5xx_rate_per_attempt"] == pytest.approx(1 / 3)
    assert summary["latency_ms_p50"] == 20
    assert summary["total_tokens"] == 9


def test_cosine_known_vectors():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0)
