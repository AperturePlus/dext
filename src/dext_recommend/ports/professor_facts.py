"""ProfessorFactPort — assemble ProfessorDetail / hydrate facts (R4 fills)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dext_grounded import SourceRef

from dext_recommend.readiness import ActiveBuildSnapshot


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
    org_units: list[str]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    profile_hash: str | None
    research_summary: str | None


@dataclass(frozen=True, slots=True)
class ProfessorDetail:
    build_id: str
    profile_hash: str | None
    entity_id: str
    display_name: str
    university: str
    org_units: list[str]
    title: str
    title_family: str
    master_eligibility: str
    phd_eligibility: str
    role_status: str
    profile_url: str | None
    research_statements: list[str]
    approved_topics: list[str]
    selected_publication_mentions: list[str]
    bio_snippets: list[str]
    source_urls: list[str]
    provenance_refs: list[SourceRef]
    quality_findings: list[str]
    risk_flags: list[str]
    contacts: dict[str, str] = field(default_factory=dict)   # gated by viewer perms


@runtime_checkable
class ProfessorFactPort(Protocol):
    def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail: ...

    def hydrate(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
    ) -> dict[str, ProfessorFact]: ...


__all__ = ["ProfessorDetail", "ProfessorFact", "ProfessorFactPort", "ViewerPermissions"]
