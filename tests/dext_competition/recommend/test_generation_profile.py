from __future__ import annotations

import json

import pytest

from dext_competition.recommend.generation_profile import load_query_understanding_profile


def test_query_understanding_profile_loads_checked_in_operation() -> None:
    profile = load_query_understanding_profile()
    assert profile.version == "competition.query-understanding.v1"
    assert profile.operation_id == "competition_query_understanding"
    assert profile.system_prompt_id == "competition_query_understanding_v1"
    assert profile.safety_domain == "competition"
    assert profile.timeout_seconds == 8.0
    assert profile.json_schema["additionalProperties"] is False


def test_query_understanding_profile_rejects_open_schema(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "version": "bad",
        "safety_domain": "competition",
        "operation": {
            "id": "competition_query_understanding",
            "system_prompt_id": "prompt",
            "timeout_seconds": 1,
            "json_schema": {"type": "object"},
        },
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="additional properties"):
        load_query_understanding_profile(path)
