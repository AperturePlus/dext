from __future__ import annotations

from aiohttp import web

from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ok, read_json
from dext_recommend.api.routes._utils import repository, runtime
from dext_recommend.api.schemas import UserProfile


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/profile", handle_get_profile),
        web.put(f"{prefix}/profile", handle_put_profile),
        web.delete(f"{prefix}/profile", handle_delete_profile),
        web.post(f"{prefix}/profile/achievements/extract", handle_extract_achievements),
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
    return ok({"cleared": True})


async def handle_extract_achievements(request: web.Request) -> web.Response:
    await require_principal(request)
    body = await read_json(request)
    raw_text = str(body.get("raw_text") or "").strip()
    if not raw_text:
        return ok({"competitions": [], "research": []})
    return ok(await runtime(request).achievement_extraction.extract(raw_text))
