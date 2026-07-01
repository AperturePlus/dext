# src/dext_recommend/core/recall.py
"""Adaptive oversample step loop + RRF per-query normalization.

core does NOT touch raw dense/sparse scores — only the fused VectorHit.score
emitted by the port. Normalization maps the fused score to [0,1] per query.
"""
from __future__ import annotations

from dext_recommend.models import RecommendationFilters
from dext_recommend.ports.vector_search import VectorHit, VectorSearchPort
from dext_recommend.readiness import ActiveBuildSnapshot

from dext_recommend.core.ranking_profile import RankingProfile


def compute_oversample_steps(
    request_oversample: int, profile: RankingProfile, *, oversample_max: int,
) -> tuple[int, ...]:
    steps = tuple(s for s in profile.oversample_steps
                  if s >= request_oversample and s <= oversample_max)
    if not steps:
        # request_oversample larger than every profile step under max -> clamp to max
        clamp = min(request_oversample, oversample_max)
        steps = (clamp,)
    return steps


def normalize_rrf(hits: list[VectorHit]) -> dict[str, float]:
    if not hits:
        return {}
    if len(hits) == 1:
        return {hits[0].entity_id: 1.0}
    scores = [h.score for h in hits]
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return {h.entity_id: 1.0 for h in hits}
    return {h.entity_id: (h.score - lo) / (hi - lo) for h in hits}


async def recall_loop(
    snapshot: ActiveBuildSnapshot,
    vector_port: VectorSearchPort,
    query_vector: list[float],
    filters: RecommendationFilters,
    profile: RankingProfile,
    *,
    oversample_max: int,
    request_oversample: int,
    limit: int,
) -> tuple[list[VectorHit], int]:
    steps = compute_oversample_steps(request_oversample, profile, oversample_max=oversample_max)
    last_hits: list[VectorHit] = []
    steps_used = 0
    for step in steps:
        steps_used += 1
        hits = await vector_port.hybrid_recall(
            snapshot, query_vector, filters, step, profile.version,
        )
        last_hits = list(hits)
        if len(hits) >= limit:
            break
    return last_hits, steps_used


__all__ = ["compute_oversample_steps", "normalize_rrf", "recall_loop"]
