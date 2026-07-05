"""Read-only competition-catalog port (C2 spec).

C2 owns the concrete extractor; C3/C4/C5/C6 consume this protocol.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from dext_competition.contracts.catalog import CompetitionCard, CompetitionCatalogManifest


@runtime_checkable
class CompetitionCatalogPort(Protocol):
    """Read-only access to the competition catalog built from C1 chunks."""

    def manifest(self) -> CompetitionCatalogManifest:
        ...

    async def get(self, competition_id: str) -> CompetitionCard | None:
        ...

    async def list_competitions(self) -> tuple[CompetitionCard, ...]:
        ...


__all__ = ["CompetitionCatalogPort"]
