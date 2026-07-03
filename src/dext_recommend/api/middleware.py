"""aiohttp middleware and envelope helpers for /api/v1."""
from __future__ import annotations

import json
import uuid
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from dext_recommend.api.keys import REQUEST_ID_KEY, SETTINGS_KEY
from dext_recommend.app_state.db import SchemaNotReadyError
from dext_recommend.app_state.repositories import AppStateError
from dext_recommend.errors import RecommendationRuntimeError


class ApiError(RuntimeError):
    def __init__(
        self,
        status: int,
        error_code: str,
        message: str,
        *,
        data: Any = None,
    ) -> None:
        self.status = status
        self.error_code = error_code
        self.message = message
        self.data = data
        super().__init__(message)


def json_response(payload: dict[str, Any], *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        status=status,
        content_type="application/json",
    )


def ok(data: Any = None, *, message: str = "ok", status: int = 200) -> web.Response:
    return json_response({"code": 0, "message": message, "data": data}, status=status)


def error_response(
    *,
    status: int,
    error_code: str,
    message: str,
    request_id: str,
    data: Any = None,
) -> web.Response:
    return json_response(
        {
            "code": status,
            "error_code": error_code,
            "message": message,
            "request_id": request_id,
            "data": data,
        },
        status=status,
    )


async def read_json(request: web.Request) -> dict[str, Any]:
    settings = request.app[SETTINGS_KEY]
    if request.can_read_body:
        size = int(request.headers.get("Content-Length") or 0)
        if size and size > settings.request_body_max_bytes:
            raise ApiError(413, "request_body_too_large", "request body too large")
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise ApiError(400, "invalid_json", "request body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "request body must be a JSON object")
    return body


@web.middleware
async def request_middleware(request: web.Request, handler):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request[REQUEST_ID_KEY] = request_id
    try:
        response = await handler(request)
    except ApiError as exc:
        response = error_response(
            status=exc.status,
            error_code=exc.error_code,
            message=exc.message,
            request_id=request_id,
            data=exc.data,
        )
    except AppStateError as exc:
        response = error_response(
            status=exc.status,
            error_code=exc.error_code,
            message=str(exc),
            request_id=request_id,
        )
    except ValidationError as exc:
        response = error_response(
            status=422,
            error_code="validation_error",
            message="request validation failed",
            request_id=request_id,
            data=exc.errors(include_url=False, include_input=False),
        )
    except ValueError as exc:
        response = error_response(
            status=422,
            error_code="invalid_request",
            message=str(exc),
            request_id=request_id,
        )
    except SchemaNotReadyError as exc:
        response = error_response(
            status=503,
            error_code="schema_not_ready",
            message=str(exc),
            request_id=request_id,
        )
    except RecommendationRuntimeError as exc:
        response = error_response(
            status=503 if exc.retryable else 500,
            error_code=exc.code,
            message=exc.message,
            request_id=request_id,
        )
    except web.HTTPException:
        raise
    except Exception:
        response = error_response(
            status=500,
            error_code="internal_error",
            message="internal server error",
            request_id=request_id,
        )
    response.headers["X-Request-ID"] = request_id
    return response


__all__ = ["ApiError", "error_response", "json_response", "ok", "read_json", "request_middleware"]
