# tests/test_recommend_models.py  (snapshot portion — appended in this task)
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from dext_recommend import ActiveBuildSnapshot, ReadinessService


def _make_snapshot(**overrides):
    base = dict(
        build_id="b-1",
        catalog_schema_version=6,
        neo4j_active_build_id="b-1",
        qdrant_alias_target="dext_professors_current__phys-1",
        qdrant_payload_schema_version=2,
        embedding_provider="siliconflow",
        embedding_model="BAAI/bge-m3",
        embedding_dimension=1024,
        embedding_fingerprint="fp-xyz",
        taxonomy_version="tax-v1",
        ranking_profile_version="rank-v1",
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return ActiveBuildSnapshot(**base)


def test_active_build_snapshot_fields():
    snap = _make_snapshot()
    assert snap.build_id == "b-1"
    assert snap.embedding_dimension == 1024


def test_active_build_snapshot_is_frozen():
    snap = _make_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.build_id = "other"


async def test_readiness_service_requires_deps_and_settings():
    # ReadinessService is now a real two-phase orchestrator (R2); it requires
    # ReadinessDeps + RecommendSettings. Detailed behavior is covered by
    # tests/dext_recommend/test_recommend_readiness_service.py.
    with pytest.raises(TypeError):
        ReadinessService()  # type: ignore[call-arg]


def test_coverage_stat_accepts_valid_range():
    from dext_recommend import CoverageStat
    stat = CoverageStat(field="org_unit_ids", covered=0.0, sample_size=0, passes=False)
    assert stat.covered == 0.0
    stat_full = CoverageStat(field="profile_hash", covered=1.0, sample_size=100, passes=True)
    assert stat_full.covered == 1.0


def test_coverage_stat_rejects_covered_out_of_range():
    from dext_recommend import CoverageStat
    with pytest.raises(ValueError):
        CoverageStat(field="org_unit_ids", covered=1.05, sample_size=10, passes=True)
    with pytest.raises(ValueError):
        CoverageStat(field="org_unit_ids", covered=-0.01, sample_size=10, passes=True)


def test_coverage_stat_rejects_negative_sample_size():
    from dext_recommend import CoverageStat
    with pytest.raises(ValueError):
        CoverageStat(field="org_unit_ids", covered=0.5, sample_size=-1, passes=True)


# appended to tests/test_recommend_models.py
from dext_recommend import (
    ConversationContext, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendationFilters, RecommendResponse, RecommendedProfessor,
)
from dext_grounded import StudentContext


def test_recommendation_filters_defaults():
    f = RecommendationFilters()
    assert not f.university_ids
    assert f.master_eligibility == "any"
    assert f.phd_eligibility == "any"
    assert f.topic_filter_mode == "soft"


def test_recommend_request_defaults():
    req = RecommendRequest(query_text="NLP 导师")
    assert req.limit == 10
    assert req.oversample == 200
    assert req.ranking_mode == "explainable_precision"
    assert req.review_policy == "exclude"
    assert req.include_contacts is False
    assert req.diagnostics_level == "summary"
    assert req.student_context is None
    assert req.conversation_context is None


def test_recommend_request_reexports_student_context():
    # StudentContext comes from dext_grounded, not redefined here
    req = RecommendRequest(
        query_text="x",
        student_context=StudentContext(school="X"),
    )
    assert req.student_context.school == "X"


def test_conversation_context_fields():
    ctx = ConversationContext(
        session_id="s1", turn_id="t1",
        intent="more_mentors", intent_source="explicit",
        prior_result_entity_ids=["e1", "e2"],
    )
    assert ctx.anchor_entity_id is None
    assert ctx.intent_confidence is None


def test_query_understanding_fields():
    qu = QueryUnderstanding(
        research_interests=["NLP"],
        preferred_universities=["A"],
        preferred_cities=["北京"],
        preferred_org_units=[],
        degree_goal="phd",
        mentor_eligibility_requirement="phd_confirmed",
        missing_information=["gpa"],
        needs_clarification=False,
        confidence=0.8,
    )
    assert qu.research_interests == ("NLP",)
    assert qu.confidence == 0.8


def test_recommended_professor_minimum():
    p = RecommendedProfessor(
        entity_id="e1", display_name="Prof", university="U",
        org_units=[], title="Professor", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="unknown",
        role_status="included", profile_url="http://x",
        research_summary="RAG", match_level="strong",
        short_reasons=["works on RAG"], score=0.9, score_components={},
        matched_topics=[], matched_statements=[], matched_publications=[],
        evidence_refs=[], risk_flags=[], available_actions=["detail"],
    )
    assert p.entity_id == "e1"


# appended to tests/test_recommend_models.py
from dext_recommend import RecommendationCore, RecommendDeps
from dext_recommend import (
    FakeActiveSnapshotProvider, FakeLLMGenerationPort, FakeProfessorFactPort,
    FakeQueryEmbeddingPort, FakeRankingProfilePort, FakeVectorSearchPort,
    RecommendSettings,
)
from dext_recommend.core.ranking_profile import RankingProfile


def _profile():
    return RankingProfile.from_dict({
        "version": "rank-v1",
        "weights": {
            "semantic_score": 0.50, "topic_statement_score": 0.18,
            "student_fit_score": 0.12, "eligibility_score": 0.08,
            "provenance_score": 0.08, "completeness_score": 0.04,
        },
        "rrf_k": 60, "oversample_steps": (200, 400, 800, 1000),
        "detail_rerank_window": 50, "detail_fetch_concurrency": 8,
        "detail_rerank_window_max": 100,
        "match_level_thresholds": {"excellent": 0.75, "strong": 0.55, "possible": 0.35},
        "tie_break": ("score", "semantic_score", "evidence_count", "entity_id"),
        "same_field_boost_per_topic": 0.05,
        "same_field_boost_max": 0.15,
    })


def test_recommendation_core_constructs_from_fake_ports():
    from dext_grounded import FakeLLMGenerationPort, GenerationResult
    snap = _make_snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
        llm_port=FakeLLMGenerationPort(preset=GenerationResult(output={})),
        ranking_port=FakeRankingProfilePort(profile=_profile()),
        coverage_flags_by_build_id={snap.build_id: {"org_unit_ids": True}},
    )
    core = RecommendationCore(deps, RecommendSettings())
    assert core is not None
    assert core.deps is deps


async def test_recommendation_core_recommend_runs_pipeline():
    # The placeholder NotImplementedError is gone in R3; the real pipeline
    # runs end-to-end against fakes and returns a validated RecommendResponse.
    from dext_grounded import FakeLLMGenerationPort, GenerationResult
    snap = _make_snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
        llm_port=FakeLLMGenerationPort(preset=GenerationResult(output={})),
        ranking_port=FakeRankingProfilePort(profile=_profile()),
        coverage_flags_by_build_id={snap.build_id: {"org_unit_ids": True}},
    )
    core = RecommendationCore(deps, RecommendSettings())
    resp = await core.recommend(RecommendRequest(query_text="x"))
    from dext_recommend import RecommendResponse
    assert isinstance(resp, RecommendResponse)
    assert resp.build_id == "b-1"


def test_query_diagnostics_steps_used_defaults_zero():
    from dext_recommend.models import QueryDiagnostics
    d = QueryDiagnostics(query_length=5, language_summary="en", filter_summary="none")
    assert d.steps_used == 0


def test_query_diagnostics_steps_used_set():
    from dext_recommend.models import QueryDiagnostics
    d = QueryDiagnostics(query_length=5, language_summary="en",
                        filter_summary="none", steps_used=2)
    assert d.steps_used == 2
