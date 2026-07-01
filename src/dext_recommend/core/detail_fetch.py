# src/dext_recommend/core/detail_fetch.py
"""Bounded fan-out of ProfessorFactPort.get_detail over the detail rerank window.

get_detail is single-entity by protocol; core never calls a non-existent
batch method. Missing details return None; callers mark weak_explanation.
"""
from __future__ import annotations

import asyncio

from dext_recommend.ports.professor_facts import ProfessorDetail, ProfessorFactPort, ViewerPermissions
from dext_recommend.readiness import ActiveBuildSnapshot


async def _fetch_one(
    facts_port: ProfessorFactPort,
    snapshot: ActiveBuildSnapshot,
    entity_id: str,
    include_contacts: bool,
    viewer_permissions: ViewerPermissions,
) -> ProfessorDetail | None:
    try:
        return await facts_port.get_detail(
            snapshot, entity_id, include_contacts, viewer_permissions,
        )
    except (KeyError, LookupError):
        return None


async def fetch_details(
    snapshot: ActiveBuildSnapshot,
    facts_port: ProfessorFactPort,
    entity_ids: list[str],
    *,
    include_contacts: bool,
    viewer_permissions: ViewerPermissions,
    concurrency: int,
) -> dict[str, ProfessorDetail | None]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _guarded(eid: str) -> tuple[str, ProfessorDetail | None]:
        async with semaphore:
            return eid, await _fetch_one(
                facts_port, snapshot, eid, include_contacts, viewer_permissions,
            )

    pairs = await asyncio.gather(*(_guarded(eid) for eid in entity_ids))
    return {eid: detail for eid, detail in pairs}


__all__ = ["fetch_details"]
