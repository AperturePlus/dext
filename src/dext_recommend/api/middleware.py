"""aiohttp middleware and envelope helpers for /api/v1."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from dext_recommend.api.keys import REQUEST_ID_KEY, SETTINGS_KEY
from dext_recommend.app_state.db import SchemaNotReadyError
from dext_recommend.app_state.repositories import AppStateError
from dext_recommend.errors import RecommendationRuntimeError
from dext_recommend.ports.release_readback import ReadinessSourceError


logger = logging.getLogger(__name__)


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


_ERROR_CODES = {
    400: 40001,
    401: 40101,
    403: 40301,
    404: 40401,
    409: 40901,
    413: 41301,
    422: 42201,
    429: 42901,
    500: 50001,
    503: 50301,
    504: 50401,
}


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
            "code": _ERROR_CODES.get(status, status * 100 + 1),
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
    if request.method == "OPTIONS":
        response = web.Response(status=204)
        response.headers["X-Request-ID"] = request_id
        _apply_cors(request, response)
        return response
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
    except ReadinessSourceError as exc:
        response = error_response(
            status=503,
            error_code="readiness_source_unavailable",
            message="导师详情暂时不可用，请稍后重试",
            request_id=request_id,
            data={"source": exc.source, "retryable": exc.retryable},
        )
    except web.HTTPException as exc:
        response = error_response(
            status=exc.status,
            error_code="http_error",
            message=exc.reason or "HTTP error",
            request_id=request_id,
        )
    except Exception:
        logger.exception(
            "unhandled recommend api request error request_id=%s method=%s path=%s",
            request_id,
            request.method,
            request.path_qs,
        )
        response = error_response(
            status=500,
            error_code="internal_error",
            message="internal server error",
            request_id=request_id,
        )
    if not getattr(response, "prepared", False):
        response.headers["X-Request-ID"] = request_id
        _apply_cors(request, response)
    return response


def _apply_cors(request: web.Request, response: web.StreamResponse) -> None:
    origin = request.headers.get("Origin")
    if not origin:
        return
    settings = request.app[SETTINGS_KEY]
    allowed = tuple(getattr(settings, "cors_allowed_origins", ()) or ())
    if origin not in allowed:
        return
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Expose-Headers"] = "X-Request-ID"
    requested_headers = request.headers.get("Access-Control-Request-Headers")
    response.headers["Access-Control-Allow-Headers"] = (
        requested_headers
        or "Authorization, Content-Type, X-Request-ID, X-CSRF-Token, Idempotency-Key"
    )
    response.headers["Access-Control-Allow-Methods"] = (
        "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    )


__all__ = ["ApiError", "error_response", "json_response", "ok", "read_json", "request_middleware"]
