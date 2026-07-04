from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dext_recommend.api.access_log import RecommendAccessLogger
from dext_recommend.api.keys import REQUEST_ID_KEY
from dext_recommend.api.middleware import request_middleware


class FakeAccessRequest(dict):
    remote = "127.0.0.1"
    method = "GET"
    path_qs = "/api/v1/health?ready=1"
    version = SimpleNamespace(major=1, minor=1)


def test_recommend_access_logger_emits_uvicorn_style_request_line(caplog) -> None:
    logger = logging.getLogger("tests.recommend.access")
    access_logger = RecommendAccessLogger(logger, "")
    request = FakeAccessRequest({REQUEST_ID_KEY: "req-123"})
    response = SimpleNamespace(status=200)

    caplog.set_level(logging.INFO, logger=logger.name)
    access_logger.log(request, response, 0.0034)

    assert '127.0.0.1 - "GET /api/v1/health?ready=1 HTTP/1.1" 200 OK 3.4ms request_id=req-123' in caplog.text


@pytest.mark.asyncio
async def test_request_middleware_logs_unexpected_exception_stack(caplog) -> None:
    async def explode(request: web.Request) -> web.Response:
        raise RuntimeError("boom")

    app = web.Application(middlewares=[request_middleware])
    app.router.add_get("/explode", explode)
    request_id = "req-500"

    caplog.set_level(logging.ERROR, logger="dext_recommend.api.middleware")
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/explode", headers={"X-Request-ID": request_id})
        body = json.loads(await response.text())

    assert response.status == 500
    assert body["error_code"] == "internal_error"
    assert body["message"] == "internal server error"
    assert body["request_id"] == request_id
    assert "unhandled recommend api request error" in caplog.text
    assert f"request_id={request_id}" in caplog.text
    assert "RuntimeError: boom" in caplog.text
