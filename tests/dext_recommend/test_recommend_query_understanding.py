from __future__ import annotations

import pytest

from dext_grounded import GenerationResult

from dext_recommend import QueryUnderstanding
from dext_recommend.core.query_understanding import understand_query
from dext_recommend.models import RecommendRequest

from tests.dext_recommend._recfixtures import fake_llm_for_understanding, snapshot


def _output(**over) -> dict:
    base = {
        "research_interests": ["NLP", "机器学习"],
        "preferred_universities": ["清华"],
        "preferred_cities": ["北京"],
        "preferred_org_units": [],
        "degree_goal": "master",
        "mentor_eligibility_requirement": "confirmed",
        "missing_information": [],
        "needs_clarification": False,
        "confidence": 0.8,
    }
    base.update(over)
    return base


async def test_understand_query_maps_llm_output_to_query_understanding():
    llm = fake_llm_for_understanding(_output())
    qu = await understand_query(
        RecommendRequest(query_text="NLP 导师"), llm, snapshot(), profile_version="r1",
    )
    assert isinstance(qu, QueryUnderstanding)
    assert qu.research_interests == ("NLP", "机器学习")
    assert qu.preferred_universities == ("清华",)
    assert qu.preferred_cities == ("北京",)
    assert qu.degree_goal == "master"
    assert qu.needs_clarification is False
    assert qu.confidence == 0.8
    # LLM was called once with the query_understanding system prompt id
    assert len(llm.calls) == 1
    assert llm.calls[0]["system_prompt_id"] == "query_understanding_v1"
    assert llm.calls[0]["json_schema"] is not None


async def test_understand_query_needs_clarification_passes_through():
    llm = fake_llm_for_understanding(_output(needs_clarification=True, confidence=0.2))
    qu = await understand_query(
        RecommendRequest(query_text="随便"), llm, snapshot(), profile_version="r1",
    )
    assert qu.needs_clarification is True
    assert qu.confidence == 0.2


async def test_understand_query_parse_failure_falls_back_to_needs_clarification():
    llm = fake_llm_for_understanding({"not": "the expected schema"})
    qu = await understand_query(
        RecommendRequest(query_text="NLP 导师"), llm, snapshot(), profile_version="r1",
    )
    assert qu.needs_clarification is True
    assert qu.confidence == 0.0
    assert qu.research_interests == ("NLP", "导师")  # query split


async def test_understand_query_generation_warning_blocks_fallback():
    from dext_grounded import GenerationWarning
    preset = GenerationResult(output=_output(), warnings=[
        GenerationWarning(code="generation_unavailable", message="down"),
    ])
    from dext_grounded import FakeLLMGenerationPort
    llm = FakeLLMGenerationPort(preset=preset)
    qu = await understand_query(
        RecommendRequest(query_text="NLP 导师"), llm, snapshot(), profile_version="r1",
    )
    assert qu.needs_clarification is True


async def test_understand_query_fact_bundle_uses_snapshot_build_id():
    llm = fake_llm_for_understanding(_output())
    snap = snapshot(build_id="b-77")
    await understand_query(
        RecommendRequest(query_text="NLP"), llm, snap, profile_version="r1",
    )
    bundle = llm.calls[0]["fact_bundle"]
    assert bundle.build_id == "b-77"
    assert bundle.subject_id == "query_understanding"
    assert bundle.facts == ()
