"""Grounded comparison for two to four competitions."""
from __future__ import annotations

from dext_competition.contracts.catalog import CompetitionCard
from dext_competition.contracts.qa import CompetitionComparison
from dext_competition.ports.catalog import CompetitionCatalogPort
from dext_competition.qa.answer import _evidence_status, _output_text, support_map_validator
from dext_competition.qa.retrieval import bundle_from_cards, dedupe_source_refs
from dext_competition.qa.schemas import (
    COMPARE_JSON_SCHEMA,
    COMPARE_SYSTEM_PROMPT_ID,
    FRESHNESS_NOTICE,
    QA_GENERATION_PROFILE_VERSION,
    SAFETY_DOMAIN,
)
from dext_grounded import ConstrainedGenerationPipeline, StudentContext


def _dimension_value(card: CompetitionCard, name: str) -> str:
    value = getattr(card, name)
    if isinstance(value, tuple):
        return "、".join(str(item) for item in value)
    return str(value)


def _dimensions(cards: tuple[CompetitionCard, ...]) -> tuple[tuple[str, str, str], ...]:
    out: list[tuple[str, str, str]] = []
    for name in ("category", "eligibility", "schedule", "team_policy", "preparation_focus"):
        first = _dimension_value(cards[0], name)
        rest = " | ".join(_dimension_value(card, name) for card in cards[1:])
        out.append((name, first, rest))
    return tuple(out)


def _deterministic_summary(cards: tuple[CompetitionCard, ...]) -> str:
    names = "、".join(card.display_name for card in cards)
    categories = "、".join(dict.fromkeys(card.category for card in cards))
    return f"{names} 覆盖 {categories} 等方向；具体报名与校内认定需复核当届文件。"


async def compare_competitions(
    *,
    competition_ids: tuple[str, ...],
    catalog: CompetitionCatalogPort,
    pipeline: ConstrainedGenerationPipeline,
    student_context: StudentContext | None = None,
    include_contacts: bool = False,
    generation_profile_version: str = QA_GENERATION_PROFILE_VERSION,
) -> CompetitionComparison:
    """Compare two to four competitions through the shared grounded pipeline."""

    ids = tuple(competition_ids)
    if not 2 <= len(ids) <= 4:
        raise ValueError("compare_competitions requires 2 to 4 competition_ids")
    if len(set(ids)) != len(ids):
        raise ValueError("competition_ids must be unique")

    cards: list[CompetitionCard] = []
    for competition_id in ids:
        card = await catalog.get(competition_id)
        if card is None:
            raise KeyError(f"competition not found: {competition_id}")
        cards.append(card)
    card_tuple = tuple(cards)

    manifest = catalog.manifest()
    bundle = bundle_from_cards(
        cards=card_tuple,
        knowledge_base_version=manifest.knowledge_base_version,
        subject_id="compare:" + ",".join(ids),
    )
    result = await pipeline.generate(
        system_prompt_id=COMPARE_SYSTEM_PROMPT_ID,
        user_inputs={
            "competition_ids": ids,
            "display_names": tuple(card.display_name for card in card_tuple),
        },
        fact_bundle=bundle,
        student_context=student_context,
        json_schema=COMPARE_JSON_SCHEMA,
        generation_profile_version=generation_profile_version,
        safety_domain=SAFETY_DOMAIN,
        include_contacts=include_contacts,
        operation_id="competition_compare",
        support_validator=support_map_validator,
    )

    summary = _output_text(result.output, key="summary") or _deterministic_summary(card_tuple)
    if _evidence_status(result) != "grounded" and FRESHNESS_NOTICE not in summary:
        summary = f"{summary}\n{FRESHNESS_NOTICE}"
    refs = tuple(result.cited_refs) or dedupe_source_refs(bundle.source_refs)
    return CompetitionComparison(
        competition_ids=ids,
        summary=summary,
        dimensions=_dimensions(card_tuple),
        internal_source_refs=refs,
    )


__all__ = ["compare_competitions"]

