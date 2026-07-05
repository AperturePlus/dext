"""Versioned competition ranking profile (C3 spec §5)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_RANKING_PROFILE_PATH = Path("data/competition/profiles/ranking-v1.json")
_SCORE_COMPONENTS = (
    "interest_match_score",
    "eligibility_score",
    "effort_fit_score",
    "timeline_fit_score",
    "goal_fit_score",
    "provenance_score",
)


@dataclass(frozen=True, slots=True)
class CompetitionRankingProfile:
    version: str
    weights: dict[str, float]
    fit_thresholds: dict[str, float]
    tie_break: tuple[str, ...]
    recall_top_k: int
    freshness_notice: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("competition ranking profile version must be non-empty")
        weights = {str(k): float(v) for k, v in self.weights.items()}
        missing = sorted(set(_SCORE_COMPONENTS) - weights.keys())
        extra = sorted(set(weights) - set(_SCORE_COMPONENTS))
        if missing or extra:
            raise ValueError(
                "competition ranking profile weights mismatch: "
                f"missing={missing}, extra={extra}"
            )
        if any(value < 0.0 for value in weights.values()):
            raise ValueError("competition ranking profile weights must be non-negative")
        total = sum(weights.values())
        if total <= 0.0:
            raise ValueError("competition ranking profile weights must have positive sum")
        thresholds = {str(k): float(v) for k, v in self.fit_thresholds.items()}
        for name in ("strong", "good", "exploratory"):
            if name not in thresholds:
                raise ValueError(f"missing fit threshold: {name}")
            if thresholds[name] < 0.0 or thresholds[name] > 1.0:
                raise ValueError(f"fit threshold out of range: {name}")
        if not (thresholds["strong"] >= thresholds["good"] >= thresholds["exploratory"]):
            raise ValueError("fit thresholds must descend strong >= good >= exploratory")
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "fit_thresholds", thresholds)
        object.__setattr__(self, "tie_break", tuple(self.tie_break or ()))
        object.__setattr__(self, "recall_top_k", int(self.recall_top_k))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CompetitionRankingProfile":
        return cls(
            version=str(raw["version"]),
            weights=dict(raw["weights"]),
            fit_thresholds=dict(raw["fit_thresholds"]),
            tie_break=tuple(raw.get("tie_break") or ()),
            recall_top_k=int(raw.get("recall_top_k", 24)),
            freshness_notice=str(raw.get("freshness_notice", "")),
        )

    def fit_level(self, score: float) -> str:
        if score >= self.fit_thresholds["strong"]:
            return "strong"
        if score >= self.fit_thresholds["good"]:
            return "good"
        if score >= self.fit_thresholds["exploratory"]:
            return "exploratory"
        return "weak"


def load_ranking_profile(path: str | Path = DEFAULT_RANKING_PROFILE_PATH) -> CompetitionRankingProfile:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("competition ranking profile must be a JSON object")
    return CompetitionRankingProfile.from_dict(raw)


__all__ = [
    "CompetitionRankingProfile",
    "DEFAULT_RANKING_PROFILE_PATH",
    "load_ranking_profile",
]
