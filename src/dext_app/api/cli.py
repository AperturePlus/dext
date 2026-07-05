"""Command-line entrypoint for the combined Flutter-facing API."""
from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import web

from dext_app.api.app import create_app, prepare_competition_artifacts
from dext_competition.catalog import CatalogArtifactError, CatalogExtractionError
from dext_competition.index import IndexArtifactError, KnowledgeSourceReadError
from dext_recommend.api.access_log import RecommendAccessLogger
from dext_recommend.api.settings import AppSettings
from dext_recommend.app_state.db import SchemaNotReadyError


logger = logging.getLogger("dext_app.api")
access_logger = logging.getLogger("dext_app.api.access")


def _configure_logging(log_level: str) -> None:
    level = getattr(logging, log_level.upper())
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s", force=True)
    logging.getLogger().setLevel(level)


def _log_startup_banner(message: str) -> None:
    for line in str(message).splitlines():
        logger.info(line)


def _settings_from_args(args) -> AppSettings:
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
    return settings.model_copy(update=update) if update else settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dext-api")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--dev-bootstrap-schema", action="store_true")
    serve.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error", "critical"),
        help="Console log level for the combined API server.",
    )
    serve.add_argument("--no-access-log", action="store_true", help="Disable per-request access logs.")
    args = parser.parse_args(argv)
    if args.command != "serve":
        return

    settings = _settings_from_args(args)
    _configure_logging(settings.log_level)
    try:
        asyncio.run(prepare_competition_artifacts())
        app = create_app(settings)
        web.run_app(
            app,
            host=settings.http_host,
            port=settings.http_port,
            print=_log_startup_banner,
            access_log=access_logger if settings.access_log_enabled else None,
            access_log_class=RecommendAccessLogger,
        )
    except SchemaNotReadyError as exc:
        parser.exit(1, f"Error: recommendation app-state schema is not ready: {exc}\n")
    except ConnectionRefusedError as exc:
        parser.exit(1, f"Error: recommendation app-state database is unavailable: {exc}\n")
    except (
        CatalogArtifactError,
        CatalogExtractionError,
        IndexArtifactError,
        KnowledgeSourceReadError,
        OSError,
        ValueError,
    ) as exc:
        parser.exit(
            1,
            "Error: competition artifacts could not be prepared automatically.\n"
            f"Cause: {exc}\n",
        )


if __name__ == "__main__":
    main()


__all__ = ["main"]
