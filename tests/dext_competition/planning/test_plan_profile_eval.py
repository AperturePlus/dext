from __future__ import annotations

import json
from pathlib import Path

from dext_competition.planning import load_plan_generation_profile


def test_plan_generation_profile_is_closed_and_versioned() -> None:
    profile = load_plan_generation_profile()
    assert profile.version == "competition.plan.v1"
    assert profile.max_optional_tasks_per_phase == 3
    assert profile.json_schema["additionalProperties"] is False


def test_plan_eval_has_ten_required_scenarios() -> None:
    rows = [
        json.loads(line)
        for line in Path("data/competition/evals/plan-v1.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 10
    assert len({row["id"] for row in rows}) == 10
    assert {row["time_model"] for row in rows} == {"submission_deadline", "competition_window"}
    scenarios = " ".join(row["scenario"] for row in rows)
    assert all(term in scenarios for term in ("初学者", "高投入", "低投入", "考试", "团队", "provider"))
