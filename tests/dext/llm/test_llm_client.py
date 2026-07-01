import json
from types import SimpleNamespace

import pytest

from dext.llm.client import LLMClient, LLMResponse


def _settings(key="sk-x"):
    return SimpleNamespace(
        deepseek_api_key=key, llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash", llm_reasoning_effort="high",
        llm_reasoning_effort_retry="max", llm_max_page_tokens=24000,
    )


def test_build_kwargs_first_pass_uses_high_and_enables_thinking():
    c = LLMClient(_settings())
    kw = c._build_kwargs([{"role": "user", "content": "hi"}], tools=None,
                         tool_choice=None, response_format=None, thinking=True, retry_mode=False)
    assert kw["model"] == "deepseek-v4-flash"
    assert kw["reasoning_effort"] == "high"
    assert kw["extra_body"] == {"thinking": {"type": "enabled"}}


def test_build_kwargs_retry_escalates_to_max():
    c = LLMClient(_settings())
    kw = c._build_kwargs([], tools=None, tool_choice=None, response_format=None,
                         thinking=True, retry_mode=True)
    assert kw["reasoning_effort"] == "max"


def test_build_kwargs_thinking_disabled_omits_effort():
    c = LLMClient(_settings())
    kw = c._build_kwargs([], tools=None, tool_choice=None, response_format=None,
                         thinking=False, retry_mode=True)
    assert "reasoning_effort" not in kw
    assert kw["extra_body"] == {"thinking": {"type": "disabled"}}


def test_parse_splits_valid_and_invalid_tool_calls():
    msg = SimpleNamespace(
        content=None,
        reasoning_content="…",
        tool_calls=[
            SimpleNamespace(function=SimpleNamespace(name="save_professors", arguments='{"professors": []}')),
            SimpleNamespace(function=SimpleNamespace(name="save_professors", arguments='{bad json')),
        ],
    )
    resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
    out = LLMClient._parse(resp)
    assert isinstance(out, LLMResponse)
    assert out.tool_calls == [{"name": "save_professors", "arguments": {"professors": []}}]
    assert len(out.invalid_tool_calls) == 1
    assert out.invalid_tool_calls[0]["name"] == "save_professors"
    assert out.reasoning_content == "…"


async def test_missing_api_key_raises_runtimeerror():
    c = LLMClient(_settings(key=""))
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        await c.chat([{"role": "user", "content": "hi"}])
