from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from dext_graph.catalog.topic_llm import TopicLLMClient
from dext_graph.config import GraphSettings


class _Completions:
    def __init__(self, content: str):
        self.content = content

    async def create(self, **_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.content))
            ]
        )


def _client(content: str):
    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions(content)))


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
