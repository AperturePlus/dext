from __future__ import annotations

import hashlib

from dext_competition import CompetitionCard
from dext_competition.contracts.knowledge import Chunk
from dext_competition.recommend.explanation import (
    GroundedRecommendationReason,
    build_grounded_reasons,
)
from dext_competition.recommend.ranking import RankedCompetition
from dext_competition.recommend.recall import CompetitionCandidate
from dext_grounded import ContentClass, SourceRef



def _chunk(text: str) -> Chunk:
    return Chunk(
        "竞赛信息总览.md",
        "竞赛信息总览.md > 赛事",
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text,
    )


def _card(name: str, *, category: str, chunk: Chunk) -> CompetitionCard:
    return CompetitionCard(
        competition_id="cmp-test",
        display_name=name,
        category=category,
        tags=("算法",),
        summary="算法竞赛",
        internal_source_refs=(chunk.to_source_ref(),),
        in_2024_catalog=True,
    )


def test_fact_reason_without_source_is_downgraded_to_uncertain() -> None:
    reason = GroundedRecommendationReason("事实", ContentClass.FACT)
    assert reason.content_class == ContentClass.UNCERTAIN


def test_every_fact_reason_has_a_canonical_source_ref() -> None:
    chunk = _chunk("算法竞赛")
    card = _card("算法竞赛", category="计算机", chunk=chunk)
    ranked = RankedCompetition(
        candidate=CompetitionCandidate(card, 1.0, (chunk.to_source_ref(),)),
        score=0.9,
        score_components={"effort_fit_score": 0.8},
        fit_level="strong",
    )
    reasons = build_grounded_reasons(card, ranked)
    fact_reasons = [reason for reason in reasons if reason.content_class == ContentClass.FACT]
    assert fact_reasons
    assert all(reason.source_refs for reason in fact_reasons)
    assert all(isinstance(ref, SourceRef) for reason in fact_reasons for ref in reason.source_refs)
