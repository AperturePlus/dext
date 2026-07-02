"""CatalogProfessorFactAdapter: implements ProfessorFactPort over the
published catalog SQLite (R4). Two methods: hydrate (batch authority facts)
and get_detail (single-entity full ProfessorDetail + FactBundle).

Never imports dext_graph; reads only the pinned build_id. Contacts are
double-gated and never enter the FactBundle. No cache (R7 decorator if ever).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dext_grounded import SourceRef
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.content import ContentClass

from dext_recommend.adapters._catalog_fact_reader import (
    CatalogProfessorFactReader, CatalogProfessorDetailRows,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids
from dext_recommend.facts.evidence import (
    build_fact_items, select_publication_snippets, select_statement_snippets,
)
from dext_recommend.facts.source_urls import dedupe_source_urls
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactNotFound, ViewerPermissions,
)
from dext_recommend.readiness import ActiveBuildSnapshot


def _fact_from_row(row: dict[str, Any]) -> ProfessorFact:
    return ProfessorFact(
        entity_id=str(row["entity_id"]),
        display_name=str(row["display_name"] or ""),
        university=str(row.get("university_id") or ""),
        org_units=tuple(row.get("org_unit_ids") or ()),
        title=str(row.get("title_family") or ""),
        title_family=str(row.get("title_family") or ""),
        master_eligibility=str(row.get("master_eligibility") or "unknown"),
        phd_eligibility=str(row.get("phd_eligibility") or "unknown"),
        role_status=str(row.get("role_status") or "included"),
        profile_url=row.get("profile_url"),
        profile_hash=row.get("profile_hash"),
        research_summary=None,
        university_id=row.get("university_id"),
        city_name=row.get("city_name"),
        org_unit_ids=tuple(row.get("org_unit_ids") or ()),
        topic_ids=tuple(row.get("topic_ids") or ()),
    )


class CatalogProfessorFactAdapter:
    def __init__(
        self, reader: CatalogProfessorFactReader, *, settings: RecommendSettings,
    ) -> None:
        self._reader = reader
        self._settings = settings

    async def hydrate(
        self, snapshot: ActiveBuildSnapshot, entity_ids: list[str],
    ) -> dict[str, ProfessorFact]:
        if not entity_ids:
            return {}
        ordered = dedupe_entity_ids(entity_ids)
        chunks = chunk_entity_ids(ordered, self._settings.fact_chunk_size)
        out: dict[str, ProfessorFact] = {}
        for chunk in chunks:
            rows = await self._reader.read_fact_rows(snapshot.build_id, list(chunk))
            for row in rows:
                fact = _fact_from_row(dict(row))
                out[fact.entity_id] = fact
        return out

    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        raise NotImplementedError  # filled by Task 12


__all__ = ["CatalogProfessorFactAdapter"]
