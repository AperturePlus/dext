# src/dext_recommend/core/recall.py
"""Adaptive oversample step loop + RRF per-query normalization.

core does NOT touch raw dense/sparse scores — only the fused VectorHit.score
emitted by the port. Normalization maps the fused score to [0,1] per query.

W1 (R3 hardening): recall_loop now does per-step payload_prefilter -> hydrate
(deduped by entity_id across steps) -> final_filter. The current step's
survivors are authoritative (overwritten each step); only the hydrated fact
cache persists across steps. Fused scores from different oversample steps are
NOT comparable, so mixing them is forbidden. Break when
len(current_survivors) >= limit.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dext_recommend.models import RecommendationFilters
from dext_recommend.ports.vector_search import VectorHit, VectorSearchPort
from dext_recommend.ports.professor_facts import ProfessorFact, ProfessorFactPort
from dext_recommend.readiness import ActiveBuildSnapshot

from dext_recommend.core._resilience import (
    RecommendExecutionContext, _guarded_async,
)
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.filters import payload_prefilter, final_filter, FilterDiagnostics
from dext_recommend.core.intent import RecommendRoute


@dataclass(frozen=True, slots=True)
class StepDiag:
    step: int
    raw_hits: int
    post_prefilter: int
    post_filter: int


@dataclass(frozen=True, slots=True)
class RecallResult:
    survivors: tuple[VectorHit, ...]
    fact_map: Mapping[str, ProfessorFact]
    filter_diagnostics: FilterDiagnostics
    steps_used: int
    step_diags: tuple[StepDiag, ...]


def compute_oversample_steps(
    request_oversample: int, profile: RankingProfile, *, oversample_max: int,
) -> tuple[int, ...]:
    steps = tuple(s for s in profile.oversample_steps
                  if s >= request_oversample and s <= oversample_max)
    if not steps:
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
    effective_filters: RecommendationFilters,
    profile: RankingProfile,
    *,
    facts_port: ProfessorFactPort,
    route: RecommendRoute,
    coverage_flags: Mapping[str, bool],
    review_policy: str,
    embedding_sparse_vector,
    oversample_max: int,
    request_oversample: int,
    limit: int,
    ctx: RecommendExecutionContext,
) -> RecallResult:
    steps = compute_oversample_steps(request_oversample, profile, oversample_max=oversample_max)
    org_unit_degraded = coverage_flags.get("org_unit_ids") is not True

    fact_cache: dict[str, ProfessorFact] = {}
    hydrated_ids: set[str] = set()
    current_survivors: list[VectorHit] = []
    current_filter_diag = FilterDiagnostics()
    step_diags: list[StepDiag] = []
    steps_used = 0

    for step in steps:
        steps_used += 1
        # awaited immediately; safe to close over loop var `step` and `embedding_sparse_vector`.
        hits = await _guarded_async(
            ctx, "vector_recall",
            "vector_unavailable",
            lambda: vector_port.hybrid_recall(
                snapshot, query_vector, effective_filters, step, profile.version,
                rrf_k=profile.rrf_k, sparse_vector=embedding_sparse_vector,
            ),
            attempt=step,
        )
        # dedup within this step by entity_id, keep first occurrence
        seen_step: set[str] = set()
        dedup_hits: list[VectorHit] = []
        for h in hits:
            if h.entity_id not in seen_step:
                seen_step.add(h.entity_id)
                dedup_hits.append(h)
        pref = payload_prefilter(dedup_hits, effective_filters, org_unit_degraded=org_unit_degraded)

        new_ids = [h.entity_id for h in pref if h.entity_id not in hydrated_ids]
        if new_ids:
            hydrated_ids.update(new_ids)
            # awaited immediately; safe to close over loop var `step` and `new_ids`.
            hydrated = await _guarded_async(
                ctx, "candidate_hydrate",
                "hydrate_unavailable",
                lambda: facts_port.hydrate(snapshot, new_ids),
                attempt=step,
            )
            fact_cache.update(hydrated)

        current_survivors, current_filter_diag = final_filter(
            pref, fact_cache, effective_filters, route, coverage_flags,
            review_policy=review_policy,
        )
        step_diags.append(StepDiag(
            step=step, raw_hits=len(hits),
            post_prefilter=len(pref), post_filter=len(current_survivors),
        ))
        if len(current_survivors) >= limit:
            break

    return RecallResult(
        survivors=tuple(current_survivors),
        fact_map=dict(fact_cache),
        filter_diagnostics=current_filter_diag,
        steps_used=steps_used,
        step_diags=tuple(step_diags),
    )


__all__ = ["StepDiag", "RecallResult", "compute_oversample_steps", "normalize_rrf", "recall_loop"]
