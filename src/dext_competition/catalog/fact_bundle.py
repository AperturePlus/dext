"""CompetitionCard to shared grounded FactBundle mapping."""
from __future__ import annotations

from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionCard,
)
from dext_grounded import ContentClass, FactBundle, FactItem, SourceRef


def card_to_fact_bundle(card: CompetitionCard, knowledge_base_version: str) -> FactBundle:
    facts: list[FactItem] = []
    refs: list[SourceRef] = []
    seen: set[SourceRef] = set()
    for evidence in card.field_evidence:
        if not evidence.values:
            continue
        content_class = (
            ContentClass.FACT
            if evidence.status == CatalogEvidenceStatus.GROUNDED
            else ContentClass.UNCERTAIN
        )
        facts.append(FactItem(
            field=evidence.field,
            value=" / ".join(evidence.values),
            content_class=content_class,
            source_refs=evidence.source_refs,
        ))
        for ref in evidence.source_refs:
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)
    return FactBundle(
        build_id=knowledge_base_version,
        subject_id=card.competition_id,
        facts=tuple(facts),
        source_refs=tuple(refs),
    )


__all__ = ["card_to_fact_bundle"]
