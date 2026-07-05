"""Combined aiohttp application for recommendation and competition routes."""
from __future__ import annotations

from dataclasses import dataclass
import logging

from aiohttp import web

from dext_competition.adapters.llm_generation import CompetitionOpenAICompatibleGenerationAdapter
from dext_competition.assistant import PlanAssistantDeps
from dext_competition.catalog import (
    CatalogArtifactError,
    build_catalog,
    verify_catalog,
)
from dext_competition.config import CompetitionSettings
from dext_competition.http.app import CompetitionHttpDeps
from dext_competition.http.app import setup_routes as setup_competition_routes
from dext_competition.http.errors import ApiError as CompetitionApiError
from dext_competition.index import (
    IndexArtifactError,
    build_index,
    verify_index,
)
from dext_competition.planning import PlanGeneratorDeps, PreparationPlanGenerator
from dext_competition.recommend import CompetitionRecommendDeps, CompetitionRecommendationService
from dext_competition.repositories import (
    InMemoryPreparationAssistantHistoryRepository,
    InMemoryPreparationPlanRepository,
)
from dext_grounded import ConstrainedGenerationPipeline
from dext_recommend.api.app import RuntimeFactory, create_recommendation_app
from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ApiError as RecommendApiError
from dext_recommend.api.routes import API_PREFIX
from dext_recommend.api.settings import AppSettings
from dext_recommend.config import RecommendSettings

logger = logging.getLogger(__name__)


logger = logging.getLogger("dext_app.api")


@dataclass(frozen=True, slots=True)
class PreparedCompetitionArtifacts:
    knowledge: object
    catalog: object
    index_built: bool
    catalog_built: bool


@web.middleware
async def _competition_api_error_adapter(request: web.Request, handler):
    try:
        return await handler(request)
    except CompetitionApiError as exc:
        raise RecommendApiError(exc.status, exc.code, exc.message) from exc


async def _owner_id_from_recommend_auth(request: web.Request) -> str:
    principal = await require_principal(request)
    return str(principal.owner_id)


def _with_recommend_owner_resolver(deps: CompetitionHttpDeps) -> CompetitionHttpDeps:
    if deps.owner_resolver is not None:
        return deps
    return CompetitionHttpDeps(
        recommendation_service=deps.recommendation_service,
        plan_generator=deps.plan_generator,
        assistant_deps=deps.assistant_deps,
        plan_repository=deps.plan_repository,
        assistant_history_repository=deps.assistant_history_repository,
        owner_resolver=_owner_id_from_recommend_auth,
    )


async def prepare_competition_artifacts(
    settings: CompetitionSettings | None = None,
) -> PreparedCompetitionArtifacts:
    """Ensure local competition index/catalog artifacts exist and are compatible."""

    resolved = settings or CompetitionSettings()
    index_built = False
    catalog_built = False
    try:
        knowledge = verify_index(resolved.index_artifact_dir)
    except (IndexArtifactError, OSError, ValueError) as exc:
        logger.info("Competition index artifacts unavailable; building them: %s", exc)
        build_index(resolved.knowledge_source_root, resolved.index_artifact_dir)
        index_built = True
        knowledge = verify_index(resolved.index_artifact_dir)

    expected_version = knowledge.manifest().version_id
    try:
        catalog = verify_catalog(
            resolved.catalog_artifact_dir,
            expected_knowledge_base_version=expected_version,
        )
    except (CatalogArtifactError, OSError, ValueError) as exc:
        logger.info("Competition catalog artifacts unavailable or stale; building them: %s", exc)
        await build_catalog(knowledge, resolved.catalog_artifact_dir)
        catalog_built = True
        catalog = verify_catalog(
            resolved.catalog_artifact_dir,
            expected_knowledge_base_version=expected_version,
        )

    return PreparedCompetitionArtifacts(
        knowledge=knowledge,
        catalog=catalog,
        index_built=index_built,
        catalog_built=catalog_built,
    )


def create_live_competition_http_deps(
    settings: CompetitionSettings | None = None,
    *,
    generation_pipeline: ConstrainedGenerationPipeline | None = None,
    recommend_settings: RecommendSettings | None = None,
) -> CompetitionHttpDeps:
    """Build competition HTTP dependencies from local checked artifacts."""

    resolved = settings or CompetitionSettings()
    if generation_pipeline is None:
        generation_pipeline = _new_competition_generation_pipeline(recommend_settings)
    knowledge = verify_index(resolved.index_artifact_dir)
    catalog = verify_catalog(
        resolved.catalog_artifact_dir,
        expected_knowledge_base_version=knowledge.manifest().version_id,
    )
    return CompetitionHttpDeps(
        recommendation_service=CompetitionRecommendationService(
            CompetitionRecommendDeps(
                catalog_port=catalog,
                knowledge_index=knowledge,
                generation_pipeline=generation_pipeline,
            ),
            settings=resolved,
        ),
        plan_generator=PreparationPlanGenerator(
            PlanGeneratorDeps(
                catalog_port=catalog,
                generation_pipeline=generation_pipeline,
            )
        ),
        assistant_deps=PlanAssistantDeps(
            generation_pipeline=generation_pipeline,
            knowledge_index=knowledge,
        ),
        plan_repository=InMemoryPreparationPlanRepository(),
        assistant_history_repository=InMemoryPreparationAssistantHistoryRepository(),
    )


def create_app(
    settings: AppSettings | None = None,
    *,
    recommend_settings: RecommendSettings | None = None,
    runtime_factory: RuntimeFactory | None = None,
    competition_deps: CompetitionHttpDeps | None = None,
    competition_settings: CompetitionSettings | None = None,
    competition_generation_pipeline: ConstrainedGenerationPipeline | None = None,
    identity_provider: object | None = None,
) -> web.Application:
    """Return the Flutter-facing app with mentor and competition endpoints."""

    app = create_recommendation_app(
        settings,
        recommend_settings=recommend_settings,
        runtime_factory=runtime_factory,
        identity_provider=identity_provider,
    )
    app.middlewares.append(_competition_api_error_adapter)
    deps = competition_deps or create_live_competition_http_deps(
        competition_settings,
        generation_pipeline=competition_generation_pipeline,
        recommend_settings=recommend_settings,
    )
    setup_competition_routes(
        app,
        _with_recommend_owner_resolver(deps),
        prefix=API_PREFIX,
    )
    return app


def _new_competition_generation_pipeline(
    settings: RecommendSettings | None = None,
) -> ConstrainedGenerationPipeline | None:
    resolved = settings or RecommendSettings()
    api_key = resolved.llm_api_key.get_secret_value()
    if not api_key:
        logger.warning("competition LLM pipeline disabled: recommend LLM API key is not configured")
        return None
    try:
        from openai import AsyncOpenAI

        timeout = max(float(resolved.llm_timeout), 120.0)
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=resolved.llm_base_url,
            max_retries=resolved.llm_max_retries,
            timeout=timeout,
        )
        return ConstrainedGenerationPipeline(
            CompetitionOpenAICompatibleGenerationAdapter(
                client=client,
                model=resolved.llm_model,
                timeout=timeout,
            )
        )
    except Exception as exc:
        logger.warning(
            "competition LLM pipeline disabled: %s",
            type(exc).__name__,
        )
        return None
      
__all__ = [
    "PreparedCompetitionArtifacts",
    "create_app",
    "create_live_competition_http_deps",
    "prepare_competition_artifacts",
]
