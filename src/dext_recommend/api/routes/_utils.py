"""Shared route utilities."""
from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from dext_recommend.api.keys import (
    APPLICATION_SERVICES_KEY,
    REPOSITORY_KEY,
    RUNTIME_KEY,
)
from dext_recommend.api.middleware import ApiError


def repository(request: web.Request):
    return request.app[REPOSITORY_KEY]


def runtime(request: web.Request):
    return request.app[RUNTIME_KEY]


def services(request: web.Request):
    return request.app[APPLICATION_SERVICES_KEY]


def idempotency_key(request: web.Request) -> str:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        raise ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required")
    if len(key) > 128:
        raise ApiError(422, "invalid_idempotency_key", "Idempotency-Key is too long")
    return key


def raise_if_domain_error(warnings: tuple | list) -> None:
    errors = [item for item in warnings if getattr(item, "severity", None) == "error"]
    if not errors:
        return
    first = errors[0]
    code = str(getattr(first, "code", "domain_error"))
    message = str(getattr(first, "message", "domain error"))
    status = 500
    if code in {"invalid_request", "invalid_conversation_state", "invalid_intent"}:
        status = 422
    elif code.startswith("unauthorized"):
        status = 403
    elif code in {"active_build_unavailable", "generation_unavailable", "llm_unavailable"}:
        status = 503
    elif code in {"anchor_not_in_active_build", "no_candidates_after_filters"}:
        status = 404
    elif code == "request_timeout":
        status = 504
    raise ApiError(status, code, message)


async def write_sse(request: web.Request, events: list[tuple[str, dict[str, Any]]]) -> web.StreamResponse:
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    for event, data in events:
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
        await response.write(f"event: {event}\ndata: {payload}\n\n".encode("utf-8"))
    await response.write_eof()
    return response


__all__ = [
    "idempotency_key",
    "raise_if_domain_error",
    "repository",
    "runtime",
    "services",
    "write_sse",
]
