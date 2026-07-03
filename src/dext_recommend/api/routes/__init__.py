"""Route registration for recommendation HTTP API."""
from __future__ import annotations

from aiohttp import web

from dext_recommend.api.routes import (
    conversations,
    favorites,
    history,
    identity,
    professors,
    profile,
    recommendations,
)

API_PREFIX = "/api/v1"


def setup_routes(app: web.Application) -> None:
    routes: list[web.AbstractRouteDef] = []
    for module in (
        identity,
        recommendations,
        professors,
        conversations,
        profile,
        favorites,
        history,
    ):
        routes.extend(module.routes(API_PREFIX))
    app.add_routes(routes)

    async def deferred(_: web.Request) -> web.Response:
        from dext_recommend.api.middleware import ApiError
        raise ApiError(404, "deferred_route", "route is deferred until a core service exists")

    app.add_routes([
        web.post(f"{API_PREFIX}/chat/route", deferred),
        web.post(f"{API_PREFIX}/chat/quick-actions", deferred),
        web.post(f"{API_PREFIX}/profile/achievements/extract", deferred),
    ])


__all__ = ["API_PREFIX", "setup_routes"]
