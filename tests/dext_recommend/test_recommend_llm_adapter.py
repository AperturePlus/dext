from __future__ import annotations

from types import SimpleNamespace

import pytest

from dext_grounded import FactBundle
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
        dict(op.json_schema), profile.version,
    )
    assert result.output["intent"] == "new_search"
    assert calls.calls[0]["max_tokens"] == op.token_budget
    assert calls.calls[0]["timeout"] == op.timeout


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
