"""C3 competition recommendation service."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dext_competition.config import CompetitionSettings
from dext_competition.contracts.catalog import CompetitionCategory
from dext_competition.contracts.recommend import (
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
    CompetitionRecommendRequest,
    CompetitionRecommendResponse,
    CompetitionWarning,
)
from dext_competition.errors import CompetitionErrorCode
from dext_competition.ports import (
    CompetitionCatalogPort,
    ConstrainedGenerationPipeline,
    KnowledgeIndexPort,
)
from dext_competition.recommend.explanation import assemble_recommended_competition
from dext_competition.recommend.filters import apply_filters
from dext_competition.recommend.profile import (
    CompetitionRankingProfile,
    load_ranking_profile,
)
from dext_competition.recommend.generation_profile import (
    DEFAULT_QUERY_PROFILE_PATH,
    QueryUnderstandingProfile,
    load_query_understanding_profile,
)
from dext_competition.recommend.query_understanding import (
    DEFAULT_GENERATION_PROFILE_VERSION,
    understand_query,
)
from dext_competition.recommend.ranking import rank_candidates
from dext_competition.recommend.recall import recall_candidates


@dataclass(frozen=True, slots=True)
class CompetitionRecommendDeps:
    catalog_port: CompetitionCatalogPort
    knowledge_index: KnowledgeIndexPort
    generation_pipeline: ConstrainedGenerationPipeline | None = None
    ranking_profile: CompetitionRankingProfile | None = None
    ranking_profile_path: str | Path = "data/competition/profiles/ranking-v1.json"
    generation_profile: QueryUnderstandingProfile | None = None
    generation_profile_path: str | Path = DEFAULT_QUERY_PROFILE_PATH


def _warning(code: CompetitionErrorCode, message: str,
             competition_id: str | None = None) -> CompetitionWarning:
    return CompetitionWarning(code=code.value, message=message, competition_id=competition_id)


def _kb_version(deps: CompetitionRecommendDeps) -> str:
    try:
        return deps.catalog_port.manifest().knowledge_base_version
    except Exception:
        try:
            return deps.knowledge_index.manifest().version_id
        except Exception:
            return "unavailable"


def _response(
    *,
    deps: CompetitionRecommendDeps,
    profile: CompetitionRankingProfile,
    generation_profile_version: str,
    query_understanding: CompetitionQueryUnderstanding | None,
    warnings: tuple[CompetitionWarning, ...] = (),
    results=(),
) -> CompetitionRecommendResponse:
    return CompetitionRecommendResponse(
        knowledge_base_version=_kb_version(deps),
        query_understanding=query_understanding,
        competition_ranking_profile_version=profile.version,
        generation_profile_version=generation_profile_version,
        results=tuple(results),
        warnings=warnings,
    )


def _validate_preferences(preferences: CompetitionPreferences) -> str | None:
    valid_categories = {item.value for item in CompetitionCategory}
    invalid_categories = sorted(set(preferences.categories) - valid_categories)
    if invalid_categories:
        return "unknown competition categories: " + ", ".join(invalid_categories)
    if preferences.experience_level not in {None, "beginner", "intermediate", "advanced"}:
        return "experience_level must be beginner|intermediate|advanced"
    if preferences.team_preference not in {None, "solo", "team", "either"}:
        return "team_preference must be solo|team|either"
    if preferences.target_goal not in {
        None, "learn", "portfolio", "school_recognition", "research", "job_skill",
    }:
        return "target_goal must be learn|portfolio|school_recognition|research|job_skill"
    if preferences.risk_tolerance not in {"low", "medium", "high"}:
        return "risk_tolerance must be low|medium|high"
    if preferences.weekly_hours is not None and preferences.weekly_hours < 0:
        return "weekly_hours must be non-negative"
    return None


def validate_request(request: CompetitionRecommendRequest) -> str | None:
    if not isinstance(request.query_text, str):
        return "query_text must be a string"
    if not request.query_text.strip() and not request.preferences.categories:
        return "query_text or preferences.categories is required"
    if request.limit < 1 or request.limit > 20:
        return "limit must be in [1, 20]"
    if request.diagnostics_level not in {"none", "summary", "debug"}:
        return "diagnostics_level must be none|summary|debug"
    return _validate_preferences(request.preferences)


class CompetitionRecommendationService:
    def __init__(self, deps: CompetitionRecommendDeps,
                 settings: CompetitionSettings | None = None) -> None:
        self._deps = deps
        self._settings = settings or CompetitionSettings()

    def _profile(self) -> CompetitionRankingProfile:
        return self._deps.ranking_profile or load_ranking_profile(self._deps.ranking_profile_path)

    def _generation_profile(self) -> QueryUnderstandingProfile:
        return self._deps.generation_profile or load_query_understanding_profile(
            self._deps.generation_profile_path
        )

    async def recommend(self, request: CompetitionRecommendRequest) -> CompetitionRecommendResponse:
        profile = self._profile()
        try:
            generation_profile = self._generation_profile()
        except (OSError, KeyError, TypeError, ValueError):
            generation_profile = None
        generation_profile_version = (
            generation_profile.version if generation_profile else DEFAULT_GENERATION_PROFILE_VERSION
        )
        validation_error = validate_request(request)
        if validation_error is not None:
            return _response(
                deps=self._deps,
                profile=profile,
                generation_profile_version=generation_profile_version,
                query_understanding=None,
                warnings=(
                    _warning(CompetitionErrorCode.INVALID_REQUEST, validation_error),
                ),
            )

        if generation_profile is None:
            return _response(
                deps=self._deps,
                profile=profile,
                generation_profile_version=generation_profile_version,
                query_understanding=None,
                warnings=(_warning(
                    CompetitionErrorCode.GENERATION_UNAVAILABLE,
                    "query-understanding generation profile is unavailable",
                ),),
            )

        query_result = await understand_query(
            request,
            knowledge_base_version=_kb_version(self._deps),
            generation_pipeline=self._deps.generation_pipeline,
            profile=generation_profile,
        )
        understanding = query_result.understanding
        generation_profile_version = query_result.generation_profile_version
        if query_result.warning_code is not None:
            return _response(
                deps=self._deps,
                profile=profile,
                generation_profile_version=generation_profile_version,
                query_understanding=understanding,
                warnings=(CompetitionWarning(
                    code=query_result.warning_code,
                    message="query understanding failed before recall",
                ),),
            )
        if understanding.needs_clarification:
            return _response(
                deps=self._deps,
                profile=profile,
                generation_profile_version=generation_profile_version,
                query_understanding=understanding,
                warnings=(
                    _warning(
                        CompetitionErrorCode.NEEDS_CLARIFICATION,
                        "needs clarification before recall",
                    ),
                ),
            )

        candidates = await recall_candidates(
            catalog=self._deps.catalog_port,
            knowledge_index=self._deps.knowledge_index,
            understanding=understanding,
            query_text=request.query_text,
            limit=max(profile.recall_top_k, request.limit),
        )
        filtered = apply_filters(candidates, request.preferences)
        if not filtered:
            return _response(
                deps=self._deps,
                profile=profile,
                generation_profile_version=generation_profile_version,
                query_understanding=understanding,
                warnings=(
                    _warning(
                        CompetitionErrorCode.NO_CANDIDATES_AFTER_FILTERS,
                        "no competitions matched the requested filters",
                    ),
                ),
            )

        ranked = rank_candidates(
            filtered,
            preferences=request.preferences,
            understanding=understanding,
            profile=profile,
        )
        results = tuple(
            assemble_recommended_competition(item, profile=profile)
            for item in ranked[: request.limit]
        )
        return _response(
            deps=self._deps,
            profile=profile,
            generation_profile_version=generation_profile_version,
            query_understanding=understanding,
            results=results,
        )


__all__ = [
    "CompetitionRecommendDeps",
    "CompetitionRecommendationService",
    "validate_request",
]
