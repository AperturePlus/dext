# src/dext_recommend/core/ranking_profile.py
"""Versioned ranking profile: weights, RRF, oversample steps, match_level
thresholds, tie-break, detail window. Loaded via RankingProfilePort.read_profile.
Schema validated here so core never silently falls back to defaults.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dext_recommend._immutable import freeze_mapping

_REQUIRED_WEIGHTS = (
    "semantic_score", "topic_statement_score", "student_fit_score",
    "eligibility_score", "provenance_score", "completeness_score",
)
_WEIGHT_SUM_TOLERANCE = 0.01
_allowed_tie_break = {"score", "semantic_score", "evidence_count", "entity_id"}


@dataclass(frozen=True, slots=True)
class RankingProfile:
    version: str
    weights: Mapping[str, float]
    rrf_k: int
    oversample_steps: tuple[int, ...]
    detail_rerank_window: int
    detail_fetch_concurrency: int
    detail_rerank_window_max: int
    match_level_thresholds: Mapping[str, float]
    tie_break: tuple[str, ...]
    same_field_boost_per_topic: float
    same_field_boost_max: float

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("RankingProfile.version must be non-empty")
        missing = [k for k in _REQUIRED_WEIGHTS if k not in self.weights]
        if missing:
            raise ValueError(f"RankingProfile.weights missing: {missing}")
        total = sum(self.weights[k] for k in _REQUIRED_WEIGHTS)
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"RankingProfile.weights must sum to 1.0±{_WEIGHT_SUM_TOLERANCE}, got {total}"
            )
        steps = tuple(self.oversample_steps)
        if not steps or any(steps[i] >= steps[i + 1] for i in range(len(steps) - 1)):
            raise ValueError("RankingProfile.oversample_steps must be strictly increasing")
        if self.rrf_k < 1:
            raise ValueError("RankingProfile.rrf_k must be >= 1")
        if self.detail_rerank_window < 1:
            raise ValueError("RankingProfile.detail_rerank_window must be >= 1")
        if self.detail_fetch_concurrency < 1:
            raise ValueError("RankingProfile.detail_fetch_concurrency must be >= 1")
        if self.detail_rerank_window > self.detail_rerank_window_max:
            raise ValueError(
                "detail_rerank_window must be <= detail_rerank_window_max"
            )
        thr = self.match_level_thresholds
        for key in ("excellent", "strong", "possible"):
            if key not in thr:
                raise ValueError(f"match_level_thresholds missing: {key}")
        if not (thr["excellent"] > thr["strong"] > thr["possible"]):
            raise ValueError(
                "match_level_thresholds must satisfy excellent > strong > possible"
            )
        if not self.tie_break:
            raise ValueError("RankingProfile.tie_break must be non-empty")
        if len(set(self.tie_break)) != len(self.tie_break):
            raise ValueError("RankingProfile.tie_break must not repeat fields")
        unknown = [f for f in self.tie_break if f not in _allowed_tie_break]
        if unknown:
            raise ValueError(f"RankingProfile.tie_break has unknown fields: {unknown}")
        if set(self.tie_break) != _allowed_tie_break:
            raise ValueError(
                "RankingProfile.tie_break must be a complete permutation of "
                "{score, semantic_score, evidence_count, entity_id}"
            )
        if not (0.0 <= self.same_field_boost_per_topic <= self.same_field_boost_max <= 1.0):
            raise ValueError(
                "require 0 <= same_field_boost_per_topic <= same_field_boost_max <= 1"
            )
        object.__setattr__(self, "weights", freeze_mapping(self.weights))
        object.__setattr__(self, "oversample_steps", tuple(self.oversample_steps))
        object.__setattr__(
            self, "match_level_thresholds", freeze_mapping(self.match_level_thresholds)
        )
        object.__setattr__(self, "tie_break", tuple(self.tie_break))

    @classmethod
    def from_dict(cls, d: dict) -> "RankingProfile":
        return cls(
            version=d["version"],
            weights=d["weights"],
            rrf_k=d["rrf_k"],
            oversample_steps=tuple(d["oversample_steps"]),
            detail_rerank_window=d["detail_rerank_window"],
            detail_fetch_concurrency=d["detail_fetch_concurrency"],
            detail_rerank_window_max=d["detail_rerank_window_max"],
            match_level_thresholds=d["match_level_thresholds"],
            tie_break=tuple(d["tie_break"]),
            same_field_boost_per_topic=d["same_field_boost_per_topic"],
            same_field_boost_max=d["same_field_boost_max"],
        )


__all__ = ["RankingProfile"]
