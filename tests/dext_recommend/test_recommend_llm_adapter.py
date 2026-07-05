from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dext_grounded import FactBundle, StudentContext
from dext_recommend.adapters import OpenAICompatibleLLMGenerationAdapter
from tests.dext_recommend._recfixtures import generation_profile


class _Completions:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self.content)
        )])


def _client(content: str):
    completions = _Completions(content)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


class _RawCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _raw_client(response):
    completions = _RawCompletions(response)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


@pytest.mark.asyncio
async def test_adapter_uses_profile_prompt_budget_and_strict_json():
    profile = generation_profile()
    client, calls = _client(
        '{"intent":"new_search","confidence":0.9,"rationale":"new query"}'
    )
    adapter = OpenAICompatibleLLMGenerationAdapter(
        client=client, model="test-model", profile=profile,
    )
    op = profile.operations["implicit_intent"]
    result = await adapter.generate(
        op.system_prompt_id, {"query_text": "NLP"},
        FactBundle("b1", "implicit-intent", (), ()), None,
        op.json_schema, profile.version,
    )
    assert result.output["intent"] == "new_search"
    assert calls.calls[0]["max_tokens"] == op.token_budget
    assert calls.calls[0]["timeout"] == op.timeout
    assert calls.calls[0]["response_format"] == {"type": "json_object"}
    assert calls.calls[0]["messages"][0]["content"] == Path(
        "data/recommend/prompts/implicit_intent.md"
    ).read_text(encoding="utf-8").strip()
    payload = json.loads(calls.calls[0]["messages"][1]["content"])
    contract = payload["output_contract"]
    assert contract["instructions"] == list(profile.output_contract_instructions)
    assert contract["json_schema"]["required"] == ["intent", "confidence", "rationale"]
    assert contract["json_schema"]["properties"]["intent"]["enum"] == [
        "new_search", "more_mentors", "same_field", "refine_direction", "detail_followup"
    ]
    assert "Include every field listed in json_schema.required." in contract["instructions"]


@pytest.mark.asyncio
async def test_adapter_returns_stable_parse_and_schema_warnings():
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    client, _ = _client("not-json")
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)
    parsed = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )
    assert parsed.warnings[0].code == "generation_parse_error"

    client, _ = _client('{"intent":"bogus"}')
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)
    invalid = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )
    assert invalid.warnings[0].code == "schema_validation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    SimpleNamespace(choices=[]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))]),
])
async def test_adapter_returns_parse_warning_for_missing_message_content(response):
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    client, _ = _raw_client(response)
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)

    result = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )

    assert result.output == {}
    assert result.warnings[0].code == "generation_parse_error"
    assert result.warnings[0].message == "provider response missing message content"


@pytest.mark.asyncio
async def test_adapter_parses_user_context_refs_on_claims():
    profile = generation_profile()
    op = profile.operations["detail_followup"]
    client, _ = _client(json.dumps({
        "answer": "You can mention your NLP background.",
        "claims": [{
            "text": "You can mention your NLP background.",
            "content_class": "advice",
            "fact_indices": [],
            "fact_refs": [],
            "user_context_ref": {
                "field": "research_interests",
                "value_bucket": None,
                "quote_or_summary": "NLP",
            },
        }],
    }))
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)
    result = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "e1", (), ()),
        StudentContext(research_interests=["NLP"]), dict(op.json_schema), profile.version,
    )
    assert len(result.claims) == 1
    assert result.claims[0].user_context_ref is not None
    assert result.claims[0].user_context_ref.field == "research_interests"
