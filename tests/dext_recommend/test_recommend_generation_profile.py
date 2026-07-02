from __future__ import annotations

import json
from pathlib import Path

import pytest
from dext_grounded import load_grounded_rules

from dext_recommend.core.generation_profile import (
    OperationConfig, RecommendGenerationProfile,
)
from dext_recommend.ports import FakeRecommendGenerationProfilePort


def _valid_payload() -> dict:
    return {
        "version": "generation-v1",
        "grounded_rules_manifest_hash": load_grounded_rules().manifest_hash,
        "operations": {
            "query_understanding": {
                "system_prompt_id": "dext_recommend.query_understanding.v1",
                "system_prompt": "query prompt",
                "json_schema": {"type": "object", "required": ["confidence"]},
                "timeout": 8.0,
                "token_budget": 1024,
            },
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "system_prompt": "intent prompt",
                "json_schema": {"type": "object", "required": ["intent"]},
                "timeout": 8.0,
                "token_budget": 1024,
                "confidence_threshold": 0.6,
                "query_max_chars": 4096,
                "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "system_prompt": "detail prompt",
                "json_schema": {"type": "object", "required": ["answer"]},
                "timeout": 15.0,
                "token_budget": 2048,
            },
            "match_analysis": {
                "system_prompt_id": "dext_recommend.match_analysis.v1",
                "system_prompt": "match prompt",
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["summary", "dimension_scores", "next_steps", "claims"],
                    "properties": {},
                },
                "timeout": 15.0,
                "token_budget": 2048,
            },
            "outreach_email": {
                "system_prompt_id": "dext_recommend.outreach_email.v1",
                "system_prompt": "email prompt",
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["subject", "body", "claims"],
                    "properties": {},
                },
                "timeout": 15.0,
                "token_budget": 2048,
            },
            "professor_comparison": {
                "system_prompt_id": "dext_recommend.professor_comparison.v1",
                "system_prompt": "compare prompt",
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["summary", "professor_notes", "evidence_gaps", "claims"],
                    "properties": {},
                },
                "timeout": 20.0,
                "token_budget": 3072,
            },
        },
    }


def test_profile_round_trips_through_json(tmp_path: Path) -> None:
    payload = _valid_payload()
    p = tmp_path / "gp.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    prof = RecommendGenerationProfile.from_dict(json.loads(p.read_text(encoding="utf-8")))
    assert prof.version == "generation-v1"
    assert prof.grounded_rules_manifest_hash == load_grounded_rules().manifest_hash
    assert set(prof.operations) == {
        "query_understanding", "implicit_intent", "detail_followup",
        "match_analysis", "outreach_email", "professor_comparison",
    }
    impl = prof.operations["implicit_intent"]
    assert impl.confidence_threshold == 0.6
    assert impl.summary_max_chars == 500


def test_checked_in_profile_matches_grounded_manifest_and_is_deeply_immutable() -> None:
    profile = RecommendGenerationProfile.from_file(
        Path("data/recommend/generation-profile.json")
    )
    assert profile.grounded_rules_manifest_hash == load_grounded_rules().manifest_hash
    assert {"match_analysis", "outreach_email", "professor_comparison"} <= set(profile.operations)
    assert set(profile.operations["match_analysis"].json_schema["required"]) == {
        "summary", "dimension_scores", "next_steps", "claims",
    }
    assert set(profile.operations["outreach_email"].json_schema["required"]) == {
        "subject", "body", "claims",
    }
    assert set(profile.operations["professor_comparison"].json_schema["required"]) == {
        "summary", "professor_notes", "evidence_gaps", "claims",
    }
    with pytest.raises(TypeError):
        profile.operations["implicit_intent"].json_schema["type"] = "array"


def test_loader_rejects_grounded_manifest_mismatch(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["grounded_rules_manifest_hash"] = "wrong"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        RecommendGenerationProfile.from_file(path)


@pytest.mark.parametrize("bad,path", [
    ({"operations": {"implicit_intent": {"system_prompt_id": ""}}}, "empty prompt id"),
    ({"operations": {"implicit_intent": {"timeout": -1.0}}}, "negative timeout"),
    ({"operations": {"implicit_intent": {"token_budget": 0}}}, "non-positive budget"),
    ({"operations": {"implicit_intent": {"confidence_threshold": 1.5}}}, "threshold out of range"),
    ({"operations": {}}, "missing operations"),
    ({}, "missing operations key"),
])
def test_loader_rejects_invalid(tmp_path: Path, bad: dict, path: str) -> None:
    payload = _valid_payload()
    payload.update(bad) if "operations" in bad else payload.pop("operations", None)
    if "operations" in bad:
        # deep-merge the bad operation overrides
        payload["operations"].update(bad["operations"])
    p = tmp_path / "gp.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises((ValueError, TypeError)):
        RecommendGenerationProfile.from_file(p)


@pytest.mark.asyncio
async def test_fake_profile_port_returns_preset() -> None:
    prof = RecommendGenerationProfile.from_dict(_valid_payload())
    port = FakeRecommendGenerationProfilePort(profile=prof)
    got = await port.read_profile(Path("ignored"))
    assert got is prof
