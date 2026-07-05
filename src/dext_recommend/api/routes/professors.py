from __future__ import annotations

from aiohttp import web

from dext_recommend.api.adapters import (
    auxiliary_result_to_public,
    professor_detail_to_public,
    student_context_from_profile,
)
from dext_recommend.api.auth import require_principal
from dext_recommend.api.keys import REQUEST_ID_KEY
from dext_recommend.api.middleware import ApiError, ok, read_json
from dext_recommend.api.routes._utils import raise_if_domain_error, runtime
from dext_recommend.api.schemas import (
    MatchAnalysisRequest,
    OutreachEmailRequest,
    ProfessorCompareRequest,
)
from dext_recommend.ports import ViewerPermissions


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/professors/{{professor_id}}", handle_get_professor),
        web.post(f"{prefix}/professors/compare", handle_compare),
        web.post(f"{prefix}/professors/{{professor_id}}/match-analysis", handle_match),
        web.post(f"{prefix}/professors/{{professor_id}}/outreach-email", handle_outreach),
    ]


async def handle_get_professor(request: web.Request) -> web.Response:
    rt = runtime(request)
    snapshot = rt.readiness.get_snapshot()
    if snapshot is None:
        raise ApiError(503, "active_build_unavailable", "no ACTIVE build")
    try:
        detail = await rt.core.deps.facts_port.get_detail(
            snapshot,
            request.match_info["professor_id"],
            include_contacts=False,
            viewer_permissions=ViewerPermissions(),
        )
    except (LookupError, KeyError) as exc:
        raise ApiError(404, "professor_not_found", "professor not found") from exc
    if detail.role_status in {"excluded", "review"}:
        raise ApiError(404, "professor_not_found", "professor not found")
    return ok(professor_detail_to_public(detail))


async def handle_compare(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = ProfessorCompareRequest.model_validate(await read_json(request))
    result = await runtime(request).auxiliary_generation.compare_professors(
        tuple(dto.professor_ids),
        None,
        "strict",
        viewer_permissions=principal.viewer_permissions(),
        request_id=request.get(REQUEST_ID_KEY, ""),
    )
    raise_if_domain_error(result.issues)
    return ok(auxiliary_result_to_public(result))


async def handle_match(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = MatchAnalysisRequest.model_validate(await read_json(request))
    result = await runtime(request).auxiliary_generation.analyze_match(
        request.match_info["professor_id"],
        student_context_from_profile(dto.profile),
        "strict",
        viewer_permissions=principal.viewer_permissions(),
        request_id=request.get(REQUEST_ID_KEY, ""),
    )
    raise_if_domain_error(result.issues)
    return ok(auxiliary_result_to_public(result))


async def handle_outreach(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = OutreachEmailRequest.model_validate(await read_json(request))
    result = await runtime(request).auxiliary_generation.draft_outreach_email(
        request.match_info["professor_id"],
        student_context_from_profile(dto.profile),
        "professional",
        dto.locale or "zh",
        include_contacts=False,
        viewer_permissions=principal.viewer_permissions(),
        request_id=request.get(REQUEST_ID_KEY, ""),
    )
    raise_if_domain_error(result.issues)
    return ok(auxiliary_result_to_public(result))
