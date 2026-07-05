"""C3 recommendation eval sample shape."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecommendEvalSample:
    sample_id: str
    query_text: str
    expected_categories: tuple[str, ...] = ()
    expected_competition_ids: tuple[str, ...] = ()
    notes: str = ""
    expected_outcome: str = "recommendations"
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_categories",
            tuple(self.expected_categories) if self.expected_categories is not None else (),
        )
        object.__setattr__(self, "tags", tuple(self.tags or ()))
        object.__setattr__(
            self,
            "expected_competition_ids",
            tuple(self.expected_competition_ids) if self.expected_competition_ids is not None else (),
        )


def validate_recommend_eval_sample(sample: RecommendEvalSample) -> None:
    if not sample.sample_id.strip():
        raise ValueError("recommend eval sample_id must be non-empty")
    if not sample.query_text.strip():
        raise ValueError("recommend eval query_text must be non-empty")
    if sample.expected_outcome not in {
        "recommendations", "clarification", "refusal", "no_candidates",
    }:
        raise ValueError("unsupported recommend eval expected_outcome")
    if (
        sample.expected_outcome == "recommendations"
        and not sample.expected_categories
        and not sample.expected_competition_ids
    ):
        raise ValueError("recommend eval sample must carry an expected category or competition")


__all__ = ["RecommendEvalSample", "validate_recommend_eval_sample"]
