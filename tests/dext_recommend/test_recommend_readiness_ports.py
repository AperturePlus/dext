from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from dext_recommend import (
    CatalogReleaseObservation,
    CatalogReleasePort,
    FakeCatalogReleasePort,
    FakeGraphReleasePort,
    FakeRankingProfilePort,
    FakeVectorReleasePort,
    GraphReleaseObservation,
    GraphReleasePort,
    PayloadCoverageObservation,
    ProfessorReleaseSample,
    RankingProfilePort,
    ReadinessSourceError,
    VectorReleaseObservation,
    VectorReleasePort,
)


def _catalog(build_id: str = "b1") -> CatalogReleaseObservation:
    return CatalogReleaseObservation(
        build_id=build_id,
        catalog_schema_version=6,
        qdrant_payload_schema_version=2,
        embedding_provider="sf",
        embedding_model="bge-m3",
        embedding_dimension=1024,
        embedding_fingerprint="fp",
        taxonomy_version="t1",
        expected_professor_count=1,
        sample_entity_ids=["e1"],
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


async def test_raw_release_fakes_satisfy_protocols_and_represent_absence():
    assert isinstance(FakeCatalogReleasePort(), CatalogReleasePort)
    assert isinstance(FakeVectorReleasePort(), VectorReleasePort)
    assert isinstance(FakeGraphReleasePort(), GraphReleasePort)
    assert isinstance(FakeRankingProfilePort(), RankingProfilePort)
    assert await FakeCatalogReleasePort().read_active() is None
    assert await FakeVectorReleasePort().read_current("current", ()) is None
    assert await FakeGraphReleasePort().read_active(()) is None


async def test_raw_release_fakes_can_represent_three_way_mismatch():
    sample = ProfessorReleaseSample("e1", ["org"], profile_hash="h")
    catalog = FakeCatalogReleasePort(_catalog("catalog"), [sample])
    vector = FakeVectorReleasePort(VectorReleaseObservation(
        alias="current", target_collection="phys", build_id="vector",
        payload_schema_version=2, embedding_fingerprint="fp-other",
        embedding_dimension=1024, point_count=1, samples=[sample],
        coverage=[PayloadCoverageObservation("org_unit_ids", 1.0, 1)],
    ))
    graph = FakeGraphReleasePort(GraphReleaseObservation("graph", [sample]))
    assert (await catalog.read_active()).build_id == "catalog"
    assert (await vector.read_current("current", ("e1",))).build_id == "vector"
    assert (await graph.read_active(("e1",))).build_id == "graph"


async def test_readiness_source_error_is_safe_and_structured():
    error = ReadinessSourceError(
        "neo4j\nsource",
        "bolt://user:secret@host pointer\nmissing password=hunter2",
        retryable=True,
    )
    port = FakeGraphReleasePort(error=error)
    with pytest.raises(ReadinessSourceError) as raised:
        await port.read_active(())
    assert "\n" not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert "hunter2" not in str(raised.value)
    assert raised.value.retryable is True


async def test_ranking_profile_fake_returns_injected_version():
    assert await FakeRankingProfilePort("rank-v2").read_version(Path("unused")) == "rank-v2"
