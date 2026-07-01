"""Fact-citation reference units (grounded-generation spec §2.2, §4.2).

``SourceRef`` points to catalog/Neo4j evidence (recommend) or Markdown chunks
(competition). ``UserContextRef`` references a StudentContext field — it is NOT
a fact-bundle source; raw GPA/rank are never stored, only redacted buckets.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_grounded.rules import load_grounded_rules
from dext_grounded.student_context import _looks_like_raw_value

QUOTE_MAX_LEN = load_grounded_rules().quote_max_len


def _truncate(value: str, limit: int = QUOTE_MAX_LEN) -> str:
    return value[:limit]


@dataclass(frozen=True, slots=True)
class SourceRef:
    doc_path: str
    heading_path: str
    chunk_hash: str
    quote_or_summary: str
    official_url: str | None = None
    last_verified: str | None = None  # ISO-8601

    def __post_init__(self) -> None:
        object.__setattr__(self, "quote_or_summary", _truncate(self.quote_or_summary))


@dataclass(frozen=True, slots=True)
class UserContextRef:
    field: str                       # StudentContext field name
    value_bucket: str | None        # redacted bucket, never raw GPA/rank
    quote_or_summary: str

    def __post_init__(self) -> None:
        if self.value_bucket is not None:
            if not isinstance(self.value_bucket, str) or not self.value_bucket:
                raise ValueError("value_bucket must be a non-empty redacted bucket")
            rules = load_grounded_rules()
            legal_buckets = (
                rules.gpa_buckets
                | rules.rank_buckets
                | frozenset(name for name, _ in rules.completeness_buckets.thresholds)
            )
            if self.value_bucket not in legal_buckets and _looks_like_raw_value(
                self.value_bucket
            ):
                raise ValueError("value_bucket must not contain a raw GPA/rank value")
        object.__setattr__(self, "quote_or_summary", _truncate(self.quote_or_summary))


__all__ = ["SourceRef", "UserContextRef", "QUOTE_MAX_LEN"]
