"""aiohttp application factory for the R7b-lite recommendation API."""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from dext_recommend.api.keys import (
    APPLICATION_SERVICES_KEY,
    ATTEMPT_REGISTRY_KEY,
    CONVERSATION_STORE_KEY,
    REPOSITORY_KEY,
    RUNTIME_KEY,
    SETTINGS_KEY,
)
from dext_recommend.api.middleware import request_middleware
from dext_recommend.api.routes import setup_routes
from dext_recommend.api.settings import AppSettings
from dext_recommend.app_state import (
    AppStateRepository,
    PostgresConversationStore,
    create_async_engine_from_settings,
    create_sessionmaker,
    ensure_schema_ready,
)
from dext_recommend.application import ApplicationServices, AttemptRegistry
from dext_recommend.config import RecommendSettings
from dext_recommend.runtime import build_live_recommendation_runtime

RuntimeFactory = Callable[..., Awaitable[Any] | Any]


def create_recommendation_app(
    settings: AppSettings | None = None,
    *,
    recommend_settings: RecommendSettings | None = None,
    runtime_factory: RuntimeFactory | None = None,
    identity_provider: object | None = None,
) -> web.Application:
    """Return an unstarted aiohttp app.

    identity_provider is accepted as a forward-compatible seam; R7b-lite
    ships only anonymous bearer/cookie auth.
    """
    del identity_provider
    app_settings = settings or AppSettings()
    app = web.Application(middlewares=[request_middleware])
    app[SETTINGS_KEY] = app_settings
    app.cleanup_ctx.append(_cleanup_ctx(
        app_settings=app_settings,
        recommend_settings=recommend_settings or RecommendSettings(),
        runtime_factory=runtime_factory or build_live_recommendation_runtime,
    ))
    setup_routes(app)
    return app


def _cleanup_ctx(
    *,
    app_settings: AppSettings,
    recommend_settings: RecommendSettings,
    runtime_factory: RuntimeFactory,
):
    async def ctx(app: web.Application):
        engine = create_async_engine_from_settings(app_settings)
        runtime = None
        attempts = AttemptRegistry()
        try:
            await ensure_schema_ready(engine, bootstrap=app_settings.schema_bootstrap)
            sessionmaker = create_sessionmaker(engine)
            repository = AppStateRepository(sessionmaker)
            store = PostgresConversationStore(sessionmaker)
            runtime = runtime_factory(
                recommend_settings,
                conversation_store=store,
            )
            if inspect.isawaitable(runtime):
                runtime = await runtime
            app[REPOSITORY_KEY] = repository
            app[CONVERSATION_STORE_KEY] = store
            app[RUNTIME_KEY] = runtime
            app[ATTEMPT_REGISTRY_KEY] = attempts
            app[APPLICATION_SERVICES_KEY] = ApplicationServices(
                repository=repository,
                runtime=runtime,
                attempts=attempts,
            )
            yield
        finally:
            await attempts.shutdown(app_settings.shutdown_grace_seconds)
            close = getattr(runtime, "aclose", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
            await engine.dispose()

    return ctx


__all__ = ["create_recommendation_app"]
