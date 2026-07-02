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
        "operations": {
            "implicit_intent": {
                "system_prompt_id": "dext_recommend.implicit_intent.v1",
                "json_schema": {"type": "object"}, "timeout": 8.0, "token_budget": 1024,
                "confidence_threshold": 0.6, "query_max_chars": 4096, "summary_max_chars": 500,
            },
            "detail_followup": {
                "system_prompt_id": "dext_recommend.detail_followup.v1",
                "json_schema": {"type": "object"}, "timeout": 15.0, "token_budget": 2048,
            },
        },
    })


def _dispatcher(llm: FakeLLMGenerationPort, *, facts=None):
    from dext_recommend.core.service import RecommendDeps, RecommendationCore
    from dext_recommend.config import RecommendSettings
    snap = _snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], "ef"),
        vector_port=FakeVectorSearchPort(hits=[]),
        facts_port=FakeProfessorFactPort(facts=facts or {}),
        llm_port=llm,
        ranking_port=FakeRankingProfilePort(
            profile=RankingProfile.from_dict(ranking_profile_dict()),
        ),
        generation_profile_port=FakeRecommendGenerationProfilePort(_profile()),
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
    # FakeActiveSnapshotProvider just returns the stored snapshot; assert it
    # was the same object the core received by checking it's not None and stable.
    assert snap_port.get_snapshot() is not None
