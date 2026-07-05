from __future__ import annotations

import pytest

from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult
from dext_recommend.generation.achievement_extraction import AchievementExtractionService

from tests.dext_recommend._recfixtures import generation_profile


@pytest.mark.asyncio
async def test_achievement_extraction_uses_llm_and_normalizes_contract():
    llm = FakeLLMGenerationPort(
        preset=GenerationResult(
            output={
                "competitions": [
                    {"name": "ACM区域赛", "award": "银牌"},
                ],
                "research": [
                    {"type": "paper", "title": "科研经历"},
                    {"type": "paper", "title": "CCF-B论文"},
                ],
            },
        ),
    )
    service = AchievementExtractionService(
        pipeline=ConstrainedGenerationPipeline(llm),
        generation_profile=generation_profile(),
    )

    result = await service.extract("ACM区域赛银牌；一篇CCF-B论文")

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["system_prompt_id"] == "dext_recommend.achievement_extraction.v1"
    assert call["fact_bundle"].subject_id == "achievement_extraction"
    assert call["user_inputs"]["raw_text"] == "ACM区域赛银牌；一篇CCF-B论文"

    assert result["competitions"][0] == {
        "name": "ACM区域赛",
        "level": "",
        "award": "银牌",
        "year": "",
    }
    assert {
        "type": "paper",
        "title": "CCF-B论文",
        "role": "",
        "venue_or_status": "",
        "year": "",
    } in result["research"]
    assert all(item["title"] != "科研经历" for item in result["research"])
