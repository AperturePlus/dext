# tests/test_recommend_foundations_acceptance.py
"""Foundations §9 acceptance: package, models, errors, ports, fakes, boundary."""
from __future__ import annotations

import dataclasses

import pytest

import dext_recommend as rec
from dext_recommend import (
    ActiveBuildSnapshot, BuildSnapshotPort, ConversationContext, ErrorSeverity,
    FakeBuildSnapshotPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort, ProfessorFactPort, QueryEmbeddingPort,
    QueryUnderstanding, RecommendDeps, RecommendRequest, RecommendedProfessor,
    RecommendationCore, RecommendationErrorCode, RecommendationFilters,
    VectorSearchPort, ViewerPermissions,
)
import dext_grounded as grounded


def _snap():
    from datetime import datetime, timezone
    return ActiveBuildSnapshot(
        build_id="b-1", catalog_schema_version=6, neo4j_active_build_id="b-1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="sf", embedding_model="bge-m3", embedding_dimension=1024,
        embedding_fingerprint="fp-x", taxonomy_version="t1",
        ranking_profile_version="r1", created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_models_reexport_grounded_student_context():
    # Spec §3/§9: identity equivalence, NOT a behavioral substitute.
    assert rec.StudentContext is grounded.StudentContext
    assert rec.SourceRef is grounded.SourceRef
    req = RecommendRequest(query_text="x", student_context=grounded.StudentContext(school="S"))
    assert req.student_context.school == "S"


def test_config_model_dump_redacts_secrets_by_default():
    # Spec §7: model_dump itself must not leak keys (SecretStr), not just safe_snapshot().
    # Any serialization path — model_dump(), model_dump(mode="json"), model_dump_json(),
    # dict(settings) — must not emit plaintext.
    from dext_recommend import RecommendSettings
    s = RecommendSettings(
        embedding_api_key="secret-embed",
        llm_api_key="secret-llm",
        neo4j_password="secret-pw",
    )
    dumped = s.model_dump(mode="json")
    dumped_json = s.model_dump_json()
    dumped_dict = dict(s)
    for secret in ("secret-embed", "secret-llm", "secret-pw"):
        assert secret not in str(dumped)
        assert secret not in dumped_json
        assert secret not in str(dumped_dict)
    assert dumped["qdrant_alias"] == "dext_professors_current"


def test_response_collections_are_deeply_immutable():
    # Spec §3/§9: frozen dataclass + mutable list is not "immutable".
    rp = RecommendedProfessor(
        entity_id="e1", display_name="P", university="U", org_units=[],
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="unknown", role_status="included", profile_url=None,
        research_summary="R", match_level="strong", short_reasons=["x"], score=0.9,
        score_components={}, matched_topics=[], matched_statements=[],
        matched_publications=[], evidence_refs=[], risk_flags=[],
        available_actions=["detail"],
    )
    with pytest.raises((AttributeError, TypeError)):
        rp.matched_topics.append("y")
    with pytest.raises((AttributeError, TypeError)):
        RecommendationFilters().university_ids.append("U")
    with pytest.raises((AttributeError, TypeError)):
        ConversationContext(prior_result_entity_ids=["e1"]).prior_result_entity_ids.append("e2")


def test_snapshot_is_immutable():
    snap = _snap()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.build_id = "other"


def test_all_four_fakes_satisfy_protocols():
    snap = _snap()
    assert isinstance(FakeBuildSnapshotPort(snap), BuildSnapshotPort)
    assert isinstance(
        FakeQueryEmbeddingPort([0.1], "fp-x"), QueryEmbeddingPort,
    )
    assert isinstance(FakeVectorSearchPort(), VectorSearchPort)
    assert isinstance(FakeProfessorFactPort(), ProfessorFactPort)


def test_core_wired_with_fakes_does_not_touch_real_services():
    snap = _snap()
    deps = RecommendDeps(
        snapshot_port=FakeBuildSnapshotPort(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(),
        facts_port=FakeProfessorFactPort(),
    )
    core = RecommendationCore(deps)
    # placeholder recommend raises NotImplementedError, but construction is clean
    with pytest.raises(NotImplementedError):
        core.recommend(RecommendRequest(query_text="x"))


def test_error_codes_complete():
    codes = {c.value for c in RecommendationErrorCode}
    assert "active_build_unavailable" in codes
    assert "no_candidates_after_filters" in codes
    assert "unauthorized_contact" in codes
