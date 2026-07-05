"""Competition card contract (C2 spec §3).

Maps to shared ``FactBundle`` (build_id=knowledge_base_version,
subject_id=competition_id). ``internal_source_refs`` are backend-only;
the public API never returns doc_path/heading_path/chunk_hash unless
diagnostics/debug/admin mode is on (overview §7).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from enum import Enum

from dext_grounded import SourceRef


class CompetitionCategory(str, Enum):
    COMPUTER = "计算机"
    ELECTRONIC_INFO = "电子与信息"
    ROBOTICS_AI = "机器人与人工智能"
    ENGINEERING = "工学"
    SCIENCE = "理学"
    MEDICAL_LIFE = "医学与生命科学"
    MANAGEMENT = "经管"
    LANGUAGE_ART = "语言与艺术"
    COMPREHENSIVE_ENTREPRENEURSHIP = "综合与创业"
    MATH_MODELING = "数学建模"


class CatalogEvidenceStatus(str, Enum):
    GROUNDED = "grounded"
    UNCERTAIN = "uncertain"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class CompetitionFieldEvidence:
    """All retained alternatives and citations for one catalog field."""

    field: str
    values: tuple[str, ...]
    status: CatalogEvidenceStatus
    source_refs: tuple[SourceRef, ...] = ()
    review_notice: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values or ()))
        object.__setattr__(self, "source_refs", tuple(self.source_refs or ()))


@dataclass(frozen=True, slots=True)
class CompetitionCatalogManifest:
    version_id: str
    knowledge_base_version: str
    rules_version: str
    card_count: int
    in_2024_catalog_count: int
    content_hash: str
    generated_at: datetime.datetime


@dataclass(frozen=True, slots=True)
class CompetitionCard:
    competition_id: str
    display_name: str
    category: str
    tags: tuple[str, ...] = ()
    summary: str = ""
    eligibility: str = ""
    schedule: str = ""
    team_policy: str = ""        # solo/team/either
    materials: tuple[str, ...] = ()
    ai_compliance: str = ""
    preparation_focus: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    official_links: tuple[str, ...] = ()
    internal_source_refs: tuple[SourceRef, ...] = ()
    in_2024_catalog: bool = False
    last_verified: str | None = None
    field_evidence: tuple[CompetitionFieldEvidence, ...] = ()

    def __post_init__(self) -> None:
        for f in ("tags", "materials", "preparation_focus", "risk_flags",
                  "official_links", "internal_source_refs", "field_evidence"):
            object.__setattr__(
                self, f,
                tuple(getattr(self, f)) if getattr(self, f) is not None else (),
            )


__all__ = [
    "CatalogEvidenceStatus",
    "CompetitionCard",
    "CompetitionCatalogManifest",
    "CompetitionCategory",
    "CompetitionFieldEvidence",
]
