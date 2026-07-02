from __future__ import annotations

import json
from pathlib import Path

import pytest

from dext_recommend.core.generation_profile import (
    OperationConfig, RecommendGenerationProfile,
)
from dext_recommend.ports import FakeRecommendGenerationProfilePort


def _valid_payload() -> dict:
    return {
        "version": "generation-v1",
        "grounded_rules_manifest_hash": "grules-abc123",
        "operations": {
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "json_schema": {"type": "object", "required": ["intent"]},
                "timeout": 8.0,
                "token_budget": 1024,
                "confidence_threshold": 0.6,
                "query_max_chars": 4096,
                "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "json_schema": {"type": "object", "required": ["answer"]},
                "timeout": 15.0,
                "token_budget": 2048,
            },
        },
    }


def test_profile_round_trips_through_json(tmp_path: Path) -> None:
    payload = _valid_payload()
    p = tmp_path / "gp.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    prof = RecommendGenerationProfile.from_file(p)
    assert prof.version == "generation-v1"
    assert prof.grounded_rules_manifest_hash == "grules-abc123"
    assert set(prof.operations) == {"implicit_intent", "detail_followup"}
    impl = prof.operations["implicit_intent"]
    assert impl.confidence_threshold == 0.6
    assert impl.summary_max_chars == 500


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
