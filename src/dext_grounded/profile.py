"""Versioned generation profile (spec §7).

All prompts, JSON schemas, safety rules, trim thresholds, and token budgets
MUST enter ``generation_profile_version`` — peer to ranking_profile_version
(recommend) and competition_ranking_profile_version (competition). Version
changes require re-running groundedness evaluation; prompts must never be
edited inline in a handler.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class GenerationProfile:
    version: str
    prompt_ids: list[str] = field(default_factory=list)
    json_schema_ids: list[str] = field(default_factory=list)
    safety_rule_ids: list[str] = field(default_factory=list)
    trim_token_budget: int = 4096
    quote_max_len: int = 500

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("generation profile version must be non-empty")


class ProfileRegistry:
    """In-process registry of known generation profiles."""

    def __init__(self) -> None:
        self._by_version: dict[str, GenerationProfile] = {}

    def register(self, profile: GenerationProfile) -> None:
        self._by_version[profile.version] = profile

    def get(self, version: str) -> GenerationProfile | None:
        return self._by_version.get(version)


__all__ = ["GenerationProfile", "ProfileRegistry"]
