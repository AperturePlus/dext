from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dext_graph.catalog.topic_llm import TopicLLMClient
from dext_graph.config import GraphSettings
from dext_graph.models import ValueValidationError


class _Completions:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content))
            ]
        )


class _FailingCompletions:
    def __init__(self, exc: Exception):
        self.exc = exc

    async def create(self, **_kwargs):
        raise self.exc


def _client(content: str):
    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions(content)))


def test_topic_llm_requires_dedicated_api_key(monkeypatch):
    monkeypatch.delenv("DEXT_TOPIC_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "core-secret")

    with pytest.raises(ValueValidationError, match="DEXT_TOPIC_LLM_API_KEY"):
        TopicLLMClient(GraphSettings(_env_file=None))


@pytest.mark.asyncio
async def test_topic_llm_preflight_uses_configured_chat_completion():
    completions = _Completions('{"ok": true}')
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    settings = GraphSettings(
        _env_file=None,
        topic_llm_api_key="topic-secret",
        topic_llm_model="topic-model",
    )
    llm = TopicLLMClient(settings, client=client)

    await llm.preflight()

    assert completions.calls == [
        {
            "model": "topic-model",
            "messages": [
                {
                    "role": "system",
                    "content": "Return one JSON object for a Topic LLM health check.",
                },
                {"role": "user", "content": "Return {\"ok\": true}."},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 8,
            "stream": False,
        }
    ]


@pytest.mark.asyncio
async def test_topic_llm_preflight_adds_failure_context():
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_FailingCompletions(RuntimeError("Invalid token")))
    )
    llm = TopicLLMClient(
        GraphSettings(_env_file=None, topic_llm_api_key="topic-secret"),
        client=client,
    )

    with pytest.raises(ValueValidationError, match="Topic LLM preflight failed: Invalid token"):
        await llm.preflight()


@pytest.mark.asyncio
async def test_topic_llm_extract_with_diagnostics_returns_rejected_count():
    body = {
        "concepts": [
            {
                "evidence_span": "机器学习",
                "canonical_name": "机器学习",
                "kind": "method",
                "relation_type": "USES_METHOD",
            },
            {
                "evidence_span": "不存在",
                "canonical_name": "不存在",
                "kind": "method",
                "relation_type": "USES_METHOD",
            },
        ]
    }
    llm = TopicLLMClient(GraphSettings(_env_file=None), client=_client(json.dumps(body)))

    concepts, rejected = await llm.extract_with_diagnostics("使用机器学习")

    assert [concept.canonical_name for concept in concepts] == ["机器学习"]
    assert rejected == 1
    assert llm.last_rejected_count == 0


@pytest.mark.asyncio
async def test_topic_llm_extract_keeps_legacy_rejected_count():
    body = {
        "concepts": [
            {
                "evidence_span": "不存在",
                "canonical_name": "不存在",
                "kind": "method",
                "relation_type": "USES_METHOD",
            }
        ]
    }
    llm = TopicLLMClient(GraphSettings(_env_file=None), client=_client(json.dumps(body)))

    assert await llm.extract("使用机器学习") == []
    assert llm.last_rejected_count == 1
