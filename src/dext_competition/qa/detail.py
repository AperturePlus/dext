"""Deterministic competition detail mapping."""
from __future__ import annotations

from dext_competition.contracts.qa import CompetitionDetail
from dext_competition.ports.catalog import CompetitionCatalogPort
from dext_competition.qa.retrieval import freshness_notice_for_card


async def get_competition_detail(
    catalog: CompetitionCatalogPort,
    competition_id: str,
) -> CompetitionDetail:
    """Return a product detail DTO from C2 catalog data without LLM calls."""

    card = await catalog.get(competition_id)
    if card is None:
        raise KeyError(f"competition not found: {competition_id}")

    return CompetitionDetail(
        competition_id=card.competition_id,
        display_name=card.display_name,
        category=card.category,
        summary=card.summary,
        eligibility=card.eligibility,
        schedule=card.schedule,
        team_policy=card.team_policy,
        materials=card.materials,
        ai_compliance=card.ai_compliance,
        preparation_focus=card.preparation_focus,
        risk_flags=card.risk_flags,
        official_links=card.official_links,
        internal_source_refs=card.internal_source_refs,
        freshness_notice=freshness_notice_for_card(card),
    )


__all__ = ["get_competition_detail"]

