from __future__ import annotations

import json
from pathlib import Path


def test_qa_generation_profile_fragment_shape():
    path = Path("data/competition/profiles/generation/qa-v1.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["version"] == "competition.qa.v1"
    assert payload["safety_domain"] == "competition"
    operations = {item["id"]: item for item in payload["operations"]}
    assert operations["competition_qa_answer"]["support_map_required"] is True
    assert operations["competition_compare"]["min_competitions"] == 2
    assert operations["competition_compare"]["max_competitions"] == 4


def test_qa_eval_sample_shape_has_twenty_items():
    path = Path("data/competition/evals/qa-v1.jsonl")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(rows) == 20
    assert len({row["id"] for row in rows}) == 20
    assert all(row["question"] for row in rows)
    assert any(row["freshness_sensitive"] is True for row in rows)
    assert all("source_refs" in row["expected_shape"] for row in rows)

