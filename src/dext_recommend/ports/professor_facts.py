"""ProfessorFactPort — assemble ProfessorDetail / hydrate facts (R4)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dext_grounded import SourceRef
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem

from dext_recommend._immutable import freeze_mapping
from dext_recommend.readiness import ActiveBuildSnapshot


class ProfessorFactNotFound(LookupError):
    """Raised by get_detail when the entity is missing/inactive/excluded."""
    def __init__(self, entity_id: str, build_id: str) -> None:
        self.entity_id = entity_id
        self.build_id = build_id
        super().__init__(f"professor fact not found: entity={entity_id} build={build_id}")


@dataclass(frozen=True, slots=True)
class ViewerPermissions:
    include_contacts: bool = False
    can_view_review: bool = False
    diagnostics: bool = False


@dataclass(frozen=True, slots=True)
class ProfessorFact:
    entity_id: str
    display_name: str
    university: str
    org_units: tuple[str, ...]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    profile_hash: str | None
    research_summary: str | None
    # authority fields for hard filters (R3); display fields above are for cards only
    university_id: str | None = None
    city_name: str | None = None
    org_unit_ids: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "org_units", tuple(self.org_units or ()))
        object.__setattr__(self, "org_unit_ids", tuple(self.org_unit_ids or ()))
        object.__setattr__(self, "topic_ids", tuple(self.topic_ids or ()))


@dataclass(frozen=True, slots=True)
class ProfessorDetail:
    build_id: str
    profile_hash: str | None
    entity_id: str
    display_name: str
    university: str
    org_units: tuple[str, ...]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_statements: tuple[str, ...]
    approved_topics: tuple[str, ...]
    selected_publication_mentions: tuple[str, ...]
    bio_snippets: tuple[str, ...]
    source_urls: tuple[str, ...]
    provenance_refs: tuple[SourceRef, ...]
    quality_findings: tuple[str, ...]
    risk_flags: tuple[str, ...]
    fact_bundle: FactBundle                          # R6 sole fact input, required
    contacts: Mapping[str, str] = field(default_factory=dict)   # gated by viewer perms

    def __post_init__(self) -> None:
        for name in (
            "org_units",
            "research_statements",
            "approved_topics",
            "selected_publication_mentions",
            "bio_snippets",
            "source_urls",
            "provenance_refs",
            "quality_findings",
            "risk_flags",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        object.__setattr__(self, "contacts", freeze_mapping(self.contacts))


@runtime_checkable
class ProfessorFactPort(Protocol):
    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail: ...

    async def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]: ...


__all__ = [
    "ContentClass", "FactBundle", "FactItem", "ProfessorDetail",
    "ProfessorFact", "ProfessorFactNotFound", "ProfessorFactPort",
    "ViewerPermissions",
]
