"""De-identified user background (grounded-generation spec §2.1).

Consumed by recommend/competition cores; never persisted to the fact layer
(catalog/Neo4j/Qdrant/knowledge-base). ``gpa_bucket`` and ``rank_bucket`` are
bucketed enums — raw GPA/rank values MUST NOT be accepted or logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StudentContext:
    education_stage: str | None = None
    school: str | None = None
    major: str | None = None
    gpa_bucket: str | None = None            # bucket enum, never raw GPA
    rank_bucket: str | None = None           # bucket enum, never raw rank
    research_interests: list[str] = field(default_factory=list)
    achievements_summary: str | None = None
    competition_experience_summary: str | None = None
    profile_completeness: float | None = None

    def __post_init__(self) -> None:
        # research_interests default must remain a fresh list per-instance
        if self.research_interests is None:
            object.__setattr__(self, "research_interests", [])

    def safe_log_summary(self) -> dict[str, object]:
        """Return a redacted dict safe for recommendation logs.

        Per spec §18: log only completeness bucket + whether profile was used,
        never raw profile text, GPA/rank buckets, or research-interest lists.
        """
        uses = any(
            v is not None and v != []
            for v in (
                self.education_stage, self.school, self.major, self.gpa_bucket,
                self.rank_bucket, self.achievements_summary,
                self.competition_experience_summary,
            )
        ) or bool(self.research_interests)
        return {
            "uses_profile": uses,
            "education_stage": self.education_stage,
            "profile_completeness": self.profile_completeness,
        }


__all__ = ["StudentContext"]
