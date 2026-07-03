"""Route registration for recommendation HTTP API."""
from __future__ import annotations

from aiohttp import web

from dext_recommend.api.routes import (
    conversations,
    favorites,
    feedback,
    history,
    home,
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
        home,
        recommendations,
        professors,
        conversations,
        profile,
        favorites,
        history,
        feedback,
    ):
        routes.extend(module.routes(API_PREFIX))
    app.add_routes(routes)


__all__ = ["API_PREFIX", "setup_routes"]
