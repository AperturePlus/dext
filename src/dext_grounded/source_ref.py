"""Fact-citation reference units (grounded-generation spec §2.2, §4.2).

``SourceRef`` points to catalog/Neo4j evidence (recommend) or Markdown chunks
(competition). ``UserContextRef`` references a StudentContext field — it is NOT
a fact-bundle source; raw GPA/rank are never stored, only redacted buckets.
"""
from __future__ import annotations

from dataclasses import dataclass

QUOTE_MAX_LEN = 500


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
        object.__setattr__(self, "quote_or_summary", _truncate(self.quote_or_summary))


__all__ = ["SourceRef", "UserContextRef", "QUOTE_MAX_LEN"]
