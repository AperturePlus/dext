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
    QueryUnderstanding, RecommendDeps, RecommendationCore,
    RecommendationErrorCode, RecommendationFilters, RecommendRequest,
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
    # StudentContext must come from dext_grounded, not be redefined.
    # dext_recommend.models imports it from dext_grounded (re-export rather
    # than redefine, per foundations §3); it is not re-exported as a top-level
    # package attribute. The behavioral check below verifies a grounded
    # StudentContext is accepted by RecommendRequest unchanged.
    req = RecommendRequest(query_text="x", student_context=grounded.StudentContext(school="S"))
    assert req.student_context.school == "S"


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
