from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from dext_grounded import FactBundle, StudentContext
from dext_recommend.adapters import OpenAICompatibleLLMGenerationAdapter
from tests.dext_recommend._recfixtures import generation_profile


class _Completions:
    def __init__(self, content):
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
        self.responses = list(response) if isinstance(response, (list, tuple)) else [response]
        self.calls = []

    async def create(self, **kwargs):
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append(kwargs)
        return self.responses[index]


def _raw_client(response):
    completions = _RawCompletions(response)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


class _OptionedClient:
    def __init__(self, content: str):
        self.options = []
        self.completions = _Completions(content)
        self.chat = SimpleNamespace(completions=_Completions("should-not-call"))

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(completions=self.completions))


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
    assert "extra_body" not in calls.calls[0]
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
async def test_adapter_can_disable_retries_per_request():
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    client = _OptionedClient('{"intent":"new_search","confidence":0.9,"rationale":"new query"}')
    adapter = OpenAICompatibleLLMGenerationAdapter(
        client=client,
        model="test-model",
        profile=profile,
        request_max_retries=0,
    )

    result = await adapter.generate(
        op.system_prompt_id,
        {"query_text": "NLP"},
        FactBundle("b1", "implicit-intent", (), ()),
        None,
        op.json_schema,
        profile.version,
    )

    assert result.output["intent"] == "new_search"
    assert client.options == [{"max_retries": 0}]
    assert len(client.completions.calls) == 1


@pytest.mark.asyncio
async def test_adapter_disables_deepseek_thinking_for_json_generation():
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    client, calls = _client(
        '{"intent":"new_search","confidence":0.9,"rationale":"new query"}'
    )
    adapter = OpenAICompatibleLLMGenerationAdapter(
        client=client, model="deepseek-v4-flash", profile=profile,
    )

    result = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )

    assert result.output["intent"] == "new_search"
    assert calls.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_adapter_from_settings_configures_deepseek_request_options():
    profile = generation_profile()
    deepseek = OpenAICompatibleLLMGenerationAdapter.from_settings(
        SimpleNamespace(
            llm_api_key=SecretStr("sk-test"),
            llm_base_url="https://api.deepseek.com",
            llm_max_retries=1,
            llm_model="custom-model",
        ),
        profile,
    )
    generic = OpenAICompatibleLLMGenerationAdapter.from_settings(
        SimpleNamespace(
            llm_api_key=SecretStr("sk-test"),
            llm_base_url="https://api.example.test/v1",
            llm_max_retries=1,
            llm_model="custom-model",
        ),
        profile,
    )

    try:
        assert deepseek._request_options == {"extra_body": {"thinking": {"type": "disabled"}}}
        assert generic._request_options == {}
    finally:
        await deepseek.aclose()
        await generic.aclose()


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
async def test_adapter_retries_missing_message_content_once():
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    responses = [
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"intent":"new_search","confidence":0.9,"rationale":"retry"}'
        ))]),
    ]
    client, calls = _raw_client(responses)
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)

    result = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )

    assert result.output["intent"] == "new_search"
    assert len(calls.calls) == 2


@pytest.mark.asyncio
async def test_adapter_parses_text_content_parts():
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=[
        {"type": "text", "text": '{"intent":"new_search",'},
        {"type": "text", "text": '"confidence":0.9,"rationale":"parts"}'},
    ]))])
    client, _ = _raw_client(response)
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)

    result = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )

    assert result.output["rationale"] == "parts"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    SimpleNamespace(choices=[]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace())]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
    SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=""))]),
])
async def test_adapter_returns_unavailable_warning_for_missing_message_content(response):
    profile = generation_profile()
    op = profile.operations["implicit_intent"]
    client, calls = _raw_client(response)
    adapter = OpenAICompatibleLLMGenerationAdapter(client=client, model="m", profile=profile)

    result = await adapter.generate(
        op.system_prompt_id, {}, FactBundle("b", "i", (), ()), None,
        dict(op.json_schema), profile.version,
    )

    assert result.output == {}
    assert result.warnings[0].code == "llm_unavailable"
    assert result.warnings[0].message == "provider response missing message content"
    assert len(calls.calls) == 2


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
