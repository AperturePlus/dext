from __future__ import annotations

from aiohttp import web

from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ok, read_json
from dext_recommend.api.routes._utils import repository
from dext_recommend.api.schemas import HistoryItem


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/history", handle_list_history),
        web.post(f"{prefix}/history", handle_put_history),
        web.delete(f"{prefix}/history", handle_delete_history),
        web.delete(f"{prefix}/history/{{session_id}}", handle_delete_history_session),
    ]


async def handle_list_history(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok(await repository(request).list_history(str(principal.owner_id)))


async def handle_put_history(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = HistoryItem.model_validate(await read_json(request))
    return ok(await repository(request).put_history(
        str(principal.owner_id), dto.model_dump(mode="json"),
    ))


async def handle_delete_history(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    await repository(request).delete_history(str(principal.owner_id))
    return ok({"cleared": True})


async def handle_delete_history_session(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    await repository(request).delete_history(str(principal.owner_id), request.match_info["session_id"])
    return ok({"removed": True})
