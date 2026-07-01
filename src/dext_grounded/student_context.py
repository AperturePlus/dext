"""De-identified user background (grounded-generation spec §2.1).

Consumed by recommend/competition cores; never persisted to the fact layer
(catalog/Neo4j/Qdrant/knowledge-base). ``gpa_bucket`` and ``rank_bucket`` are
bucketed enums — raw GPA/rank values MUST NOT be accepted or logged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from dext_grounded.rules import GroundedRules, load_grounded_rules


# Heuristics for values that look like raw GPA/rank rather than a bucket enum.
# A bucket is a short alphabetic token (top10/high/medium/...); anything that
# carries a number, a slash-ratio, a "%", or a "rank"/"GPA" prefix is a raw
# value and must be rejected (spec §2.1).
_RAW_VALUE_PATTERNS = (
    re.compile(r"\d"),            # any digit → numeric raw value
    re.compile(r"/"),             # ratio like 3.9/4.0
    re.compile(r"%"),             # percentage
    re.compile(r"rank", re.IGNORECASE),
    re.compile(r"gpa", re.IGNORECASE),
    re.compile(r"#"),             # rank #12
)


def _looks_like_raw_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _RAW_VALUE_PATTERNS)


def _validate_bucket(
    field_name: str, value: str | None, allowed: frozenset[str],
) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty bucket string")
    if value in allowed:
        return
    # Not a legal bucket enum. Distinguish a raw value (number/ratio/percent)
    # from an unknown token so the error message points at the real problem.
    if _looks_like_raw_value(value):
        raise ValueError(
            f"{field_name} rejected raw value {value!r}; pass a bucket enum "
            f"from {sorted(allowed)}"
        )
    raise ValueError(
        f"{field_name} {value!r} is not in the legal bucket enum {sorted(allowed)}"
    )


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
        rules = load_grounded_rules()
        # research_interests default must remain a fresh list per-instance
        if self.research_interests is None:
            object.__setattr__(self, "research_interests", [])
        _validate_bucket("gpa_bucket", self.gpa_bucket, rules.gpa_buckets)
        _validate_bucket("rank_bucket", self.rank_bucket, rules.rank_buckets)
        if self.profile_completeness is not None:
            pc = self.profile_completeness
            if not isinstance(pc, (int, float)) or isinstance(pc, bool):
                raise ValueError("profile_completeness must be a float in [0,1]")
            if pc < 0.0 or pc > 1.0:
                raise ValueError("profile_completeness must be in [0,1]")

    def safe_log_summary(self) -> dict[str, object]:
        """Return a redacted dict safe for recommendation logs.

        Per spec §2.1/§18: log only the coarse completeness bucket + whether
        profile was used, never raw profile text, GPA/rank buckets, research
        interests, or the raw ``profile_completeness`` float.
        """
        uses = any(
            v is not None and v != []
            for v in (
                self.education_stage, self.school, self.major, self.gpa_bucket,
                self.rank_bucket, self.achievements_summary,
                self.competition_experience_summary,
            )
        ) or bool(self.research_interests)
        bucket = load_grounded_rules().completeness_buckets.bucket_for(
            self.profile_completeness
        )
        return {
            "uses_profile": uses,
            "education_stage": self.education_stage,
            "completeness_bucket": bucket,
        }


__all__ = ["StudentContext"]
