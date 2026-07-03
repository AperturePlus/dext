from __future__ import annotations

from datetime import timezone

from aiohttp import web

from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ok, read_json
from dext_recommend.api.schemas import UserFeedbackRequest
from dext_recommend.app_state.models import utcnow


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [web.post(f"{prefix}/feedback", handle_feedback)]


async def handle_feedback(request: web.Request) -> web.Response:
    await require_principal(request)
    dto = UserFeedbackRequest.model_validate(await read_json(request))
    received_at = utcnow().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return ok({"id": dto.id, "status": "received", "received_at": received_at})


__all__ = ["routes"]
