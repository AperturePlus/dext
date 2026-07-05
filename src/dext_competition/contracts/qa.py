"""Detail + grounded QA + comparison contracts (overview §3, C4 spec)."""
from __future__ import annotations

from dataclasses import dataclass

from dext_grounded import SourceRef


@dataclass(frozen=True, slots=True)
class CompetitionDetail:
    competition_id: str
    display_name: str
    category: str
    summary: str
    eligibility: str
    schedule: str
    team_policy: str
    materials: tuple[str, ...] = ()
    ai_compliance: str = ""
    preparation_focus: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    official_links: tuple[str, ...] = ()
    internal_source_refs: tuple[SourceRef, ...] = ()
    freshness_notice: str | None = None

    def __post_init__(self) -> None:
        for f in ("materials", "preparation_focus", "risk_flags",
                  "official_links", "internal_source_refs"):
            object.__setattr__(
                self, f,
                tuple(getattr(self, f)) if getattr(self, f) is not None else (),
            )


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    question: str
    answer: str
    evidence_status: str            # grounded|partial|uncertain
    internal_source_refs: tuple[SourceRef, ...] = ()
    freshness_notice: str | None = None
    competition_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "internal_source_refs",
            tuple(self.internal_source_refs) if self.internal_source_refs is not None else (),
        )


@dataclass(frozen=True, slots=True)
class CompetitionComparison:
    competition_ids: tuple[str, ...]
    summary: str
    dimensions: tuple[tuple[str, str, str], ...] = ()  # (dimension, id_a_value, id_b_value)
    internal_source_refs: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        for f in ("competition_ids", "dimensions", "internal_source_refs"):
            object.__setattr__(
                self, f,
                tuple(getattr(self, f)) if getattr(self, f) is not None else (),
            )


__all__ = ["CompetitionComparison", "CompetitionDetail", "GroundedAnswer"]
