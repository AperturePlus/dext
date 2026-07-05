"""Aiohttp wiring for C7 competition endpoints."""
from __future__ import annotations

from dataclasses import dataclass

from aiohttp import web

from dext_competition.assistant import PlanAssistantDeps
from dext_competition.http.auth import OwnerResolver
from dext_competition.http.errors import error_middleware
from dext_competition.http.keys import (
    ASSISTANT_DEPS_KEY,
    ASSISTANT_HISTORY_REPOSITORY_KEY,
    OWNER_RESOLVER_KEY,
    PLAN_GENERATOR_KEY,
    PLAN_REPOSITORY_KEY,
    RECOMMENDATION_SERVICE_KEY,
)
from dext_competition.http.routes import routes
from dext_competition.planning import PlanGeneratorDeps, PreparationPlanGenerator
from dext_competition.recommend import CompetitionRecommendationService
from dext_competition.repositories import (
    InMemoryPreparationAssistantHistoryRepository,
    InMemoryPreparationPlanRepository,
    PreparationAssistantHistoryRepository,
    PreparationPlanRepository,
)

API_PREFIX = "/api/v1"


@dataclass(slots=True)
class CompetitionHttpDeps:
    recommendation_service: CompetitionRecommendationService
    plan_generator: PreparationPlanGenerator
    assistant_deps: PlanAssistantDeps
    plan_repository: PreparationPlanRepository
    assistant_history_repository: PreparationAssistantHistoryRepository
    owner_resolver: OwnerResolver | None = None


def setup_routes(
    app: web.Application,
    deps: CompetitionHttpDeps,
    *,
    prefix: str = API_PREFIX,
) -> None:
    app[RECOMMENDATION_SERVICE_KEY] = deps.recommendation_service
    app[PLAN_GENERATOR_KEY] = deps.plan_generator
    app[ASSISTANT_DEPS_KEY] = deps.assistant_deps
    app[PLAN_REPOSITORY_KEY] = deps.plan_repository
    app[ASSISTANT_HISTORY_REPOSITORY_KEY] = deps.assistant_history_repository
    if deps.owner_resolver is not None:
        app[OWNER_RESOLVER_KEY] = deps.owner_resolver
    app.add_routes(routes(prefix))


def create_app(deps: CompetitionHttpDeps) -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    setup_routes(app, deps)
    return app


def create_test_app(
    *,
    recommendation_service: CompetitionRecommendationService,
    plan_generator: PreparationPlanGenerator,
    assistant_deps: PlanAssistantDeps,
    owner_resolver: OwnerResolver | None = None,
) -> web.Application:
    return create_app(CompetitionHttpDeps(
        recommendation_service=recommendation_service,
        plan_generator=plan_generator,
        assistant_deps=assistant_deps,
        plan_repository=InMemoryPreparationPlanRepository(),
        assistant_history_repository=InMemoryPreparationAssistantHistoryRepository(),
        owner_resolver=owner_resolver,
    ))


__all__ = ["API_PREFIX", "CompetitionHttpDeps", "create_app", "create_test_app", "setup_routes"]
