"""Opt-in live runtime round-trip against local published dependencies."""
from __future__ import annotations

import os

import pytest

from dext_recommend import (
    RecommendSettings, RecommendationFilters, build_live_recommendation_runtime,
)


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("DEXT_RECOMMEND_RUN_LIVE_RUNTIME") != "1",
    reason="set DEXT_RECOMMEND_RUN_LIVE_RUNTIME=1",
)
async def test_live_runtime_embedding_and_hybrid_recall_round_trip():
    runtime = await build_live_recommendation_runtime(RecommendSettings())
    try:
        snapshot = runtime.readiness.get_snapshot()
        assert snapshot is not None
        embedding = await runtime.core.deps.embedding_port.embed(
            snapshot, "自然语言处理",
        )
        hits = await runtime.core.deps.vector_port.hybrid_recall(
            snapshot,
            list(embedding.vector),
            RecommendationFilters(),
            10,
            snapshot.ranking_profile_version,
            rrf_k=60,
            sparse_vector=embedding.sparse_vector,
        )
        assert isinstance(hits, list)
    finally:
        await runtime.aclose()
