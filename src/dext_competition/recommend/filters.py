"""Structured filters for C3 candidate pruning."""
from __future__ import annotations

from dext_competition.contracts.recommend import CompetitionPreferences
from dext_competition.recommend.recall import CompetitionCandidate


def apply_filters(
    candidates: tuple[CompetitionCandidate, ...],
    preferences: CompetitionPreferences,
) -> tuple[CompetitionCandidate, ...]:
    allowed_categories = {category.strip() for category in preferences.categories if category.strip()}
    team_pref = (preferences.team_preference or "").strip()
    result: list[CompetitionCandidate] = []
    for candidate in candidates:
        card = candidate.card
        if allowed_categories and card.category not in allowed_categories:
            continue
        if team_pref in {"solo", "team"} and card.team_policy in {"solo", "team"}:
            if card.team_policy != team_pref:
                continue
        result.append(candidate)
    return tuple(result)


__all__ = ["apply_filters"]
