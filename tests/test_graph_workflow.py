import json
from pathlib import Path

import pytest

from dext_graph.config import GraphSettings
from dext_graph.models import ValueValidationError
from dext_graph.workflow import (
    cleanup_experiment,
    compare_experiments,
    finalize_comparison,
    pool_experiments,
)


def _json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _experiment(root: Path, experiment_id: str, ranking: list[str]) -> Path:
    directory = root / experiment_id
    directory.mkdir()
    _json(
        directory / "manifest.json",
        {
            "artifact_class": "temporary_value_validation_experiment",
            "status": "completed",
            "experiment_id": experiment_id,
            "template": experiment_id,
            "source": {"sha256": "source-hash"},
            "query_set": {"hash": "query-hash"},
            "collection_name": f"dext_eval__{experiment_id}",
        },
    )
    candidate_rows = []
    for rank, source_key in enumerate(ranking, start=1):
        candidate_rows.append(
            {
                "experiment_id": experiment_id,
                "query_id": "q1",
                "query_text": "测试查询",
                "rank": rank,
                "score": 1 - rank / 10,
                "point_id": source_key,
                "source_row_key": source_key,
                "profile_hash": f"profile-{experiment_id}-{source_key}",
                "professor_name": f"教师{source_key}",
                "university": "测试大学",
                "org_units": ["测试学院"],
                "title": "教授",
                "research_areas": "研究方向",
                "publications": None,
                "bio": None,
            }
        )
    _jsonl(directory / "candidates.jsonl", candidate_rows)
    _jsonl(
        directory / "requests.jsonl",
        [
            {
                "latency_ms": 10,
                "status_code": 200,
                "succeeded": True,
                "prompt_tokens": 2,
                "total_tokens": 2,
            }
        ],
    )
    _jsonl(
        directory / "sentinels.jsonl",
        [
            {"sentinel_id": "s1", "repeat": 1, "vector": [1.0, 0.0]},
            {"sentinel_id": "s1", "repeat": 2, "vector": [1.0, 0.0]},
        ],
    )
    return directory


def test_pool_compare_and_finalize(tmp_path):
    experiments_root = tmp_path / "fixture-experiments"
    experiments_root.mkdir()
    first = _experiment(experiments_root, "baseline", ["a", "b"])
    second = _experiment(experiments_root, "research", ["b", "a"])
    settings = GraphSettings(value_validation_root=tmp_path / "artifacts")

    pooled = pool_experiments([first, second], settings)
    judgment_path = Path(pooled["judgments"])
    rows = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert all(len(row["appearances"]) == 2 for row in rows)
    for row in rows:
        row["judgment"] = 2 if row["source_row_key"] == "a" else 0
    _jsonl(judgment_path, rows)

    compared = compare_experiments([first, second], judgment_path, settings)
    comparison_dir = Path(compared["comparison_dir"])
    result = json.loads((comparison_dir / "results.json").read_text(encoding="utf-8"))
    assert result["experiments"][0]["retrieval"]["ndcg_at_10"] > result["experiments"][1]["retrieval"]["ndcg_at_10"]
    assert not json.loads((comparison_dir / "manifest.json").read_text())["finalized"]

    finalized = finalize_comparison(comparison_dir, "iterate", "继续优化研究方向预算。")
    assert finalized["finalized"]
    manifest = json.loads((comparison_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["decision"] == "iterate"
    assert "继续优化" in (comparison_dir / "report.md").read_text(encoding="utf-8")


def test_compare_rejects_missing_annotations(tmp_path):
    root = tmp_path / "experiments"
    root.mkdir()
    first = _experiment(root, "one", ["a"])
    second = _experiment(root, "two", ["a"])
    judgments = tmp_path / "judgments.jsonl"
    _jsonl(judgments, [{"query_id": "q1", "source_row_key": "a", "judgment": None}])
    with pytest.raises(ValueValidationError, match="judgment row 1"):
        compare_experiments(
            [first, second], judgments, GraphSettings(value_validation_root=tmp_path / "out")
        )


async def test_cleanup_rejects_unfinalized_comparison_before_qdrant_access(tmp_path):
    root = tmp_path / "experiments"
    root.mkdir()
    experiment = _experiment(root, "one", ["a"])
    comparison = tmp_path / "comparison"
    comparison.mkdir()
    _json(
        comparison / "manifest.json",
        {
            "artifact_class": "temporary_value_validation_comparison",
            "finalized": False,
            "experiment_ids": ["one"],
        },
    )
    with pytest.raises(ValueValidationError, match="finalized comparison"):
        await cleanup_experiment(experiment, comparison, GraphSettings())
