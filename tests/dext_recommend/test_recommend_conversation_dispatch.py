from __future__ import annotations

from datetime import datetime

import pytest

from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult
from dext_recommend import ConversationContext, RecommendRequest
from dext_recommend.core.conversation import ConversationDispatcher
from dext_recommend.core.generation_profile import RecommendGenerationProfile
from dext_recommend.ports import (
    FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeRankingProfilePort, FakeRecommendGenerationProfilePort, FakeVectorSearchPort,
)
from dext_recommend.readiness import ActiveBuildSnapshot

from tests.dext_recommend._recfixtures import ranking_profile_dict
from dext_recommend.core.ranking_profile import RankingProfile


def _snapshot():
    return ActiveBuildSnapshot(
        build_id="b1", catalog_schema_version=1, neo4j_active_build_id="b1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="p", embedding_model="m", embedding_dimension=128,
        embedding_fingerprint="ef", taxonomy_version="t1",
        ranking_profile_version="rv",
        created_at=datetime.fromisoformat("2026-07-02T00:00:00+00:00"),
    )


def _profile():
    return RecommendGenerationProfile.from_dict({
        "version": "generation-v1", "grounded_rules_manifest_hash": "grh",
        "output_contract_instructions": ["test output contract"],
        "operations": {
            "query_understanding": {
                "system_prompt_id": "dext_recommend.query_understanding.v1",
                "system_prompt": "query prompt", "json_schema": {"type": "object"},
                "timeout": 8.0, "token_budget": 1024,
            },
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "system_prompt": "intent prompt",
                "json_schema": {"type": "object"}, "timeout": 8.0, "token_budget": 1024,
                "confidence_threshold": 0.6, "query_max_chars": 4096, "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "system_prompt": "detail prompt",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
            "match_analysis": {
                "system_prompt_id": "dext_recommend.match_analysis.v1",
                "system_prompt": "match prompt",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
            "outreach_email": {
                "system_prompt_id": "dext_recommend.outreach_email.v1",
                "system_prompt": "email prompt",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
            "professor_comparison": {
                "system_prompt_id": "dext_recommend.professor_comparison.v1",
                "system_prompt": "compare prompt",
                "json_schema": {"type": "object"}, "timeout": 20.0, "token_budget": 3072,
            },
            "quick_actions": {
                "system_prompt_id": "dext_recommend.quick_actions.v1",
                "system_prompt": "quick actions prompt",
                "json_schema": {"type": "object"}, "timeout": 8.0, "token_budget": 256,
            },
            "conversation_title": {
                "system_prompt_id": "dext_recommend.conversation_title.v1",
                "system_prompt": "conversation title prompt",
                "json_schema": {"type": "object"}, "timeout": 8.0, "token_budget": 128,
            },
            "achievement_extraction": {
                "system_prompt_id": "dext_recommend.achievement_extraction.v1",
                "system_prompt": "achievement extraction prompt",
                "json_schema": {"type": "object"}, "timeout": 8.0, "token_budget": 768,
            },
        },
    })


def _dispatcher(llm: FakeLLMGenerationPort, *, facts=None, details=None,
                snapshot_port=None, profile_port=None, conversation_store=None):
    from dext_recommend.core.service import RecommendDeps, RecommendationCore
    from dext_recommend.config import RecommendSettings
    snap = _snapshot()
    deps = RecommendDeps(
        snapshot_port=snapshot_port or FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], "ef"),
        vector_port=FakeVectorSearchPort(hits=[]),
        facts_port=FakeProfessorFactPort(facts=facts or {}, details=details or {}),
        llm_port=llm,
        ranking_port=FakeRankingProfilePort(
            profile=RankingProfile.from_dict(ranking_profile_dict()),
        ),
        generation_profile_port=profile_port or FakeRecommendGenerationProfilePort(_profile()),
        conversation_store=conversation_store,
    )
    core = RecommendationCore(deps, RecommendSettings())
    pipe = ConstrainedGenerationPipeline(llm_port=llm)
    return ConversationDispatcher(core=core, pipeline=pipe, settings=RecommendSettings())


@pytest.mark.asyncio
async def test_dispatch_implicit_high_confidence_more_mentors_routes_to_recommend():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "more_mentors", "confidence": 0.9, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="more?",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit",
            prior_result_entity_ids=("e1",)),
    )
    result = await d.dispatch(req)
    # more_mentors + prior present -> recommendation path (no survivors -> no_candidates warning, but kind=recommendation)
    assert result.kind == "recommendation"
    assert result.generation_profile_version == "generation-v1"


@pytest.mark.asyncio
async def test_dispatch_implicit_low_confidence_returns_clarification():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "more_mentors", "confidence": 0.2, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="hmm",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    result = await d.dispatch(req)
    assert result.kind == "clarification"
    assert any(w.code == "needs_clarification" for w in result.issues)


@pytest.mark.asyncio
async def test_dispatch_implicit_illegal_enum_returns_clarification():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "bogus", "confidence": 0.99, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="x",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    result = await d.dispatch(req)
    assert result.kind == "clarification"


@pytest.mark.asyncio
async def test_dispatch_explicit_detail_followup_without_anchor_is_error():
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"answer": "x", "claims": []}, claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    req = RecommendRequest(
        query_text="detail?",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="explicit",
            intent="detail_followup"),
    )
    result = await d.dispatch(req)
    assert result.kind == "error"
    assert any(w.code == "detail_followup_requires_anchor" for w in result.issues)


@pytest.mark.asyncio
async def test_dispatch_pins_snapshot_once():
    # The dispatcher reads snapshot/profile once; _recommend_pinned must not re-read.
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "new_search", "confidence": 0.9, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    d = _dispatcher(llm)
    snap_port = d._core.deps.snapshot_port
    req = RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit"),
    )
    await d.dispatch(req)
    assert snap_port.get_snapshot_calls == 1
    assert len(d.core.deps.generation_profile_port.read_profile_calls) == 1


@pytest.mark.asyncio
async def test_dispatch_loads_store_summary_and_request_context_wins():
    from dext_recommend import ConversationSummary, FakeConversationStorePort
    stored = ConversationContext(
        session_id="s1", turn_id="t1", anchor_entity_id="stored-anchor",
        intent_source="explicit", intent="new_search",
        prior_result_entity_ids=("stored-result",),
    )
    summary = ConversationSummary(
        session_id="s1", through_turn_id="t1", text="stored summary",
        created_at="2026-07-02T00:00:00Z",
    )
    store = FakeConversationStorePort(
        initial={("s1", "t1"): stored}, summaries={("s1", "t1"): summary},
    )
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={"intent": "more_mentors", "confidence": 0.9, "rationale": "r"},
        claims=(), cited_refs=(), warnings=[]))
    dispatcher = _dispatcher(llm, conversation_store=store)
    result = await dispatcher.dispatch(RecommendRequest(
        query_text="more",
        conversation_context=ConversationContext(
            session_id="s1", turn_id="t1", intent_source="implicit",
            anchor_entity_id="request-anchor",
        ),
    ))
    assert result.context.anchor_entity_id == "request-anchor"
    assert result.context.prior_result_entity_ids == ("stored-result",)
    assert llm.calls[0]["user_inputs"]["conversation_summary"] == "stored summary"
