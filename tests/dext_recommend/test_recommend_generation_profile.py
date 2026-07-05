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
        "output_contract_instructions": ["test output contract"],
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
            "quick_actions": {
                "system_prompt_id": "dext_recommend.quick_actions.v1",
                "system_prompt": "quick actions prompt",
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["quick_actions"],
                    "properties": {},
                },
                "timeout": 8.0,
                "token_budget": 256,
            },
            "conversation_title": {
                "system_prompt_id": "dext_recommend.conversation_title.v1",
                "system_prompt": "conversation title prompt",
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title"],
                    "properties": {},
                },
                "timeout": 8.0,
                "token_budget": 128,
            },
            "achievement_extraction": {
                "system_prompt_id": "dext_recommend.achievement_extraction.v1",
                "system_prompt": "achievement extraction prompt",
                "json_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["competitions", "research"],
                    "properties": {},
                },
                "timeout": 8.0,
                "token_budget": 768,
            },
        },
    }


def _path_payload(tmp_path: Path) -> dict:
    payload = _valid_payload()
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    payload.pop("output_contract_instructions")
    payload["output_contract_prompt_path"] = "prompts/output_contract.md"
    (prompt_dir / "output_contract.md").write_text(
        "- contract one\n- contract two\n", encoding="utf-8"
    )
    for operation_id, cfg in payload["operations"].items():
        cfg.pop("system_prompt")
        cfg["system_prompt_path"] = f"prompts/{operation_id}.md"
        (prompt_dir / f"{operation_id}.md").write_text(
            f"{operation_id} markdown prompt\n", encoding="utf-8"
        )
    return payload


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
        "quick_actions", "conversation_title", "achievement_extraction",
    }
    impl = prof.operations["implicit_intent"]
    assert impl.confidence_threshold == 0.6
    assert impl.summary_max_chars == 500
    assert prof.output_contract_instructions == ("test output contract",)


def test_profile_loads_prompts_from_markdown_paths(tmp_path: Path) -> None:
    profile = RecommendGenerationProfile.from_dict(
        _path_payload(tmp_path), prompt_base_path=tmp_path,
    )

    assert profile.output_contract_instructions == ("contract one", "contract two")
    assert (
        profile.operations["implicit_intent"].system_prompt
        == "implicit_intent markdown prompt"
    )


def test_checked_in_profile_matches_grounded_manifest_and_is_deeply_immutable() -> None:
    profile = RecommendGenerationProfile.from_file(
        Path("data/recommend/generation-profile.json")
    )
    raw = json.loads(Path("data/recommend/generation-profile.json").read_text(encoding="utf-8"))
    assert "output_contract_prompt_path" in raw
    assert "output_contract_instructions" not in raw
    assert all("system_prompt" not in cfg for cfg in raw["operations"].values())
    assert all("system_prompt_path" in cfg for cfg in raw["operations"].values())
    assert profile.grounded_rules_manifest_hash == load_grounded_rules().manifest_hash
    assert profile.output_contract_instructions == (
        "Return exactly one JSON object and no markdown.",
        "The object must conform to output_contract.json_schema.",
        "Include every field listed in json_schema.required.",
        "Use only enum values declared in the schema.",
        "Do not include fields outside json_schema.properties.",
    )
    assert {
        "match_analysis", "outreach_email", "professor_comparison",
        "quick_actions", "conversation_title", "achievement_extraction",
    } <= set(profile.operations)
    assert profile.operations["query_understanding"].timeout >= 20.0
    assert profile.operations["match_analysis"].timeout == 30.0
    assert set(profile.operations["match_analysis"].json_schema["required"]) == {
        "summary", "dimension_scores", "next_steps", "claims",
    }
    assert set(profile.operations["outreach_email"].json_schema["required"]) == {
        "subject", "body", "claims",
    }
    assert set(profile.operations["professor_comparison"].json_schema["required"]) == {
        "summary", "professor_notes", "evidence_gaps", "claims",
    }
    assert set(profile.operations["quick_actions"].json_schema["required"]) == {
        "quick_actions",
    }
    assert set(profile.operations["conversation_title"].json_schema["required"]) == {
        "title",
    }
    assert set(profile.operations["achievement_extraction"].json_schema["required"]) == {
        "competitions", "research",
    }
    assert profile.operations["conversation_title"].json_schema["additionalProperties"] is False
    assert profile.operations["achievement_extraction"].json_schema["additionalProperties"] is False
    with pytest.raises(TypeError):
        profile.operations["implicit_intent"].json_schema["type"] = "array"


def test_loader_rejects_grounded_manifest_mismatch(tmp_path: Path) -> None:
    payload = _valid_payload()
    payload["grounded_rules_manifest_hash"] = "wrong"
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        RecommendGenerationProfile.from_file(path)


def test_loader_rejects_empty_prompt_file(tmp_path: Path) -> None:
    payload = _path_payload(tmp_path)
    (tmp_path / "prompts" / "implicit_intent.md").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        RecommendGenerationProfile.from_dict(payload, prompt_base_path=tmp_path)


def test_loader_rejects_missing_prompt_path(tmp_path: Path) -> None:
    payload = _path_payload(tmp_path)
    (tmp_path / "prompts" / "implicit_intent.md").unlink()

    with pytest.raises(ValueError, match="unavailable"):
        RecommendGenerationProfile.from_dict(payload, prompt_base_path=tmp_path)


def test_loader_rejects_inline_and_path_prompt_together(tmp_path: Path) -> None:
    payload = _path_payload(tmp_path)
    payload["operations"]["implicit_intent"]["system_prompt"] = "intent prompt"

    with pytest.raises(ValueError, match="mutually exclusive"):
        RecommendGenerationProfile.from_dict(payload, prompt_base_path=tmp_path)


def test_loader_rejects_inline_and_path_output_contract_together(tmp_path: Path) -> None:
    payload = _path_payload(tmp_path)
    payload["output_contract_instructions"] = ["inline contract"]

    with pytest.raises(ValueError, match="mutually exclusive"):
        RecommendGenerationProfile.from_dict(payload, prompt_base_path=tmp_path)


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
