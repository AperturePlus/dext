from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ConstrainedGenerationPipeline, ContentClass, FakeLLMGenerationPort,
    GenerationResult,
)
from dext_recommend import ConversationContext, RecommendRequest
from dext_recommend.core.generation_profile import RecommendGenerationProfile
from dext_recommend.ports import (
    FakeProfessorFactPort, FakeRecommendGenerationProfilePort, ProfessorFact,
)


def _profile_payload():
    return {
        "version": "generation-v1",
        "grounded_rules_manifest_hash": "grh",
        "operations": {
            "query_understanding": {
                "system_prompt_id": "dext_recommend.query_understanding.v1",
                "system_prompt": "query prompt", "json_schema": {"type": "object"},
                "timeout": 8.0, "token_budget": 1024,
            },
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "system_prompt": "intent prompt",
                "json_schema": {"type": "object"},
                "timeout": 8.0, "token_budget": 1024,
                "confidence_threshold": 0.6, "query_max_chars": 4096,
                "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "system_prompt": "detail prompt",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
        },
    }


def _profile():
    return RecommendGenerationProfile.from_dict(_profile_payload())


def _llm_classifying(intent: str, confidence: float) -> FakeLLMGenerationPort:
    raw = GenerationResult(
        output={"intent": intent, "confidence": confidence, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[],
    )
    return FakeLLMGenerationPort(preset=raw)


# Dispatcher construction helper lives in a shared conftest or local import;
# see test_recommend_conversation_dispatch.py for build_dispatcher.


@pytest.mark.asyncio
async def test_implicit_classifier_high_confidence_sets_intent():
    """The implicit classifier, when confidence >= threshold, sets the resolved
    intent on the context and routes to the recommend path."""
    from tests.dext_recommend.test_recommend_conversation_dispatch import _dispatcher
    llm = _llm_classifying("more_mentors", 0.9)
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="more?",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit",
            prior_result_entity_ids=("e1",)),
    )
    result = await d.dispatch(req)
    assert result.kind == "recommendation"
    assert result.context is not None
    assert result.context.intent == "more_mentors"
    assert result.context.intent_confidence == 0.9


@pytest.mark.asyncio
async def test_implicit_classifier_low_confidence_yields_clarification():
    """When confidence < threshold, dispatch returns clarification with
    needs_clarification code, carrying the generation_profile_version."""
    from tests.dext_recommend.test_recommend_conversation_dispatch import _dispatcher
    llm = _llm_classifying("more_mentors", 0.2)
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="hmm",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    result = await d.dispatch(req)
    assert result.kind == "clarification"
    assert any(w.code == "needs_clarification" for w in result.issues)
    assert result.generation_profile_version == "generation-v1"


@pytest.mark.asyncio
async def test_dispatch_without_context_is_treated_as_unresolved_implicit():
    from tests.dext_recommend.test_recommend_conversation_dispatch import _dispatcher
    dispatcher = _dispatcher(_llm_classifying("new_search", 0.9))
    result = await dispatcher.dispatch(RecommendRequest(query_text="find NLP mentors"))
    assert result.context is not None
    assert result.context.intent_source == "implicit"
    assert result.context.intent == "new_search"
