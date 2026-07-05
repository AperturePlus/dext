"""Small JSON-envelope helpers for competition HTTP routes."""
from __future__ import annotations

import json
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from dext_competition.errors import CompetitionErrorCode


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def ok(data: Any) -> web.Response:
    return web.json_response({"code": 0, "message": "ok", "data": data}, dumps=_dumps)


def error_response(error: ApiError) -> web.Response:
    return web.json_response(
        {
            "code": error.status,
            "message": error.message,
            "data": {"error_code": error.code},
        },
        status=error.status,
        dumps=_dumps,
    )


async def read_json(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ApiError(400, "invalid_json", "request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError(422, "invalid_request", "request body must be a JSON object")
    return payload


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as exc:
        return error_response(exc)
    except ValidationError as exc:
        return error_response(ApiError(422, "invalid_request", str(exc.errors()[0]["msg"])))
    except ValueError as exc:
        return error_response(ApiError(422, CompetitionErrorCode.INVALID_REQUEST.value, str(exc)))


__all__ = ["ApiError", "error_middleware", "ok", "read_json"]
