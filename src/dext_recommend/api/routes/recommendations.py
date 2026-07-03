from __future__ import annotations

from aiohttp import web

from dext_recommend.api.adapters import (
    recommend_request_from_public,
    recommendation_response_to_public,
)
from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ok, read_json
from dext_recommend.api.routes._utils import raise_if_domain_error, runtime
from dext_recommend.api.schemas import MentorRecommendationRequest


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [web.post(f"{prefix}/recommendations/mentors", handle_recommend_mentors)]


async def handle_recommend_mentors(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = MentorRecommendationRequest.model_validate(await read_json(request))
    req = recommend_request_from_public(
        prompt=dto.prompt,
        profile=dto.profile,
        session_id=str(dto.session_id) if dto.session_id else None,
        limit=dto.limit,
    )
    response = await runtime(request).core.recommend(
        req,
        viewer_permissions=principal.viewer_permissions(),
    )
    raise_if_domain_error(response.warnings)
    return ok(recommendation_response_to_public(
        response,
        session_id=str(dto.session_id) if dto.session_id else None,
    ))
