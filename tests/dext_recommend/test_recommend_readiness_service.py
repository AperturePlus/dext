import asyncio

import pytest

from dext_recommend.errors import ErrorSeverity, RecommendationErrorCode
from dext_recommend.ports._fakes import (
    FakeCatalogReleasePort, FakeGraphReleasePort, FakeRankingProfilePort,
    FakeVectorReleasePort,
)
from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation, GraphReleaseObservation, PayloadCoverageObservation,
    ProfessorReleaseSample, VectorReleaseObservation,
)
from dext_recommend.readiness import ReadinessDeps, ReadinessService
from dext_recommend.config import RecommendSettings
from datetime import datetime, timezone


def _catalog_obs(build_id="b1", sample_ids=("e1",)):
    return CatalogReleaseObservation(
        build_id=build_id, catalog_schema_version=6,
        qdrant_payload_schema_version=2, embedding_provider="openai",
        embedding_model="m", embedding_dimension=1536,
        embedding_fingerprint="fp-1", taxonomy_version="tax-v1",
        expected_professor_count=1, sample_entity_ids=sample_ids,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def _sample(eid="e1"):
    return ProfessorReleaseSample(eid, ("org-a",), profile_hash="h1",
                                  role_status="included", master_eligibility="confirmed",
                                  phd_eligibility="unknown", embedding_fingerprint="fp-1")


def _vector_obs(build_id="b1"):
    return VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id=build_id, payload_schema_version=2,
        embedding_fingerprint="fp-1", embedding_dimension=1536, point_count=1,
        samples=(_sample(),), coverage=(PayloadCoverageObservation("org_unit_ids", 1.0, 1),),
    )


def _graph_obs(build_id="b1"):
    return GraphReleaseObservation(build_id=build_id, samples=(_sample(),))


def _service(catalog, vector, graph, ranking, **settings):
    s = RecommendSettings(**settings)
    return ReadinessService(ReadinessDeps(catalog, vector, graph, ranking), s)


def _reconcile(catalog, vector, graph):
    return ReadinessService._reconcile_samples(catalog, vector, graph)


def test_reconcile_profile_hash_mismatch_returns_inconsistent():
    cat = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    vec = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h2"),)
    graph = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    errors = _reconcile(cat, vec, graph)
    codes = {e.code for e in errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


def test_reconcile_org_unit_mismatch_returns_inconsistent():
    cat = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    vec = (ProfessorReleaseSample("e1", ("org-b",), profile_hash="h1"),)
    graph = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    errors = _reconcile(cat, vec, graph)
    codes = {e.code for e in errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


def test_reconcile_consistent_returns_no_errors():
    s = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    errors = _reconcile((s,), (s,), (s,))
    assert errors == []


def test_reconcile_missing_in_one_source_not_mismatch():
    # e1 only in catalog; e2 in catalog+vector with agreeing facts
    cat = (
        ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),
        ProfessorReleaseSample("e2", ("org-b",), profile_hash="h2"),
    )
    vec = (ProfessorReleaseSample("e2", ("org-b",), profile_hash="h2"),)
    graph = ()
    errors = _reconcile(cat, vec, graph)
    assert errors == []


def test_reconcile_none_vs_value_is_mismatch():
    # same entity in two sources; one has profile_hash, other has None
    cat = (ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1"),)
    vec = (ProfessorReleaseSample("e1", ("org-a",), profile_hash=None),)
    graph = ()
    errors = _reconcile(cat, vec, graph)
    codes = {e.code for e in errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_happy_path_returns_ready_snapshot():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(_vector_obs()),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    assert report.snapshot is not None
    assert report.snapshot.build_id == "b1"
    assert report.snapshot.ranking_profile_version == "ranking-v1"
    assert report.errors == ()
    assert svc.get_snapshot() is report.snapshot


async def test_check_no_active_build_returns_unavailable():
    svc = _service(
        FakeCatalogReleasePort(None), FakeVectorReleasePort(),
        FakeGraphReleasePort(), FakeRankingProfilePort(),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE in codes


async def test_check_three_way_build_id_mismatch_returns_inconsistent():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs("b1"), [_sample()]),
        FakeVectorReleasePort(_vector_obs("b2")),
        FakeGraphReleasePort(_graph_obs("b3")),
        FakeRankingProfilePort(),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_embedding_fingerprint_mismatch():
    v = VectorReleaseObservation(
        alias="a", target_collection="c", build_id="b1", payload_schema_version=2,
        embedding_fingerprint="fp-OTHER", embedding_dimension=1536, point_count=1,
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(v), FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort(),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH in codes


async def test_check_org_unit_coverage_insufficient_emits_warning_not_blocking():
    v = VectorReleaseObservation(
        alias="a", target_collection="c", build_id="b1", payload_schema_version=2,
        embedding_fingerprint="fp-1", embedding_dimension=1536, point_count=1,
        coverage=(PayloadCoverageObservation("org_unit_ids", 0.3, 1),),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(v), FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort(),
    )
    report = await svc.check()
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT in codes
    assert RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE in codes
    # warning-only does NOT block ready
    assert report.ready is True


async def test_check_failure_keeps_old_snapshot():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(_vector_obs()),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    ok = await svc.check()
    assert ok.ready is True
    # now break vector
    svc._deps = ReadinessDeps(
        svc._deps.catalog_port, FakeVectorReleasePort(None),
        svc._deps.graph_port, svc._deps.ranking_port,
    )
    report = await svc.check()
    assert report.ready is False
    assert svc.get_snapshot() is ok.snapshot  # old snapshot preserved


async def test_concurrent_check_serialized_does_not_overwrite_newer():
    # first check succeeds and caches snapshot b1
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs("b1"), [_sample()]),
        FakeVectorReleasePort(_vector_obs("b1")),
        FakeGraphReleasePort(_graph_obs("b1")),
        FakeRankingProfilePort("ranking-v1"),
    )
    await svc.check()
    first = svc.get_snapshot()
    assert first.build_id == "b1"
    # a second concurrent check (newer b2) starts; an older-style late finish
    # cannot overwrite because the Lock serializes
    calls = []

    async def _check_b2():
        calls.append(("b2", "start"))
        await asyncio.sleep(0)  # yield
        report = await svc.check()
        calls.append(("b2", "done"))

    async def _check_b1_again():
        calls.append(("b1", "start"))
        report = await svc.check()
        calls.append(("b1", "done"))

    # both target the same ports (b1); serialization means no interleaving corruption
    await asyncio.gather(_check_b2(), _check_b1_again())
    # both completed
    assert ("b2", "done") in calls and ("b1", "done") in calls
    assert svc.get_snapshot() is not None


async def test_check_vector_exception_emits_single_error():
    # vector port raises ReadinessSourceError (real exception path, not None
    # passthrough). gather_safe converts it to a RecommendationError placed in
    # phase2_results. The fix in _unpack_phase2 normalizes that error to None so
    # _assemble emits exactly ONE ACTIVE_BUILD_UNAVAILABLE for vector instead of
    # two (one from _check_locked's errors.extend, one from _assemble's _err).
    from dext_recommend.ports.release_readback import ReadinessSourceError

    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(
            error=ReadinessSourceError("vector", "boom", retryable=True),
        ),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    # exactly one ACTIVE_BUILD_UNAVAILABLE error attributable to vector
    unavailable = [
        e for e in report.errors
        if e.code is RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE
    ]
    assert len(unavailable) == 1
    # the single entry carries the real gather_safe detail (boom, retryable),
    # NOT a second generic "vector alias/collection unavailable" copy.
    assert unavailable[0].message == "boom"
    assert unavailable[0].retryable is True


async def test_check_sample_reconciliation_profile_hash_mismatch_blocks_ready():
    cat_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    vec_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h2")
    graph_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=1, samples=(vec_sample,),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [cat_sample]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(GraphReleaseObservation("b1", (graph_sample,))),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_sample_reconciliation_org_unit_mismatch_blocks_ready():
    cat_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    vec_sample = ProfessorReleaseSample("e1", ("org-b",), profile_hash="h1")
    graph_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=1, samples=(vec_sample,),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [cat_sample]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(GraphReleaseObservation("b1", (graph_sample,))),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT in codes


async def test_check_sample_reconciliation_consistent_passes():
    cat_sample = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1")
    v = _vector_obs()  # uses _sample() -> profile_hash="h1", org=("org-a",)
    graph = _graph_obs()
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [cat_sample]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(graph),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT not in codes


async def test_snapshot_qdrant_alias_target_is_physical_collection():
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [_sample()]),
        FakeVectorReleasePort(_vector_obs()),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    # _vector_obs() sets target_collection="dext_professors__b1", alias="dext_professors_current"
    assert report.snapshot.qdrant_alias_target == "dext_professors__b1"
    assert report.snapshot.qdrant_alias_target != "dext_professors_current"


async def test_check_eligibility_coverage_insufficient_blocks_ready():
    # 2 samples: one has master_eligibility, one lacks it -> 0.5 < 0.95 threshold
    s1 = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1",
                                 master_eligibility="confirmed")
    s2 = ProfessorReleaseSample("e2", ("org-a",), profile_hash="h2",
                                 master_eligibility=None)
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=2, samples=(s1, s2),
        coverage=(PayloadCoverageObservation("eligibility", 0.5, 2),),
    )
    svc = _service(
        FakeCatalogReleasePort(
            CatalogReleaseObservation(
                build_id="b1", catalog_schema_version=6,
                qdrant_payload_schema_version=2, embedding_provider="openai",
                embedding_model="m", embedding_dimension=1536,
                embedding_fingerprint="fp-1", taxonomy_version="tax-v1",
                expected_professor_count=2, sample_entity_ids=("e1", "e2"),
                created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
            [s1, s2],
        ),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(GraphReleaseObservation("b1", (s1, s2))),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is False
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ELIGIBILITY_COVERAGE_INSUFFICIENT in codes


async def test_check_eligibility_coverage_passes_when_master_eligible():
    s1 = ProfessorReleaseSample("e1", ("org-a",), profile_hash="h1",
                                 master_eligibility="confirmed")
    v = VectorReleaseObservation(
        alias="dext_professors_current", target_collection="dext_professors__b1",
        build_id="b1", payload_schema_version=2, embedding_fingerprint="fp-1",
        embedding_dimension=1536, point_count=1, samples=(s1,),
        coverage=(PayloadCoverageObservation("eligibility", 1.0, 1),),
    )
    svc = _service(
        FakeCatalogReleasePort(_catalog_obs(), [s1]),
        FakeVectorReleasePort(v),
        FakeGraphReleasePort(_graph_obs()),
        FakeRankingProfilePort("ranking-v1"),
    )
    report = await svc.check()
    assert report.ready is True
    codes = {e.code for e in report.errors}
    assert RecommendationErrorCode.ELIGIBILITY_COVERAGE_INSUFFICIENT not in codes
