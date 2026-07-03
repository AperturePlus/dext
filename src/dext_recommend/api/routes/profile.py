from __future__ import annotations

from aiohttp import web

from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ok, read_json
from dext_recommend.api.routes._utils import repository
from dext_recommend.api.schemas import UserProfile


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/profile", handle_get_profile),
        web.put(f"{prefix}/profile", handle_put_profile),
        web.delete(f"{prefix}/profile", handle_delete_profile),
    ]


async def handle_get_profile(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok(await repository(request).get_profile(str(principal.owner_id)))


async def handle_put_profile(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = UserProfile.model_validate(await read_json(request))
    data = dto.model_dump(mode="json", exclude_none=True)
    return ok(await repository(request).put_profile(str(principal.owner_id), data))


async def handle_delete_profile(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    await repository(request).delete_profile(str(principal.owner_id))
    return ok(None)
