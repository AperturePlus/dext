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
