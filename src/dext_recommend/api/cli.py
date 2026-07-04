"""Command-line entrypoint for the recommendation HTTP API."""
from __future__ import annotations

import argparse
import logging
from urllib.parse import urlsplit

from aiohttp import web

from dext_recommend.api.access_log import RecommendAccessLogger
from dext_recommend.api.app import create_recommendation_app
from dext_recommend.api.settings import AppSettings
from dext_recommend.app_state.db import SchemaNotReadyError


logger = logging.getLogger("dext_recommend.api")
access_logger = logging.getLogger("dext_recommend.api.access")

_SCHEMA_MISSING_PREFIX = "recommendation app-state schema is missing tables: "
_SCHEMA_MISSING_SUFFIX = "; enable dev schema bootstrap or run the migration follow-up"


def _format_database_endpoint(database_url: str) -> str:
    parsed = urlsplit(database_url)
    host = parsed.hostname or "configured host"
    port = parsed.port
    if port is None and parsed.scheme in {"postgresql", "postgresql+asyncpg"}:
        port = 5432
    return f"{host}:{port}" if port is not None else host


def _format_database_unavailable_error(settings: AppSettings, exc: ConnectionRefusedError) -> str:
    endpoint = _format_database_endpoint(settings.database_url)
    cause = str(exc) or exc.__class__.__name__
    return (
        f"Error: recommendation app-state database is unavailable at {endpoint}.\n"
        "Hint: start local data services with `docker compose -f docker/compose.yaml up -d`, "
        "or point DEXT_APP_DATABASE_URL at a reachable database.\n"
        f"Cause: {cause}\n"
    )


def _format_schema_not_ready_error(exc: SchemaNotReadyError) -> str:
    detail = str(exc)
    if detail.startswith(_SCHEMA_MISSING_PREFIX):
        missing = detail[len(_SCHEMA_MISSING_PREFIX):]
        if missing.endswith(_SCHEMA_MISSING_SUFFIX):
            missing = missing[: -len(_SCHEMA_MISSING_SUFFIX)]
        detail = f"missing tables: {missing}"
    return (
        f"Error: recommendation app-state schema is not ready: {detail}\n"
        "Hint: run with --dev-bootstrap-schema for local dev, or run the app-state migration.\n"
    )


def _configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper())
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s", force=True)
    logging.getLogger().setLevel(level)


def _log_startup_banner(message: str) -> None:
    for line in str(message).splitlines():
        logger.info(line)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="recommend")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--dev-bootstrap-schema", action="store_true")
    serve.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error", "critical"),
        help="Console log level for the recommendation API server.",
    )
    serve.add_argument("--no-access-log", action="store_true", help="Disable per-request access logs.")
    args = parser.parse_args(argv)
    if args.command == "serve":
        settings = AppSettings()
        update = {}
        if args.host:
            update["http_host"] = args.host
        if args.port:
            update["http_port"] = args.port
        if args.dev_bootstrap_schema:
            update["schema_bootstrap"] = True
        if args.log_level:
            update["log_level"] = args.log_level.upper()
        if args.no_access_log:
            update["access_log_enabled"] = False
        if update:
            settings = settings.model_copy(update=update)
        _configure_logging(settings.log_level)
        try:
            web.run_app(
                create_recommendation_app(settings),
                host=settings.http_host,
                port=settings.http_port,
                print=_log_startup_banner,
                access_log=access_logger if settings.access_log_enabled else None,
                access_log_class=RecommendAccessLogger,
            )
        except SchemaNotReadyError as exc:
            parser.exit(1, _format_schema_not_ready_error(exc))
        except ConnectionRefusedError as exc:
            parser.exit(1, _format_database_unavailable_error(settings, exc))


if __name__ == "__main__":
    main()
