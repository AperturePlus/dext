"""Command-line entrypoint for the recommendation HTTP API."""
from __future__ import annotations

import argparse

from aiohttp import web

from dext_recommend.api.app import create_recommendation_app
from dext_recommend.api.settings import AppSettings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="recommend")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--dev-bootstrap-schema", action="store_true")
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
        if update:
            settings = settings.model_copy(update=update)
        web.run_app(
            create_recommendation_app(settings),
            host=settings.http_host,
            port=settings.http_port,
            print=lambda message: print(message),  # noqa: T201
        )


if __name__ == "__main__":
    main()
