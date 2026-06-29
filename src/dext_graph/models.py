"""Small serializable value objects used by the value-validation slice."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceInfo:
    path: str
    sha256: str
    university: str
    abbreviation: str | None
    crawl_status: str
    row_counts: dict[str, int]
    coverage: dict[str, int]

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceProfessor:
    source_id: int
    source_row_key: str
    point_id: str
    name: str
    university: str
    org_units: tuple[str, ...]
    title: str | None
    research_areas: str | None
    publications: str | None
    bio: str | None


@dataclass(frozen=True)
class ProfileRecord:
    source_id: int
    source_row_key: str
    point_id: str
    name: str
    university: str
    org_units: tuple[str, ...]
    title: str | None
    template_version: str
    normalized_profile: str
    profile_hash: str
    token_count: int
    research_areas: str | None = None
    publications: str | None = None
    bio: str | None = None

    def asdict(self) -> dict[str, Any]:
        value = asdict(self)
        value["org_units"] = list(self.org_units)
        return value


@dataclass(frozen=True)
class QueryRecord:
    query_id: str
    text: str
    categories: tuple[str, ...]
    disciplines: tuple[str, ...]


@dataclass
class RequestMetric:
    request_id: str
    purpose: str
    attempt: int
    item_count: int
    status_code: int | None
    latency_ms: float
    trace_id: str | None = None
    prompt_tokens: int | None = None
    total_tokens: int | None = None
    retry_after_seconds: float | None = None
    error_kind: str | None = None
    succeeded: bool = False

    def asdict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    usage: dict[str, int] = field(default_factory=dict)


class ValueValidationError(RuntimeError):
    """A user-actionable, secret-free experiment failure."""


__all__ = [
    "EmbeddingResult",
    "ProfileRecord",
    "QueryRecord",
    "RequestMetric",
    "SourceInfo",
    "SourceProfessor",
    "ValueValidationError",
]
