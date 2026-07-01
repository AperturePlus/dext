# tests/test_recommend_fake_ports.py
from __future__ import annotations

from datetime import datetime, timezone

from dext_recommend import (
    ActiveBuildSnapshot, BuildSnapshotPort, FakeBuildSnapshotPort,
    FakeProfessorFactPort, FakeQueryEmbeddingPort, FakeVectorSearchPort,
    ProfessorDetail, ProfessorFactPort, QueryEmbeddingPort, VectorSearchPort,
)


def _snap():
    return ActiveBuildSnapshot(
        build_id="b-1", catalog_schema_version=6, neo4j_active_build_id="b-1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=2,
        embedding_provider="sf", embedding_model="bge-m3", embedding_dimension=1024,
        embedding_fingerprint="fp-x", taxonomy_version="t1",
        ranking_profile_version="r1", created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def test_fake_build_snapshot_port_satisfies_protocol():
    port = FakeBuildSnapshotPort(_snap())
    assert isinstance(port, BuildSnapshotPort)
    assert port.get_snapshot().build_id == "b-1"


def test_fake_build_snapshot_port_no_active():
    port = FakeBuildSnapshotPort(None)
    assert port.get_snapshot() is None


def test_fake_query_embedding_port_returns_fixed_vector_and_fingerprint():
    port = FakeQueryEmbeddingPort(vector=[0.1, 0.2], fingerprint="fp-x")
    assert isinstance(port, QueryEmbeddingPort)
    result = port.embed(_snap(), "NLP")
    assert result.vector == [0.1, 0.2]
    assert result.embedding_fingerprint == "fp-x"


def test_fake_vector_search_port_returns_preset_hits():
    from dext_recommend import VectorHit
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    port = FakeVectorSearchPort(hits=hits)
    assert isinstance(port, VectorSearchPort)
    out = port.hybrid_recall(_snap(), [0.1], filters=None, oversample=200, profile_version="r1")
    assert out == hits


def test_fake_professor_fact_port_returns_preset_detail():
    detail = ProfessorDetail(
        build_id="b-1", profile_hash=None, entity_id="e1", display_name="P",
        university="U", org_units=[], title="Prof", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="unknown",
        role_status="included", profile_url=None, research_statements=[],
        approved_topics=[], selected_publication_mentions=[], bio_snippets=[],
        source_urls=[], provenance_refs=[], quality_findings=[], risk_flags=[],
    )
    port = FakeProfessorFactPort(details={"e1": detail})
    assert isinstance(port, ProfessorFactPort)
    from dext_recommend import ViewerPermissions
    got = port.get_detail(_snap(), "e1", False, ViewerPermissions())
    assert got is detail
