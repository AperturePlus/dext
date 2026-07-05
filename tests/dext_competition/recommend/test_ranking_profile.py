from __future__ import annotations

import hashlib

from dext_competition import (
    CatalogEvidenceStatus,
    CompetitionCard,
    CompetitionFieldEvidence,
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
)
from dext_competition.contracts.knowledge import Chunk
from dext_competition.recommend.profile import CompetitionRankingProfile, load_ranking_profile
from dext_competition.recommend.ranking import rank_candidates
from dext_competition.recommend.recall import CompetitionCandidate


def _chunk(text: str) -> Chunk:
    return Chunk(
        doc_path="竞赛信息总览.md",
        heading_path="竞赛信息总览.md > 赛事",
        chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
    )


def _card(name: str, *, category: str, chunk: Chunk,
          in_2024_catalog: bool, summary: str) -> CompetitionCard:
    ref = chunk.to_source_ref()
    evidence = (
        CompetitionFieldEvidence("display_name", (name,), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("category", (category,), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("summary", (summary,), CatalogEvidenceStatus.GROUNDED, (ref,)),
    )
    return CompetitionCard(
        competition_id="cmp_" + hashlib.sha1(name.encode("utf-8")).hexdigest(),
        display_name=name,
        category=category,
        tags=("算法",) if "算法" in summary else (),
        summary=summary,
        internal_source_refs=(ref,),
        in_2024_catalog=in_2024_catalog,
        field_evidence=evidence,
    )


def _profile(version: str, *, interest: float, provenance: float) -> CompetitionRankingProfile:
    return CompetitionRankingProfile(
        version=version,
        weights={
            "interest_match_score": interest,
            "eligibility_score": 0.0,
            "effort_fit_score": 0.0,
            "timeline_fit_score": 0.0,
            "goal_fit_score": 0.0,
            "provenance_score": provenance,
        },
        fit_thresholds={"strong": 0.8, "good": 0.6, "exploratory": 0.4},
        tie_break=("score_desc", "in_2024_catalog_desc", "display_name_asc", "competition_id_asc"),
        recall_top_k=10,
        freshness_notice="复核当届规则",
    )


def test_default_ranking_profile_loads_from_artifact():
    profile = load_ranking_profile()
    assert profile.version == "competition.ranking.v1"
    assert profile.weights["interest_match_score"] == 0.3
    assert profile.fit_level(0.8) == "strong"


def test_ranking_profile_changes_observable_order():
    c1 = _chunk("算法 程序设计")
    c2 = _chunk("综合创业")
    specific = _card(
        "算法专项赛",
        category="计算机",
        chunk=c1,
        in_2024_catalog=False,
        summary="算法专项训练",
    )
    catalogued = _card(
        "综合创业赛",
        category="综合与创业",
        chunk=c2,
        in_2024_catalog=True,
        summary="综合创业实践",
    )
    candidates = (
        CompetitionCandidate(specific, 1.0, specific.internal_source_refs),
        CompetitionCandidate(catalogued, 0.1, catalogued.internal_source_refs),
    )
    understanding = CompetitionQueryUnderstanding(interests=("算法",), confidence=0.8)
    interest_first = rank_candidates(
        candidates,
        preferences=CompetitionPreferences(),
        understanding=understanding,
        profile=_profile("interest-heavy", interest=1.0, provenance=0.0),
    )
    provenance_first = rank_candidates(
        candidates,
        preferences=CompetitionPreferences(),
        understanding=understanding,
        profile=_profile("provenance-heavy", interest=0.0, provenance=1.0),
    )
    assert interest_first[0].candidate.card.display_name == "算法专项赛"
    assert provenance_first[0].candidate.card.display_name == "综合创业赛"
