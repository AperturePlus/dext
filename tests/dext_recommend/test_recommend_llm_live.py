"""Opt-in real-provider smoke tests; excluded from normal module regression."""
from __future__ import annotations

import os

import pytest

from dext_grounded import ContentClass, FactBundle, FactItem, SourceRef
from dext_recommend import OpenAICompatibleLLMGenerationAdapter, RecommendSettings
from tests.dext_recommend._recfixtures import generation_profile


pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.getenv("DEXT_RECOMMEND_RUN_LIVE_LLM") == "1"


@pytest.mark.asyncio
@pytest.mark.skipif(not _enabled(), reason="set DEXT_RECOMMEND_RUN_LIVE_LLM=1")
async def test_live_implicit_and_detail_json_smoke():
    settings = RecommendSettings()
    profile = generation_profile()
    adapter = OpenAICompatibleLLMGenerationAdapter.from_settings(settings, profile)
    try:
        implicit = profile.operations["implicit_intent"]
        intent = await adapter.generate(
            implicit.system_prompt_id,
            {"query_text": "再推荐几位导师", "conversation_summary": "已有推荐结果",
             "has_anchor": False, "has_prior_results": True},
            FactBundle("live-smoke", "implicit-intent", (), ()), None,
            dict(implicit.json_schema), profile.version,
        )
        assert not intent.warnings
        assert intent.output["intent"] in {
            "new_search", "more_mentors", "same_field", "refine_direction", "detail_followup"
        }

        ref = SourceRef(
            doc_path="smoke:professor:e1", heading_path="research",
            chunk_hash="smoke-hash", quote_or_summary="研究自然语言处理。",
        )
        bundle = FactBundle(
            "live-smoke", "e1",
            (FactItem("research_statement", "研究自然语言处理。",
                      ContentClass.FACT, (ref,)),), (ref,),
        )
        detail = profile.operations["detail_followup"]
        answer = await adapter.generate(
            detail.system_prompt_id,
            {"question": "这位老师研究什么？", "display_name": "示例导师"},
            bundle, None, dict(detail.json_schema), profile.version,
        )
        assert not answer.warnings
        assert isinstance(answer.output.get("answer"), str)
    finally:
        await adapter.aclose()
