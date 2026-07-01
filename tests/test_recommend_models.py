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


def test_readiness_service_check_placeholder():
    svc = ReadinessService()
    with pytest.raises(NotImplementedError):
        svc.check()


# appended to tests/test_recommend_models.py
from dext_recommend import (
    ConversationContext, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendationFilters, RecommendResponse, RecommendedProfessor,
)
from dext_grounded import StudentContext


def test_recommendation_filters_defaults():
    f = RecommendationFilters()
    assert f.university_ids == []
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
    assert qu.research_interests == ["NLP"]
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
    FakeBuildSnapshotPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort,
)


def test_recommendation_core_constructs_from_fake_ports():
    snap = _make_snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeBuildSnapshotPort(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
    )
    core = RecommendationCore(deps)
    assert core is not None


def test_recommendation_core_recommend_placeholder():
    import pytest
    snap = _make_snapshot()
    deps = RecommendDeps(
        snapshot_port=FakeBuildSnapshotPort(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
    )
    core = RecommendationCore(deps)
    with pytest.raises(NotImplementedError):
        core.recommend(RecommendRequest(query_text="x"))
