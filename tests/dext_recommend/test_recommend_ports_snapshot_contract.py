"""Snapshot pinning and async I/O boundary contracts for recommendation ports."""
from __future__ import annotations

import inspect

from dext_recommend import ActiveBuildSnapshot, ReadinessService, RecommendationCore
from dext_recommend.ports import (
    ActiveSnapshotProvider, CatalogReleasePort, EmbeddingResult, GraphReleasePort,
    ProfessorFactPort, ProfessorDetail, QueryEmbeddingPort, RankingProfilePort,
    VectorHit, VectorReleasePort, VectorSearchPort,
)


def _sig_params(protocol_method):
    return list(inspect.signature(protocol_method).parameters)


def test_vector_search_hybrid_recall_takes_snapshot():
    params = _sig_params(VectorSearchPort.hybrid_recall)
    assert "snapshot" in params
    assert "query_vector" in params
    assert "filters" in params
    assert "oversample" in params
    assert "profile_version" in params


def test_vector_search_alias_readback_takes_snapshot():
    assert "snapshot" in _sig_params(VectorSearchPort.alias_readback)


def test_vector_search_count_readback_takes_snapshot():
    assert "snapshot" in _sig_params(VectorSearchPort.count_readback)


def test_professor_fact_get_detail_takes_snapshot():
    params = _sig_params(ProfessorFactPort.get_detail)
    assert params[1] == "snapshot"
    assert "entity_id" in params
    assert "include_contacts" in params
    assert "viewer_permissions" in params


def test_professor_fact_hydrate_takes_snapshot():
    params = _sig_params(ProfessorFactPort.hydrate)
    assert params[1] == "snapshot"
    assert "entity_ids" in params


def test_query_embedding_embed_takes_snapshot():
    params = _sig_params(QueryEmbeddingPort.embed)
    assert params[1] == "snapshot"
    assert "query_text" in params


def test_embedding_result_carries_fingerprint():
    r = EmbeddingResult(vector=[0.1, 0.2], embedding_fingerprint="fp-x")
    assert r.embedding_fingerprint == "fp-x"


def test_active_snapshot_provider_only_exposes_validated_snapshot():
    assert hasattr(ActiveSnapshotProvider, "get_snapshot")


def test_raw_readback_ports_do_not_take_validated_snapshot():
    methods = (
        CatalogReleasePort.read_active,
        CatalogReleasePort.read_samples,
        VectorReleasePort.read_current,
        GraphReleasePort.read_active,
        RankingProfilePort.read_version,
    )
    for method in methods:
        assert "snapshot" not in _sig_params(method)


def test_blocking_io_port_methods_are_async():
    methods = (
        CatalogReleasePort.read_active,
        CatalogReleasePort.read_samples,
        VectorReleasePort.read_current,
        GraphReleasePort.read_active,
        RankingProfilePort.read_version,
        QueryEmbeddingPort.embed,
        VectorSearchPort.hybrid_recall,
        VectorSearchPort.alias_readback,
        VectorSearchPort.count_readback,
        ProfessorFactPort.get_detail,
        ProfessorFactPort.hydrate,
    )
    assert all(inspect.iscoroutinefunction(method) for method in methods)


def test_cached_snapshot_read_remains_synchronous():
    assert not inspect.iscoroutinefunction(ActiveSnapshotProvider.get_snapshot)


def test_io_orchestrators_are_async():
    assert inspect.iscoroutinefunction(ReadinessService.check)
    assert inspect.iscoroutinefunction(RecommendationCore.recommend)
