# tests/test_recommend_fake_ports.py
from __future__ import annotations

from datetime import datetime, timezone

from dext_recommend import (
    ActiveBuildSnapshot, ActiveSnapshotProvider, FakeActiveSnapshotProvider,
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


def test_fake_active_snapshot_provider_satisfies_protocol():
    port = FakeActiveSnapshotProvider(_snap())
    assert isinstance(port, ActiveSnapshotProvider)
    assert port.get_snapshot().build_id == "b-1"


def test_fake_active_snapshot_provider_no_active():
    port = FakeActiveSnapshotProvider(None)
    assert port.get_snapshot() is None


async def test_fake_query_embedding_port_returns_fixed_vector_and_fingerprint():
    port = FakeQueryEmbeddingPort(vector=[0.1, 0.2], fingerprint="fp-x")
    assert isinstance(port, QueryEmbeddingPort)
    result = await port.embed(_snap(), "NLP")
    assert result.vector == (0.1, 0.2)
    assert result.embedding_fingerprint == "fp-x"


async def test_fake_vector_search_port_returns_preset_hits():
    from dext_recommend import VectorHit
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    port = FakeVectorSearchPort(hits=hits)
    assert isinstance(port, VectorSearchPort)
    out = await port.hybrid_recall(
        _snap(), [0.1], filters=None, oversample=200, profile_version="r1",
        rrf_k=60,
    )
    assert out == hits


async def test_fake_vector_hybrid_recall_records_rrf_k_and_sparse_vector():
    from tests.dext_recommend._recfixtures import snapshot, vector_hits_case
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports._fakes import FakeVectorSearchPort

    port = FakeVectorSearchPort(hits=vector_hits_case("happy"))
    snap = snapshot()
    await port.hybrid_recall(
        snap, [0.1, 0.2], RecommendationFilters(), 200, "r1",
        rrf_k=42, sparse_vector={"indices": [0, 1], "values": [0.5, 0.5]},
    )
    call = port.hybrid_recall_calls[-1]
    assert call["rrf_k"] == 42
    assert call["sparse_vector"] == {"indices": [0, 1], "values": [0.5, 0.5]}


async def test_fake_professor_fact_port_returns_preset_detail():
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
    got = await port.get_detail(_snap(), "e1", False, ViewerPermissions())
    assert got is detail


async def test_fake_vector_search_port_alias_readback_returns_default_bound_to_build():
    port = FakeVectorSearchPort()
    alias = await port.alias_readback(_snap())
    assert alias.alias == "dext_professors_current"
    assert alias.target_collection == "phys-1"
    assert alias.build_id == "b-1"
    assert alias.payload_schema_version == 2


async def test_fake_vector_search_port_alias_readback_returns_preset_when_provided():
    from dext_recommend import AliasReadback
    preset = AliasReadback(
        alias="a", target_collection="phys-9", build_id="b-9", payload_schema_version=3,
    )
    port = FakeVectorSearchPort(alias=preset)
    got = await port.alias_readback(_snap())
    assert got is preset


async def test_fake_vector_search_port_count_readback_returns_preset():
    port = FakeVectorSearchPort(count=42)
    got = await port.count_readback(_snap())
    assert got == 42
    got2 = await port.count_readback(_snap(), filter={"k": "v"})
    assert got2 == 42


async def test_fake_professor_fact_port_hydrate_returns_only_known_entities():
    from dext_recommend import ProfessorFact
    fact_a = ProfessorFact(
        "e1", "A", "U", ["org"], "T", "professor", "confirmed", "confirmed",
        "included", None, None, None,
    )
    fact_b = ProfessorFact(
        "e2", "B", "U", ["org"], "T", "professor", "confirmed", "confirmed",
        "included", None, None, None,
    )
    port = FakeProfessorFactPort(facts={"e1": fact_a, "e2": fact_b})
    out = await port.hydrate(_snap(), ["e1", "e3", "e2"])
    assert set(out.keys()) == {"e1", "e2"}
    assert out["e1"] is fact_a
    assert out["e2"] is fact_b
