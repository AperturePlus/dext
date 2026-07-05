from __future__ import annotations

import pytest

from dext_grounded import (
    ConstrainedGenerationPipeline,
    FakeLLMGenerationPort,
    GenerationResult,
    GenerationWarning,
)
from dext_recommend.generation.conversation_title import (
    MAX_TITLE_CHARS,
    ConversationTitleGenerationService,
)

from tests.dext_recommend._recfixtures import generation_profile


def _service(output: dict, *, warnings=None):
    llm = FakeLLMGenerationPort(
        preset=GenerationResult(output=output, warnings=list(warnings or ())),
    )
    service = ConversationTitleGenerationService(
        pipeline=ConstrainedGenerationPipeline(llm),
        generation_profile=generation_profile(),
    )
    return service, llm


class _RaisingLLMPort:
    def __init__(self):
        self.calls = []

    async def generate(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        raise RuntimeError("llm unavailable")


@pytest.mark.asyncio
async def test_conversation_title_uses_llm_output_and_context():
    service, llm = _service({"title": "  机器学习导师\n推荐  "})

    title = await service.generate("推荐机器学习导师", "这里是推荐结果")

    assert title == "机器学习导师 推荐"
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system_prompt_id"] == "dext_recommend.conversation_title.v1"
    assert call["user_inputs"]["first_user_message"] == "推荐机器学习导师"
    assert call["user_inputs"]["assistant_answer"] == "这里是推荐结果"
    assert call["user_inputs"]["title_constraints"]["max_chars"] == MAX_TITLE_CHARS
    assert call["fact_bundle"].subject_id == "conversation_title"


@pytest.mark.asyncio
async def test_conversation_title_warnings_or_bad_output_use_message_fallback():
    warning = GenerationWarning(code="schema_validation_failed", message="bad schema")
    service, _ = _service({"title": "机器学习导师"}, warnings=[warning])
    assert await service.generate("  推荐\n机器学习导师  ", "") == "推荐 机器学习导师"

    service, _ = _service({"title": ""})
    assert await service.generate("推荐机器学习导师", "") == "推荐机器学习导师"

    service, _ = _service({"not_title": "机器学习导师"})
    assert await service.generate("推荐机器学习导师", "") == "推荐机器学习导师"


@pytest.mark.asyncio
async def test_conversation_title_exception_and_long_message_use_truncated_fallback():
    service = ConversationTitleGenerationService(
        pipeline=ConstrainedGenerationPipeline(_RaisingLLMPort()),
        generation_profile=generation_profile(),
    )
    message = "很长的研究方向" * 10

    title = await service.generate(message, "")

    assert title == message[:MAX_TITLE_CHARS]
    assert len(title) == MAX_TITLE_CHARS


@pytest.mark.asyncio
async def test_conversation_title_empty_fallback_uses_default_label():
    service, _ = _service({"title": ""})

    assert await service.generate("  \n\t  ", "") == "新会话"
