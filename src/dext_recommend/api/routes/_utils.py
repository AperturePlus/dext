"""Shared route utilities."""
from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from dext_recommend.api.keys import (
    APPLICATION_SERVICES_KEY,
    REQUEST_ID_KEY,
    REPOSITORY_KEY,
    RUNTIME_KEY,
    SETTINGS_KEY,
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
    elif code in {
        "active_build_unavailable",
        "generation_parse_error",
        "generation_unavailable",
        "llm_unavailable",
        "no_grounded_output",
        "schema_validation_failed",
    }:
        status = 503
    elif code in {"anchor_not_in_active_build", "no_candidates_after_filters"}:
        status = 404
    elif code == "request_timeout":
        status = 504
    raise ApiError(status, code, message)


async def prepare_sse(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request.get(REQUEST_ID_KEY, ""),
        },
    )
    _apply_sse_cors(request, response)
    await response.prepare(request)
    return response


class SseWriter:
    def __init__(self, response: web.StreamResponse) -> None:
        self._response = response
        self._seq = 0

    @property
    def seq(self) -> int:
        return self._seq

    async def event(self, event: str, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["seq"] = self._seq
        self._seq += 1
        await self._response.write(_sse_event_bytes(event, payload))

    async def heartbeat(self) -> None:
        await self._response.write(b": heartbeat\n\n")

    async def eof(self) -> None:
        await self._response.write_eof()


def _sse_event_bytes(event: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def write_sse_event(
    response: web.StreamResponse,
    event: str,
    data: dict[str, Any],
) -> None:
    writer = getattr(response, "_dext_sse_writer", None)
    if writer is None:
        writer = SseWriter(response)
        setattr(response, "_dext_sse_writer", writer)
    await writer.event(event, data)


async def write_sse_heartbeat(response: web.StreamResponse) -> None:
    writer = getattr(response, "_dext_sse_writer", None)
    if writer is None:
        writer = SseWriter(response)
        setattr(response, "_dext_sse_writer", writer)
    await writer.heartbeat()


async def write_sse(request: web.Request, events: list[tuple[str, dict[str, Any]]]) -> web.StreamResponse:
    response = await prepare_sse(request)
    writer = SseWriter(response)
    setattr(response, "_dext_sse_writer", writer)
    for event, data in events:
        await writer.event(event, data)
    await writer.eof()
    return response


def _apply_sse_cors(request: web.Request, response: web.StreamResponse) -> None:
    origin = request.headers.get("Origin")
    if not origin:
        return
    allowed = tuple(getattr(request.app[SETTINGS_KEY], "cors_allowed_origins", ()) or ())
    if origin not in allowed:
        return
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"


__all__ = [
    "SseWriter",
    "idempotency_key",
    "prepare_sse",
    "raise_if_domain_error",
    "repository",
    "runtime",
    "services",
    "write_sse",
    "write_sse_event",
    "write_sse_heartbeat",
]
