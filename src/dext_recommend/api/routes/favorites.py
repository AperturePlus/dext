from __future__ import annotations

from aiohttp import web

from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ApiError, ok, read_json
from dext_recommend.api.routes._utils import repository
from dext_recommend.api.schemas import FavoriteRequest


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/favorites", handle_list_favorites),
        web.put(f"{prefix}/favorites/{{professor_id}}", handle_put_favorite),
        web.delete(f"{prefix}/favorites/{{professor_id}}", handle_delete_favorite),
    ]


async def handle_list_favorites(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok(await repository(request).list_favorites(str(principal.owner_id)))


async def handle_put_favorite(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    professor_id = request.match_info["professor_id"]
    body = FavoriteRequest.model_validate(await read_json(request))
    if body.professor_id is not None and body.professor_id != professor_id:
        raise ApiError(422, "professor_id_mismatch", "path professor_id and body professor_id differ")
    item = body.model_dump(mode="json", exclude_none=True)
    return ok(await repository(request).put_favorite(
        str(principal.owner_id), professor_id, item,
    ))


async def handle_delete_favorite(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok(await repository(request).delete_favorite(
        str(principal.owner_id), request.match_info["professor_id"],
    ))
