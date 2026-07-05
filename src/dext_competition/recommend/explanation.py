"""RecommendedCompetition card assembly."""
from __future__ import annotations

from dataclasses import dataclass

from dext_competition.contracts.catalog import CatalogEvidenceStatus, CompetitionCard
from dext_competition.contracts.recommend import RecommendedCompetition
from dext_competition.recommend.profile import CompetitionRankingProfile
from dext_competition.recommend.ranking import RankedCompetition
from dext_grounded import ContentClass, SourceRef


@dataclass(frozen=True, slots=True)
class GroundedRecommendationReason:
    text: str
    content_class: ContentClass
    source_refs: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_refs", tuple(self.source_refs or ()))
        if not self.text.strip():
            raise ValueError("recommendation reason text must be non-empty")
        if self.content_class == ContentClass.FACT and not self.source_refs:
            object.__setattr__(self, "content_class", ContentClass.UNCERTAIN)


def _review_notice(card: CompetitionCard, profile: CompetitionRankingProfile) -> str | None:
    for evidence in card.field_evidence:
        if evidence.status != CatalogEvidenceStatus.GROUNDED and evidence.review_notice:
            return evidence.review_notice
    if any(field in (card.schedule, card.ai_compliance) for field in ("往届", "2024 年报名", "2024年报名")):
        return profile.freshness_notice
    return profile.freshness_notice if card.schedule or card.ai_compliance else None


def _evidence_status(card: CompetitionCard) -> str:
    if not card.internal_source_refs:
        return "uncertain"
    statuses = {evidence.status for evidence in card.field_evidence}
    if CatalogEvidenceStatus.CONFLICT in statuses:
        return "partial"
    if CatalogEvidenceStatus.UNCERTAIN in statuses:
        return "partial"
    return "grounded"


def _dedupe_refs(refs) -> tuple[SourceRef, ...]:
    result: list[SourceRef] = []
    seen: set[SourceRef] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            result.append(ref)
    return tuple(result)


def build_grounded_reasons(
    card: CompetitionCard,
    ranked: RankedCompetition,
) -> tuple[GroundedRecommendationReason, ...]:
    refs = _dedupe_refs((*ranked.candidate.matched_source_refs, *card.internal_source_refs))
    reasons = [
        GroundedRecommendationReason(
            f"方向匹配：{card.category}" + (f"（{', '.join(card.tags[:3])}）" if card.tags else ""),
            ContentClass.FACT,
            refs,
        ),
    ]
    if card.in_2024_catalog:
        reasons.append(GroundedRecommendationReason(
            "入选 2024 竞赛分析报告目录；学校认定仍需查本校文件。",
            ContentClass.FACT,
            refs,
        ))
    else:
        reasons.append(GroundedRecommendationReason(
            "不在 2024 竞赛分析报告目录内；如关注校内认定，需先查本校文件。",
            ContentClass.FACT,
            refs,
        ))
    if ranked.score_components.get("effort_fit_score", 0.0) >= 0.7:
        reasons.append(GroundedRecommendationReason(
            "备赛投入与当前时间预算较匹配。",
            ContentClass.ADVICE,
        ))
    return tuple(reasons)


def assemble_recommended_competition(
    ranked: RankedCompetition,
    *,
    profile: CompetitionRankingProfile,
) -> RecommendedCompetition:
    card = ranked.candidate.card
    reasons = build_grounded_reasons(card, ranked)
    refs = _dedupe_refs(ref for reason in reasons for ref in reason.source_refs)
    return RecommendedCompetition(
        competition_id=card.competition_id,
        display_name=card.display_name,
        category=card.category,
        summary=card.summary,
        fit_level=ranked.fit_level,
        score=ranked.score,
        score_components=ranked.score_components,
        eligibility_notes=card.eligibility,
        schedule_notes=card.schedule,
        team_notes=card.team_policy or "未明确，需复核当届规则。",
        preparation_effort="; ".join(card.preparation_focus[:2]) if card.preparation_focus else "需结合目标拆解备赛任务。",
        short_reasons=tuple(reason.text for reason in reasons),
        risk_flags=card.risk_flags,
        official_links=card.official_links,
        evidence_status=_evidence_status(card),
        freshness_notice=_review_notice(card, profile),
        internal_source_refs=refs,
        available_actions=("detail", "create_plan", "ask_rules", "compare"),
    )


__all__ = [
    "GroundedRecommendationReason",
    "assemble_recommended_competition",
    "build_grounded_reasons",
]
