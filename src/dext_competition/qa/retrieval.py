"""Fact-bundle assembly for C4 detail, QA and comparison."""
from __future__ import annotations

from dataclasses import replace

from dext_competition.catalog import card_to_fact_bundle
from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionCard,
)
from dext_competition.contracts.knowledge import KnowledgeHit
from dext_competition.ports.catalog import CompetitionCatalogPort
from dext_competition.ports.knowledge import KnowledgeIndexPort
from dext_competition.qa.schemas import FRESHNESS_NOTICE
from dext_grounded import ContentClass, FactBundle, FactItem, SourceRef

FRESHNESS_FIELDS = frozenset({
    "eligibility",
    "schedule",
    "ai_compliance",
    "official_links",
    "in_2024_catalog",
})


def dedupe_source_refs(refs: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    """Deduplicate refs by citation identity while preserving first occurrence."""

    out: list[SourceRef] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        key = (ref.doc_path, ref.heading_path, ref.chunk_hash)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return tuple(out)


def evidence_status_for_card(card: CompetitionCard) -> str:
    """Map C2 field evidence to C4 grounded|partial|uncertain."""

    if not card.internal_source_refs:
        return "uncertain"
    statuses = {item.status for item in card.field_evidence}
    if CatalogEvidenceStatus.CONFLICT in statuses:
        return "partial"
    if CatalogEvidenceStatus.UNCERTAIN in statuses:
        return "partial"
    return "grounded"


def freshness_notice_for_card(card: CompetitionCard) -> str | None:
    """Return a current-year review notice for freshness-sensitive card fields."""

    notices: list[str] = []
    for item in card.field_evidence:
        if item.field in FRESHNESS_FIELDS and item.review_notice:
            notices.append(item.review_notice)
    if notices:
        return "；".join(dict.fromkeys(notices))
    if card.schedule or card.ai_compliance or card.eligibility or card.official_links:
        return FRESHNESS_NOTICE
    return None


def facts_from_hits(hits: tuple[KnowledgeHit, ...]) -> tuple[FactItem, ...]:
    """Convert C1 knowledge hits into shared grounded FactItems."""

    facts: list[FactItem] = []
    for hit in hits:
        facts.append(FactItem(
            field="knowledge_chunk",
            value=hit.chunk.text,
            content_class=ContentClass.FACT,
            source_refs=(hit.source_ref,),
        ))
    return tuple(facts)


def hit_refs(hits: tuple[KnowledgeHit, ...]) -> tuple[SourceRef, ...]:
    return dedupe_source_refs(tuple(hit.source_ref for hit in hits))


def merge_fact_bundles(
    *,
    build_id: str,
    subject_id: str,
    bundles: tuple[FactBundle, ...],
) -> FactBundle:
    """Merge bundles without losing source identity."""

    facts: list[FactItem] = []
    refs: list[SourceRef] = []
    for bundle in bundles:
        facts.extend(bundle.facts)
        refs.extend(bundle.source_refs)
    return FactBundle(
        build_id=build_id,
        subject_id=subject_id,
        facts=tuple(facts),
        source_refs=dedupe_source_refs(tuple(refs)),
    )


def bundle_from_hits(
    *,
    build_id: str,
    subject_id: str,
    hits: tuple[KnowledgeHit, ...],
) -> FactBundle:
    return FactBundle(
        build_id=build_id,
        subject_id=subject_id,
        facts=facts_from_hits(hits),
        source_refs=hit_refs(hits),
    )


def bundle_from_cards(
    *,
    cards: tuple[CompetitionCard, ...],
    knowledge_base_version: str,
    subject_id: str,
) -> FactBundle:
    """Build one bundle for a multi-card comparison."""

    bundles: list[FactBundle] = []
    for card in cards:
        bundle = card_to_fact_bundle(card, knowledge_base_version)
        prefixed = tuple(
            replace(fact, field=f"{card.competition_id}.{fact.field}")
            for fact in bundle.facts
        )
        bundles.append(replace(bundle, facts=prefixed))
    return merge_fact_bundles(
        build_id=knowledge_base_version,
        subject_id=subject_id,
        bundles=tuple(bundles),
    )


async def question_fact_bundle(
    *,
    question: str,
    catalog: CompetitionCatalogPort,
    index: KnowledgeIndexPort,
    competition_id: str | None = None,
    limit: int = 8,
) -> tuple[FactBundle, CompetitionCard | None, tuple[KnowledgeHit, ...]]:
    """Retrieve C1/C2 facts for a grounded QA request.

    This consumes only the public C1/C2 ports. It does not parse Markdown or
    reach into private index/catalog implementation objects.
    """

    if limit < 1:
        raise ValueError("limit must be at least 1")

    manifest = catalog.manifest()
    card = await catalog.get(competition_id) if competition_id else None
    query_text = question
    if card is not None:
        query_text = " ".join((
            question,
            card.display_name,
            card.category,
            " ".join(card.tags),
        ))
    hits = await index.query(query_text, limit=limit)
    bundles: list[FactBundle] = []
    if card is not None:
        bundles.append(card_to_fact_bundle(card, manifest.knowledge_base_version))
    bundles.append(bundle_from_hits(
        build_id=manifest.knowledge_base_version,
        subject_id=competition_id or "competition-qa",
        hits=hits,
    ))
    return (
        merge_fact_bundles(
            build_id=manifest.knowledge_base_version,
            subject_id=competition_id or "competition-qa",
            bundles=tuple(bundles),
        ),
        card,
        hits,
    )


__all__ = [
    "bundle_from_cards",
    "bundle_from_hits",
    "dedupe_source_refs",
    "evidence_status_for_card",
    "freshness_notice_for_card",
    "question_fact_bundle",
]

