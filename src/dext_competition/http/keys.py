"""Typed aiohttp app keys for competition C7 wiring."""
from __future__ import annotations

from aiohttp import web

RECOMMENDATION_SERVICE_KEY = web.AppKey("competition_recommendation_service", object)
PLAN_GENERATOR_KEY = web.AppKey("competition_plan_generator", object)
ASSISTANT_DEPS_KEY = web.AppKey("competition_assistant_deps", object)
PLAN_REPOSITORY_KEY = web.AppKey("competition_plan_repository", object)
ASSISTANT_HISTORY_REPOSITORY_KEY = web.AppKey("competition_assistant_history_repository", object)
OWNER_RESOLVER_KEY = web.AppKey("competition_owner_resolver", object)

__all__ = [
    "ASSISTANT_DEPS_KEY",
    "ASSISTANT_HISTORY_REPOSITORY_KEY",
    "OWNER_RESOLVER_KEY",
    "PLAN_GENERATOR_KEY",
    "PLAN_REPOSITORY_KEY",
    "RECOMMENDATION_SERVICE_KEY",
]
