"""C7 owned HTTP routes for competition and preparation features."""
from __future__ import annotations

import uuid

from aiohttp import web

from dext_competition.assistant import suggest_plan_changes
from dext_competition.errors import CompetitionErrorCode, ErrorSeverity
from dext_competition.http.adapters import (
    assistant_history_turn_to_public,
    assistant_request_from_public,
    assistant_result_to_public,
    catalog_card_to_public,
    competition_detail_to_public,
    plan_generation_request_from_public,
    plan_generation_result_to_public,
    plan_snapshot_to_public_dict,
    recommendation_request_from_public,
    recommendation_response_to_public,
    template_phase_to_public,
)
from dext_competition.http.auth import require_owner_id
from dext_competition.http.errors import ApiError, ok, read_json
from dext_competition.http.keys import (
    ASSISTANT_DEPS_KEY,
    ASSISTANT_HISTORY_REPOSITORY_KEY,
    PLAN_GENERATOR_KEY,
    PLAN_REPOSITORY_KEY,
    RECOMMENDATION_SERVICE_KEY,
)
from dext_competition.http.schemas import (
    CompetitionRecommendationRequest,
    PreparationAssistantPlanSnapshot,
    PreparationAssistantRequest,
    PreparationDiagnoseRequest,
    PreparationPlanGenerateRequest,
)
from dext_competition.planning import diagnose_preparation_level
from dext_competition.planning.templates import build_phase_templates
from dext_competition.qa import get_competition_detail
from dext_competition.repositories import PlanNotFoundError, PlanRevisionConflictError


def _recommendation_service(request: web.Request):
    return request.app[RECOMMENDATION_SERVICE_KEY]


def _plan_generator(request: web.Request):
    return request.app[PLAN_GENERATOR_KEY]


def _assistant_deps(request: web.Request):
    return request.app[ASSISTANT_DEPS_KEY]


def _plan_repo(request: web.Request):
    return request.app[PLAN_REPOSITORY_KEY]


def _history_repo(request: web.Request):
    return request.app[ASSISTANT_HISTORY_REPOSITORY_KEY]


def _idempotency_key(request: web.Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value:
        raise ApiError(400, "missing_idempotency_key", "Idempotency-Key header is required")
    if len(value) > 128:
        raise ApiError(422, "invalid_idempotency_key", "Idempotency-Key is too long")
    return value


def _raise_for_issues(issues) -> None:
    fatal = [issue for issue in issues if getattr(issue, "severity", None) == ErrorSeverity.ERROR]
    if not fatal:
        return
    first = fatal[0]
    code = first.code.value if hasattr(first.code, "value") else str(first.code)
    status = 500
    if code in {CompetitionErrorCode.INVALID_REQUEST.value, CompetitionErrorCode.PLAN_INVALID.value}:
        status = 422
    elif code == CompetitionErrorCode.PLAN_REVISION_STALE.value:
        status = 409
    elif code in {
        CompetitionErrorCode.CATALOG_UNAVAILABLE.value,
        CompetitionErrorCode.KNOWLEDGE_BASE_UNAVAILABLE.value,
        CompetitionErrorCode.GENERATION_UNAVAILABLE.value,
        CompetitionErrorCode.LLM_UNAVAILABLE.value,
    }:
        status = 503
    raise ApiError(status, code, first.message)


def _required_query(request: web.Request, key: str) -> str:
    value = request.query.get(key)
    if value is None or not value.strip():
        raise ApiError(422, "invalid_request", f"{key} query parameter is required")
    return value.strip()


def _required_bool_query(request: web.Request, key: str) -> bool:
    value = _required_query(request, key).lower()
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ApiError(422, "invalid_request", f"{key} must be true, false, 1, or 0")


def _competition_session_id(value: str | None) -> str:
    if value is not None and value.strip():
        return value.strip()
    return f"c_{uuid.uuid4().hex}"


async def handle_list_competitions(request: web.Request) -> web.Response:
    catalog = _recommendation_service(request)._deps.catalog_port
    cards = await catalog.list_competitions()
    return ok([catalog_card_to_public(card) for card in cards])


async def handle_get_competition(request: web.Request) -> web.Response:
    catalog = _recommendation_service(request)._deps.catalog_port
    try:
        detail = await get_competition_detail(catalog, request.match_info["competition_id"])
    except KeyError as exc:
        raise ApiError(404, "competition_not_found", "competition not found") from exc
    return ok(competition_detail_to_public(detail))


async def handle_recommend_competitions(request: web.Request) -> web.Response:
    dto = CompetitionRecommendationRequest.model_validate(await read_json(request))
    response = await _recommendation_service(request).recommend(
        recommendation_request_from_public(dto)
    )
    session_id = _competition_session_id(dto.session_id)
    return ok(recommendation_response_to_public(response, session_id=session_id))


async def handle_generate_plan(request: web.Request) -> web.Response:
    await require_owner_id(request)
    dto = PreparationPlanGenerateRequest.model_validate(await read_json(request))
    result = await _plan_generator(request).generate(plan_generation_request_from_public(dto))
    _raise_for_issues(result.issues)
    assert result.draft is not None
    return ok(plan_generation_result_to_public(result.draft))


async def handle_list_plans(request: web.Request) -> web.Response:
    owner_id = await require_owner_id(request)
    return ok(await _plan_repo(request).list_plans(owner_id))


async def handle_create_plan(request: web.Request) -> web.Response:
    owner_id = await require_owner_id(request)
    dto = PreparationAssistantPlanSnapshot.model_validate(await read_json(request))
    try:
        saved = await _plan_repo(request).create_or_replace_plan(
            owner_id,
            plan_snapshot_to_public_dict(dto),
            idempotency_key=_idempotency_key(request),
        )
    except PlanRevisionConflictError as exc:
        raise ApiError(409, CompetitionErrorCode.PLAN_REVISION_STALE.value, str(exc)) from exc
    return ok(saved)


async def handle_get_plan(request: web.Request) -> web.Response:
    owner_id = await require_owner_id(request)
    plan = await _plan_repo(request).get_plan(owner_id, request.match_info["plan_id"])
    if plan is None:
        raise ApiError(404, "plan_not_found", "preparation plan not found")
    return ok(plan)


async def handle_put_plan(request: web.Request) -> web.Response:
    owner_id = await require_owner_id(request)
    plan_id = request.match_info["plan_id"]
    dto = PreparationAssistantPlanSnapshot.model_validate(await read_json(request))
    try:
        saved = await _plan_repo(request).save_new_revision(
            owner_id,
            plan_id,
            plan_snapshot_to_public_dict(dto),
            idempotency_key=_idempotency_key(request),
        )
    except PlanNotFoundError as exc:
        raise ApiError(404, "plan_not_found", "preparation plan not found") from exc
    except PlanRevisionConflictError as exc:
        raise ApiError(409, CompetitionErrorCode.PLAN_REVISION_STALE.value, str(exc)) from exc
    return ok(saved)


async def handle_delete_plan(request: web.Request) -> web.Response:
    owner_id = await require_owner_id(request)
    plan_id = request.match_info["plan_id"]
    deleted = await _plan_repo(request).delete_plan(owner_id, plan_id)
    await _history_repo(request).delete_plan(owner_id, plan_id)
    if not deleted:
        raise ApiError(404, "plan_not_found", "preparation plan not found")
    return ok({"deleted": True})


async def handle_preparation_config(request: web.Request) -> web.Response:
    await require_owner_id(request)
    return ok({
        "category_aliases": {
            "计算机": "计算机",
            "电子信息": "电子与信息",
            "机器人": "机器人与人工智能",
            "数学建模": "数学建模",
        },
        "timeline_defaults": {
            "计算机": "submission",
            "数学建模": "eventWindow",
        },
        "prior_experience_options": ["beginner", "intermediate", "experienced"],
        "domain_familiarity_options": ["low", "medium", "high"],
    })


async def handle_preparation_template(request: web.Request) -> web.Response:
    await require_owner_id(request)
    competition_id = _required_query(request, "competition_id")
    category = _required_query(request, "category")
    timeline_type = _required_query(request, "timeline_type")
    include_defense = _required_bool_query(request, "include_defense")
    if timeline_type not in {"eventWindow", "submission"}:
        raise ApiError(422, "invalid_request", "timeline_type must be eventWindow or submission")
    if timeline_type == "eventWindow" and include_defense:
        raise ApiError(422, "invalid_request", "eventWindow templates cannot include defense")
    catalog = _recommendation_service(request)._deps.catalog_port
    card = await catalog.get(competition_id)
    if card is None:
        raise ApiError(404, "competition_not_found", "competition not found")
    if card.category != category:
        raise ApiError(404, "competition_not_found", "competition category mismatch")
    time_model = "competition_window" if timeline_type == "eventWindow" else "submission_deadline"
    phases = build_phase_templates(
        card,
        time_model=time_model,
        experience_level="beginner",
        include_defense=include_defense,
    )
    return ok({"phases": [template_phase_to_public(phase) for phase in phases]})


async def handle_diagnose_plan(request: web.Request) -> web.Response:
    await require_owner_id(request)
    dto = PreparationDiagnoseRequest.model_validate(await read_json(request))
    result = diagnose_preparation_level(
        [item.model_dump(mode="json") for item in dto.answers],
        profile=dto.profile,
    )
    return ok({
        "level": result.level,
        "rationale": result.rationale,
        "suggestion": result.suggestion,
    })


async def handle_assistant(request: web.Request) -> web.Response:
    owner_id = await require_owner_id(request)
    plan_id = request.match_info["plan_id"]
    dto = PreparationAssistantRequest.model_validate(await read_json(request))
    if dto.plan_snapshot.id != plan_id:
        raise ApiError(422, "invalid_request", "path plan_id must match plan_snapshot.id")
    persisted = await _plan_repo(request).get_plan(owner_id, plan_id)
    result = await suggest_plan_changes(
        assistant_request_from_public(dto),
        _assistant_deps(request),
    )
    _raise_for_issues(result.issues)
    assert result.result is not None
    payload = assistant_result_to_public(result.result)
    if persisted is not None:
        await _history_repo(request).append_turn(
            owner_id,
            plan_id,
            assistant_history_turn_to_public(role="user", content=dto.user_message),
        )
        await _history_repo(request).append_turn(
            owner_id,
            plan_id,
            assistant_history_turn_to_public(
                role="assistant",
                content=result.result.reply,
                cards=payload["change_set"]["cards"],
            ),
        )
    return ok(payload)


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.get(f"{prefix}/competitions", handle_list_competitions),
        web.get(f"{prefix}/competitions/{{competition_id}}", handle_get_competition),
        web.post(f"{prefix}/recommendations/competitions", handle_recommend_competitions),
        web.post(f"{prefix}/preparation-plans/generate", handle_generate_plan),
        web.get(f"{prefix}/preparation-plans", handle_list_plans),
        web.post(f"{prefix}/preparation-plans", handle_create_plan),
        web.get(f"{prefix}/preparation-plans/{{plan_id}}", handle_get_plan),
        web.put(f"{prefix}/preparation-plans/{{plan_id}}", handle_put_plan),
        web.delete(f"{prefix}/preparation-plans/{{plan_id}}", handle_delete_plan),
        web.get(f"{prefix}/preparation/config", handle_preparation_config),
        web.get(f"{prefix}/preparation-templates", handle_preparation_template),
        web.post(f"{prefix}/preparation-plans/diagnose", handle_diagnose_plan),
        web.post(f"{prefix}/preparation-plans/{{plan_id}}/assistant", handle_assistant),
    ]


__all__ = ["routes"]
