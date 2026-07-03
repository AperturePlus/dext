from __future__ import annotations

from aiohttp import web

from dext_recommend.api.auth import (
    create_anonymous_identity,
    require_principal,
    set_identity_cookies,
)
from dext_recommend.api.middleware import ok
from dext_recommend.api.routes._utils import repository


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.post(f"{prefix}/identity/anonymous", handle_create_anonymous),
        web.get(f"{prefix}/account/remote-data", handle_remote_data),
        web.delete(f"{prefix}/account/remote-data", handle_delete_remote_data),
    ]


async def handle_create_anonymous(request: web.Request) -> web.Response:
    body, token, csrf = await create_anonymous_identity(request)
    response = ok(body)
    set_identity_cookies(response, token, csrf, request)
    return response


async def handle_remote_data(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    counts = await repository(request).remote_counts(str(principal.owner_id))
    return ok({
        "engine": "postgresql",
        "dsn_env": "DEXT_APP_DATABASE_URL",
        "buckets": counts,
        "excluded_resources": [
            "catalog_sqlite",
            "neo4j_active_graph",
            "qdrant_indexes",
            "competition_knowledge_base",
        ],
    })


async def handle_delete_remote_data(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    counts = await repository(request).delete_remote_data(str(principal.owner_id))
    return ok({
        "deleted": counts,
        "excluded_resources": [
            "catalog_sqlite",
            "neo4j_active_graph",
            "qdrant_indexes",
            "competition_knowledge_base",
        ],
    })
