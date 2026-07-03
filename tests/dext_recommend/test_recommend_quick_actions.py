from __future__ import annotations

import pytest

from dext_grounded import (
    ConstrainedGenerationPipeline,
    FakeLLMGenerationPort,
    GenerationResult,
    GenerationWarning,
)
from dext_recommend.generation.quick_actions import QuickActionGenerationService

from tests.dext_recommend._recfixtures import generation_profile


def _service(output: dict, *, warnings=None):
    llm = FakeLLMGenerationPort(
        preset=GenerationResult(output=output, warnings=list(warnings or ())),
    )
    service = QuickActionGenerationService(
        pipeline=ConstrainedGenerationPipeline(llm),
        generation_profile=generation_profile(),
    )
    return service, llm


@pytest.mark.asyncio
async def test_quick_actions_generate_uses_llm_context_and_dedupes():
    service, llm = _service({
        "quick_actions": ["换城市", "招生要求", "换城市", "只看985"],
    })
    recaps = [
        {
            "professor_id": f"p{i}",
            "name": f"老师{i}",
            "university": "测试大学",
            "research_fields": ["机器学习"],
        }
        for i in range(6)
    ]

    actions = await service.generate("只看上海的导师", recaps)

    assert actions == ["换城市", "招生要求", "只看985"]
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system_prompt_id"] == "dext_recommend.quick_actions.v1"
    assert call["user_inputs"]["follow_up"] == "只看上海的导师"
    assert len(call["user_inputs"]["last_recommendations"]) == 5
    assert call["user_inputs"]["label_constraints"]["max_items"] == 4
    assert call["fact_bundle"].subject_id == "quick_actions"


@pytest.mark.asyncio
async def test_quick_actions_filters_invalid_labels():
    service, _ = _service({
        "quick_actions": [
            "  换一批 ",
            "换一批",
            "",
            "是否联系",
            "你想比较吗",
            "这是什么？",
            "超长标签超过八个字",
            123,
            "偏应用",
        ],
    })

    actions = await service.generate("", [])

    assert actions == ["换一批", "偏应用"]


@pytest.mark.asyncio
async def test_quick_actions_warnings_or_bad_output_return_empty():
    warning = GenerationWarning(code="schema_validation_failed", message="bad schema")
    service, _ = _service({"quick_actions": ["换一批"]}, warnings=[warning])
    assert await service.generate("机器学习", []) == []

    service, _ = _service({"not_quick_actions": ["换一批"]})
    assert await service.generate("机器学习", []) == []
