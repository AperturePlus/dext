"""Catalog + knowledge-index recall for C3."""
from __future__ import annotations

from dataclasses import dataclass

from dext_competition.contracts.catalog import CompetitionCard
from dext_competition.contracts.knowledge import KnowledgeHit
from dext_competition.contracts.recommend import CompetitionQueryUnderstanding
from dext_competition.ports import CompetitionCatalogPort, KnowledgeIndexPort
from dext_grounded import SourceRef


@dataclass(frozen=True, slots=True)
class CompetitionCandidate:
    card: CompetitionCard
    recall_score: float
    matched_source_refs: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "matched_source_refs",
            tuple(self.matched_source_refs) if self.matched_source_refs is not None else (),
        )


def _text_blob(card: CompetitionCard) -> str:
    return " ".join((
        card.display_name,
        card.category,
        " ".join(card.tags),
        card.summary,
        card.eligibility,
        card.schedule,
        card.team_policy,
        " ".join(card.preparation_focus),
    )).casefold()


def _source_match_score(card: CompetitionCard, hits: tuple[KnowledgeHit, ...]) -> tuple[float, tuple[SourceRef, ...]]:
    if not hits:
        return 0.0, ()
    hit_scores = {hit.source_ref.chunk_hash: hit.score for hit in hits}
    matched: list[SourceRef] = []
    score = 0.0
    for ref in card.internal_source_refs:
        value = hit_scores.get(ref.chunk_hash)
        if value is not None:
            matched.append(ref)
            score += float(value)
    if not matched:
        return 0.0, ()
    max_possible = max(sum(max(hit.score, 0.0) for hit in hits), 1.0)
    return min(score / max_possible, 1.0), tuple(matched)


def _lexical_score(card: CompetitionCard, terms: tuple[str, ...]) -> float:
    if not terms:
        return 0.0
    blob = _text_blob(card)
    matched = sum(1 for term in terms if term.casefold() in blob)
    return matched / max(len(terms), 1)


async def recall_candidates(
    *,
    catalog: CompetitionCatalogPort,
    knowledge_index: KnowledgeIndexPort,
    understanding: CompetitionQueryUnderstanding,
    query_text: str,
    limit: int,
) -> tuple[CompetitionCandidate, ...]:
    cards = await catalog.list_competitions()
    terms = tuple(term for term in (*understanding.interests, query_text) if str(term).strip())
    hits = await knowledge_index.query(" ".join(terms), limit=max(limit, 1))
    candidates: list[CompetitionCandidate] = []
    for card in cards:
        source_score, refs = _source_match_score(card, hits)
        lexical = _lexical_score(card, terms)
        score = max(source_score, lexical)
        if score > 0.0:
            candidates.append(CompetitionCandidate(card, score, refs or card.internal_source_refs[:1]))
    if not candidates:
        candidates = [
            CompetitionCandidate(card, 0.05, card.internal_source_refs[:1])
            for card in cards
        ]
    candidates.sort(key=lambda item: (-item.recall_score, item.card.display_name, item.card.competition_id))
    return tuple(candidates[: max(limit, 1)])


__all__ = ["CompetitionCandidate", "recall_candidates"]
