"""Minimal owner resolution seam for C7 route tests and shared-app wiring."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

from aiohttp import web

from dext_competition.http.errors import ApiError
from dext_competition.http.keys import OWNER_RESOLVER_KEY

OwnerResolver: TypeAlias = Callable[[web.Request], str | Awaitable[str]]


async def default_owner_resolver(request: web.Request) -> str:
    owner_id = request.headers.get("X-Dext-Owner-Id", "").strip()
    if not owner_id:
        raise ApiError(401, "unauthorized", "owner identity is required")
    return owner_id


async def require_owner_id(request: web.Request) -> str:
    resolver = request.app.get(OWNER_RESOLVER_KEY, default_owner_resolver)
    value = resolver(request)
    if hasattr(value, "__await__"):
        value = await value
    owner_id = str(value).strip()
    if not owner_id:
        raise ApiError(401, "unauthorized", "owner identity is required")
    return owner_id


__all__ = ["OwnerResolver", "default_owner_resolver", "require_owner_id"]
