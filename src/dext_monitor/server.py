"""aiohttp server for the read-only monitor API and static WebUI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from aiohttp import web

from dext_monitor.catalog_reader import MonitorCatalogError
from dext_monitor.service import MonitorService
from dext_monitor.settings import MonitorSettings

SERVICE_KEY = web.AppKey("monitor_service", MonitorService)
API_PREFIX = "/api/monitor"


class MonitorNotFoundError(Exception):
    """A requested monitor API route does not exist."""


def json_response(data: Any, *, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, sort_keys=True),
        status=status,
        content_type="application/json",
    )


def error_response(exc: Exception, *, status: int = 400) -> web.Response:
    return json_response(
        {
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            }
        },
        status=status,
    )


def service(request: web.Request) -> MonitorService:
    return request.app[SERVICE_KEY]


async def handle_health(request: web.Request) -> web.Response:
    return json_response({"data": service(request).health()})


async def handle_builds(request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", "20"))
    return json_response({"data": service(request).list_builds(limit=limit)})


async def handle_build_detail(request: web.Request) -> web.Response:
    return json_response({"data": service(request).build_detail(request.match_info["build_id"])})


async def handle_metrics(request: web.Request) -> web.Response:
    return json_response({"data": service(request).metrics(request.match_info["build_id"])})


async def handle_graph_preview(request: web.Request) -> web.Response:
    limit_text = request.query.get("limit")
    limit = int(limit_text) if limit_text else None
    return json_response(
        {"data": service(request).graph_preview(request.match_info["build_id"], limit=limit)}
    )


async def handle_graph_tree(request: web.Request) -> web.Response:
    return json_response(
        {"data": service(request).graph_tree(request.match_info["build_id"])}
    )


async def handle_orgunit_professors(request: web.Request) -> web.Response:
    return json_response(
        {
            "data": service(request).orgunit_professors(
                request.match_info["build_id"],
                request.match_info["org_graph_key"],
            )
        }
    )


async def handle_findings(request: web.Request) -> web.Response:
    limit = int(request.query.get("limit", "100"))
    return json_response(
        {
            "data": service(request).findings(
                build_id=request.query.get("build_id"),
                severity=request.query.get("severity"),
                limit=limit,
            )
        }
    )


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        response = await handler(request)
    except MonitorCatalogError as exc:
        return error_response(exc, status=404)
    except MonitorNotFoundError as exc:
        return error_response(exc, status=404)
    except ValueError as exc:
        return error_response(exc, status=400)
    # Tag successful JSON responses under /api/monitor with a weak ETag derived
    # from the body and honor If-None-Match → 304. Read-only monitor data is a
    # natural fit: a terminal build's payload is byte-stable across polls, so
    # the client gets a zero-byte 304 instead of re-downloading the body.
    if (
        response.status == 200
        and request.path.startswith(API_PREFIX)
        and response.content_type == "application/json"
    ):
        etag = _weak_etag(response.body)
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = "no-cache"
        if request.headers.get("If-None-Match", "") == etag:
            response = web.Response(status=304, headers=response.headers)
            response.body = b""
    return response


def _weak_etag(body: bytes | str | None) -> str:
    payload = body if isinstance(body, bytes) else (body or "").encode("utf-8")
    digest = hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:16]
    return f'W/"{digest}"'


def create_app(settings: MonitorSettings | None = None) -> web.Application:
    settings = settings or MonitorSettings()
    app = web.Application(middlewares=[error_middleware])
    app[SERVICE_KEY] = MonitorService(settings)
    app.add_routes(
        [
            web.get(f"{API_PREFIX}/health", handle_health),
            web.get(f"{API_PREFIX}/builds", handle_builds),
            web.get(f"{API_PREFIX}/builds/{{build_id}}", handle_build_detail),
            web.get(f"{API_PREFIX}/builds/{{build_id}}/metrics", handle_metrics),
            web.get(f"{API_PREFIX}/builds/{{build_id}}/graph-preview", handle_graph_preview),
            web.get(f"{API_PREFIX}/builds/{{build_id}}/graph-tree", handle_graph_tree),
            web.get(
                f"{API_PREFIX}/builds/{{build_id}}/orgunit/{{org_graph_key}}/professors",
                handle_orgunit_professors,
            ),
            web.get(f"{API_PREFIX}/findings", handle_findings),
        ]
    )
    # Any /api/monitor path not matched above returns a JSON 404 envelope (never
    # an HTML 404), so the frontend's response parser is consistent. This is
    # registered independently of whether the static UI build exists.
    async def api_not_found(_: web.Request) -> web.Response:
        return error_response(
            MonitorNotFoundError("monitor API route not found"),
            status=404,
        )

    app.router.add_get(f"{API_PREFIX}/{{tail:.*}}", api_not_found)
    static_dir = _resolve_static_dir(settings.monitor_static_dir)
    index = static_dir / "index.html"
    assets_dir = static_dir / "assets"
    if index.is_file() and assets_dir.is_dir():
        app.router.add_static("/assets", assets_dir, name="assets")

    if index.is_file():
        async def index_handler(_: web.Request) -> web.FileResponse:
            return web.FileResponse(index)

        app.router.add_get("/", index_handler)
        app.router.add_get("/{tail:.*}", index_handler)
    return app


def _resolve_static_dir(raw: Path) -> Path:
    """Resolve a static dir path.

    A relative path (the default ``webui/dist``) is resolved against the
    repository root first, then falls back to the process CWD. This keeps
    ``dext monitor serve`` working regardless of the directory it is launched
    from. Absolute paths are used as-is.
    """
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    repo_root = Path(__file__).resolve().parents[2]
    here = (repo_root / candidate).resolve()
    if (here / "index.html").is_file():
        return here
    return (Path.cwd() / candidate).resolve()


def run_server(settings: MonitorSettings | None = None) -> None:
    settings = settings or MonitorSettings()
    web.run_app(
        create_app(settings),
        host=settings.monitor_host,
        port=settings.monitor_port,
        print=lambda message: print(message),  # noqa: T201 - CLI server startup output
    )


__all__ = ["API_PREFIX", "MonitorNotFoundError", "create_app", "run_server"]
