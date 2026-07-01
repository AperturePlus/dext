"""Raw R2 release observations; these ports never consume a validated snapshot."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


class ReadinessSourceError(RuntimeError):
    """Safe, structured failure raised by a raw readiness source adapter."""

    def __init__(self, source: str, reason: str, *, retryable: bool = False) -> None:
        safe_source = str(source).replace("\r", " ").replace("\n", " ").strip()[:80]
        safe_reason = str(reason).replace("\r", " ").replace("\n", " ").strip()
        safe_reason = re.sub(r"(://[^:/\s]+:)[^@\s]+@", r"\1***@", safe_reason)
        safe_reason = re.sub(
            r"(?i)(api[_-]?key|password|token)\s*[:=]\s*[^,;\s]+",
            r"\1=***",
            safe_reason,
        )[:500]
        self.source = safe_source or "unknown"
        self.reason = safe_reason or "readback failed"
        self.retryable = bool(retryable)
        super().__init__(f"{self.source} readiness readback failed: {self.reason}")


@dataclass(frozen=True, slots=True)
class ProfessorReleaseSample:
    entity_id: str
    org_unit_ids: tuple[str, ...] = ()
    profile_hash: str | None = None
    role_status: str | None = None
    master_eligibility: str | None = None
    phd_eligibility: str | None = None
    embedding_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "org_unit_ids", tuple(self.org_unit_ids or ()))


@dataclass(frozen=True, slots=True)
class PayloadCoverageObservation:
    field: str
    covered: float
    sample_size: int
    invalid_count: int = 0
    mismatch_count: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.covered <= 1.0:
            raise ValueError("covered must be in [0.0, 1.0]")
        if min(self.sample_size, self.invalid_count, self.mismatch_count) < 0:
            raise ValueError("coverage counts must be non-negative")


@dataclass(frozen=True, slots=True)
class CatalogReleaseObservation:
    build_id: str
    catalog_schema_version: int
    qdrant_payload_schema_version: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_fingerprint: str
    taxonomy_version: str | None
    expected_professor_count: int
    sample_entity_ids: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.expected_professor_count < 0:
            raise ValueError("expected_professor_count must be non-negative")
        object.__setattr__(self, "sample_entity_ids", tuple(self.sample_entity_ids or ()))


@dataclass(frozen=True, slots=True)
class VectorReleaseObservation:
    alias: str
    target_collection: str
    build_id: str
    payload_schema_version: int
    embedding_fingerprint: str
    embedding_dimension: int
    point_count: int
    samples: tuple[ProfessorReleaseSample, ...] = ()
    coverage: tuple[PayloadCoverageObservation, ...] = ()

    def __post_init__(self) -> None:
        if self.point_count < 0:
            raise ValueError("point_count must be non-negative")
        object.__setattr__(self, "samples", tuple(self.samples or ()))
        object.__setattr__(self, "coverage", tuple(self.coverage or ()))


@dataclass(frozen=True, slots=True)
class GraphReleaseObservation:
    build_id: str
    samples: tuple[ProfessorReleaseSample, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "samples", tuple(self.samples or ()))


@runtime_checkable
class CatalogReleasePort(Protocol):
    async def read_active(self) -> CatalogReleaseObservation | None: ...

    async def read_samples(
        self, build_id: str, sample_ids: tuple[str, ...],
    ) -> tuple[ProfessorReleaseSample, ...]: ...


@runtime_checkable
class VectorReleasePort(Protocol):
    async def read_current(
        self, alias: str, sample_ids: tuple[str, ...],
    ) -> VectorReleaseObservation | None: ...


@runtime_checkable
class GraphReleasePort(Protocol):
    async def read_active(
        self, sample_ids: tuple[str, ...],
    ) -> GraphReleaseObservation | None: ...


@runtime_checkable
class RankingProfilePort(Protocol):
    async def read_version(self, path: Path) -> str: ...


__all__ = [
    "CatalogReleaseObservation",
    "CatalogReleasePort",
    "GraphReleaseObservation",
    "GraphReleasePort",
    "PayloadCoverageObservation",
    "ProfessorReleaseSample",
    "RankingProfilePort",
    "ReadinessSourceError",
    "VectorReleaseObservation",
    "VectorReleasePort",
]
