"""Ranking and score components for C3 recommendations."""
from __future__ import annotations

from dataclasses import dataclass

from dext_competition.contracts.catalog import CompetitionCard
from dext_competition.contracts.recommend import (
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
)
from dext_competition.recommend.profile import CompetitionRankingProfile
from dext_competition.recommend.recall import CompetitionCandidate


@dataclass(frozen=True, slots=True)
class RankedCompetition:
    candidate: CompetitionCandidate
    score: float
    score_components: dict[str, float]
    fit_level: str


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(value and value.casefold() in folded for value in values)


def _interest_score(card: CompetitionCard, candidate: CompetitionCandidate,
                    understanding: CompetitionQueryUnderstanding) -> float:
    blob = " ".join((card.display_name, card.category, " ".join(card.tags), card.summary))
    if not understanding.interests:
        return min(candidate.recall_score, 1.0)
    matched = sum(1 for value in understanding.interests if value.casefold() in blob.casefold())
    lexical = matched / max(len(understanding.interests), 1)
    return min(max(candidate.recall_score, lexical), 1.0)


def _eligibility_score(card: CompetitionCard, prefs: CompetitionPreferences,
                       understanding: CompetitionQueryUnderstanding) -> float:
    hints = tuple(
        value for value in (
            prefs.major, prefs.grade, understanding.major_fit, understanding.grade_fit,
        )
        if value
    )
    if not hints:
        return 0.65 if card.eligibility else 0.45
    if _contains_any(card.eligibility + " " + card.summary, hints):
        return 1.0
    if card.eligibility:
        return 0.6
    return 0.35


def _effort_score(card: CompetitionCard, prefs: CompetitionPreferences,
                  understanding: CompetitionQueryUnderstanding) -> float:
    hours = prefs.weekly_hours if prefs.weekly_hours is not None else understanding.weekly_hours
    text = " ".join((card.summary, " ".join(card.preparation_focus), " ".join(card.risk_flags)))
    demanding = any(word in text for word in ("长期", "系统", "工程", "答辩", "项目", "论文", "高强度"))
    if hours is None:
        return 0.55 if demanding else 0.7
    if demanding:
        if hours >= 10:
            return 1.0
        if hours >= 5:
            return 0.72
        return 0.42
    if hours >= 3:
        return 0.9
    return 0.62


def _timeline_score(card: CompetitionCard, prefs: CompetitionPreferences) -> float:
    if not prefs.time_window:
        return 0.65 if card.schedule else 0.45
    if prefs.time_window.casefold() in card.schedule.casefold():
        return 1.0
    return 0.55 if card.schedule else 0.35


def _goal_score(card: CompetitionCard, prefs: CompetitionPreferences,
                understanding: CompetitionQueryUnderstanding) -> float:
    goal = prefs.target_goal or understanding.target_goal
    if not goal:
        return 0.65
    text = " ".join((card.summary, " ".join(card.preparation_focus), card.category))
    keywords = {
        "learn": ("基础", "入门", "学习", "训练", "能力"),
        "portfolio": ("作品", "项目", "代码", "设计", "展示"),
        "school_recognition": ("本校", "认定", "目录", "报告"),
        "research": ("论文", "研究", "建模", "实验", "数据"),
        "job_skill": ("工程", "实践", "开发", "创新", "创业"),
    }.get(goal, ())
    if _contains_any(text, keywords):
        return 1.0
    return 0.58


def _provenance_score(card: CompetitionCard) -> float:
    if not card.internal_source_refs:
        return 0.2
    if card.in_2024_catalog:
        return 1.0
    return 0.82


def _score(candidate: CompetitionCandidate, preferences: CompetitionPreferences,
           understanding: CompetitionQueryUnderstanding,
           profile: CompetitionRankingProfile) -> RankedCompetition:
    card = candidate.card
    components = {
        "interest_match_score": _interest_score(card, candidate, understanding),
        "eligibility_score": _eligibility_score(card, preferences, understanding),
        "effort_fit_score": _effort_score(card, preferences, understanding),
        "timeline_fit_score": _timeline_score(card, preferences),
        "goal_fit_score": _goal_score(card, preferences, understanding),
        "provenance_score": _provenance_score(card),
    }
    total_weight = sum(profile.weights.values())
    score = sum(components[name] * profile.weights[name] for name in components) / total_weight
    score = round(score, 6)
    return RankedCompetition(
        candidate=candidate,
        score=score,
        score_components={name: round(value, 6) for name, value in components.items()},
        fit_level=profile.fit_level(score),
    )


def rank_candidates(
    candidates: tuple[CompetitionCandidate, ...],
    *,
    preferences: CompetitionPreferences,
    understanding: CompetitionQueryUnderstanding,
    profile: CompetitionRankingProfile,
) -> tuple[RankedCompetition, ...]:
    ranked = tuple(_score(candidate, preferences, understanding, profile) for candidate in candidates)
    return tuple(sorted(
        ranked,
        key=lambda item: (
            -item.score,
            not item.candidate.card.in_2024_catalog,
            item.candidate.card.display_name,
            item.candidate.card.competition_id,
        ),
    ))


__all__ = ["RankedCompetition", "rank_candidates"]
