from __future__ import annotations

import json
from pathlib import Path

import pytest

from dext_competition.eval import RecommendEvalSample, validate_recommend_eval_sample


def test_recommend_eval_sample_shape_accepts_minimum_expected_category():
    sample = RecommendEvalSample(
        sample_id="rec-computer-001",
        query_text="我想找适合计算机新手的算法竞赛",
        expected_categories=["计算机"],
    )
    assert sample.expected_categories == ("计算机",)
    validate_recommend_eval_sample(sample)


def test_recommend_eval_sample_requires_expectation():
    with pytest.raises(ValueError):
        validate_recommend_eval_sample(
            RecommendEvalSample(sample_id="bad", query_text="数学建模")
        )


def test_checked_in_recommend_eval_has_forty_diverse_samples():
    path = Path("data/competition/evals/recommend-v1.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 40
    assert len({row["sample_id"] for row in rows}) == 40
    categories = {category for row in rows for category in row.get("expected_categories", [])}
    assert categories >= {
        "计算机", "电子信息", "数学建模", "机器人", "工学",
        "经管", "综合与创业", "语言艺术", "医学生命科学",
    }
    outcomes = {row["expected_outcome"] for row in rows}
    assert outcomes >= {"recommendations", "clarification", "refusal", "no_candidates"}
    tags = {tag for row in rows for tag in row.get("tags", [])}
    assert tags >= {"low_information", "high_risk", "structured_filter"}
