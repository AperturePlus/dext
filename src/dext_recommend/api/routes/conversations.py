from __future__ import annotations

from aiohttp import web

from dext_recommend.api.adapters import (
    conversation_dispatch_to_answer,
    recommend_request_from_public,
)
from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ApiError, ok, read_json
from dext_recommend.api.routes._utils import (
    idempotency_key,
    repository,
    runtime,
    services,
    write_sse,
)
from dext_recommend.api.schemas import (
    AttemptCreateRequest,
    ChatMessageRequest,
    FeedbackRequest,
    ForkCreateRequest,
    QuickActionsRequest,
    SessionCreateRequest,
    TurnCreateRequest,
)


def routes(prefix: str) -> list[web.AbstractRouteDef]:
    return [
        web.post(f"{prefix}/chat/sessions", handle_create_session),
        web.get(f"{prefix}/chat/sessions", handle_list_sessions),
        web.get(f"{prefix}/chat/sessions/{{session_id}}", handle_get_session),
        web.delete(f"{prefix}/chat/sessions/{{session_id}}", handle_delete_session),
        web.get(f"{prefix}/chat/sessions/{{session_id}}/turns", handle_list_turns),
        web.post(f"{prefix}/chat/sessions/{{session_id}}/turns", handle_create_turn),
        web.get(f"{prefix}/chat/sessions/{{session_id}}/forks", handle_list_forks),
        web.post(f"{prefix}/chat/sessions/{{session_id}}/forks", handle_create_fork),
        web.post(f"{prefix}/chat/turns/{{turn_id}}/attempts", handle_create_attempt),
        web.post(f"{prefix}/chat/attempts/{{attempt_id}}/cancel", handle_cancel_attempt),
        web.patch(f"{prefix}/chat/messages/{{message_id}}/feedback", handle_feedback),
        web.post(f"{prefix}/chat/messages", handle_legacy_message),
        web.get(f"{prefix}/chat/stream", handle_legacy_stream),
        web.post(f"{prefix}/chat/route", handle_chat_route),
        web.post(f"{prefix}/chat/quick-actions", handle_quick_actions),
    ]


async def handle_create_session(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = SessionCreateRequest.model_validate(await read_json(request))
    return ok(await repository(request).create_session(
        str(principal.owner_id),
        kind=dto.kind,
        professor_id=dto.professor_id,
    ))


async def handle_list_sessions(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok({"items": await repository(request).list_sessions(str(principal.owner_id))})


async def handle_get_session(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok(await repository(request).get_session_projection(
        str(principal.owner_id),
        request.match_info["session_id"],
    ))


async def handle_delete_session(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    await repository(request).soft_delete_session_tree(
        str(principal.owner_id),
        request.match_info["session_id"],
    )
    return ok({"deleted": True})


async def handle_list_turns(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    turns = await repository(request).list_turns(
        str(principal.owner_id),
        request.match_info["session_id"],
    )
    flat_turns: list[dict] = []
    messages: list[dict] = []
    for turn in turns:
        item = dict(turn)
        messages.extend(item.pop("messages", []))
        flat_turns.append(item)
    return ok({"turns": flat_turns, "messages": messages})


async def handle_create_turn(request: web.Request) -> web.StreamResponse:
    principal = await require_principal(request)
    dto = TurnCreateRequest.model_validate(await read_json(request))
    try:
        idem = idempotency_key(request)
        if idem != str(dto.request_id):
            raise ApiError(422, "idempotency_key_mismatch", "Idempotency-Key must equal request_id")
        result = await services(request).create_turn_and_dispatch(
            principal,
            session_id=request.match_info["session_id"],
            text=dto.text,
            request_id=str(dto.request_id),
            expected_revision=dto.expected_revision,
            idempotency_key=idem,
        )
        return await write_sse(request, _sse_events(result))
    except Exception as exc:
        data = _sse_error_data(
            exc,
            session_id=request.match_info["session_id"],
        )
        return await write_sse(request, [("error", data)])


async def handle_list_forks(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    return ok({"items": await repository(request).list_forks(
        str(principal.owner_id),
        request.match_info["session_id"],
    )})


async def handle_create_fork(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = ForkCreateRequest.model_validate(await read_json(request))
    return ok(await repository(request).create_fork(
        str(principal.owner_id),
        request.match_info["session_id"],
        str(dto.source_turn_id),
        dto.professor_id,
    ))


async def handle_create_attempt(request: web.Request) -> web.StreamResponse:
    principal = await require_principal(request)
    dto = AttemptCreateRequest.model_validate(await read_json(request))
    try:
        idem = idempotency_key(request)
        if idem != str(dto.request_id):
            raise ApiError(422, "idempotency_key_mismatch", "Idempotency-Key must equal request_id")
        result = await services(request).retry_turn_and_dispatch(
            principal,
            turn_id=request.match_info["turn_id"],
            session_id=str(dto.session_id),
            request_id=str(dto.request_id),
            expected_revision=dto.expected_revision,
            idempotency_key=idem,
        )
        return await write_sse(request, _sse_events(result))
    except Exception as exc:
        data = _sse_error_data(
            exc,
            session_id=str(getattr(locals().get("dto", None), "session_id", "")),
            turn_id=request.match_info["turn_id"],
        )
        return await write_sse(request, [("error", data)])


async def handle_cancel_attempt(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    result = await repository(request).cancel_attempt(
        str(principal.owner_id),
        request.match_info["attempt_id"],
    )
    services(request).attempts.cancel(str(principal.owner_id), request.match_info["attempt_id"])
    return ok({"interrupted": result.get("status") == "interrupted"})


async def handle_feedback(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = FeedbackRequest.model_validate(await read_json(request))
    await repository(request).patch_feedback(
        str(principal.owner_id),
        request.match_info["message_id"],
        dto.feedback,
    )
    return ok({"updated": True})


async def handle_legacy_message(request: web.Request) -> web.Response:
    principal = await require_principal(request)
    dto = ChatMessageRequest.model_validate(await read_json(request))
    rt = runtime(request)
    req = recommend_request_from_public(
        prompt=dto.message,
        profile=None,
        professor_id=dto.professor_id,
        limit=10,
    )
    result = await rt.conversation.dispatch(
        req,
        viewer_permissions=principal.viewer_permissions(),
    )
    answer, related, _ = conversation_dispatch_to_answer(result)
    return ok({
        "session_id": str(dto.session_id),
        "answer": answer,
        "related_recommendations": related,
    })


async def handle_legacy_stream(request: web.Request) -> web.StreamResponse:
    principal = await require_principal(request)
    message = request.query.get("message")
    session_id = request.query.get("session_id")
    if not message or not session_id:
        raise ApiError(422, "invalid_request", "session_id and message are required")
    rt = runtime(request)
    req = recommend_request_from_public(
        prompt=message,
        profile=None,
        professor_id=request.query.get("professor_id"),
        limit=10,
    )
    result = await rt.conversation.dispatch(
        req,
        viewer_permissions=principal.viewer_permissions(),
    )
    answer, related, _ = conversation_dispatch_to_answer(result)
    return await write_sse(request, [
        ("delta", {"text": answer}),
        ("related_recommendations", {"items": related}),
        ("done", {"session_id": session_id}),
    ])


async def handle_chat_route(request: web.Request) -> web.Response:
    await require_principal(request)
    body = await read_json(request)
    follow_up = str(body.get("follow_up") or "").strip()
    if not follow_up:
        raise ApiError(422, "invalid_request", "follow_up is required")
    keywords = ("推荐", "换", "筛选", "只看", "方向", "导师", "教授", "学校", "地区", "城市", "985", "211")
    return ok({"need": any(word in follow_up for word in keywords)})


async def handle_quick_actions(request: web.Request) -> web.Response:
    await require_principal(request)
    dto = QuickActionsRequest.model_validate(await read_json(request))
    quick_actions = await runtime(request).quick_actions.generate(
        dto.follow_up,
        [item.model_dump(mode="json") for item in dto.last_recommendations],
    )
    return ok({"quick_actions": quick_actions})


def _sse_events(result: dict) -> list[tuple[str, dict]]:
    turn = result.get("turn") or {}
    message = result.get("assistant_message")
    session = result.get("session") or {}
    session_id = str(session.get("id") or turn.get("session_id") or "")
    turn_id = str(turn.get("id") or "")
    attempt_id = str(result.get("attempt_id") or result.get("active_attempt_id") or "")
    revision = int(session.get("revision") or result.get("revision") or 0)
    route = turn.get("route") or "conversation"
    base = {
        "session_id": session_id,
        "turn_id": turn_id,
        "attempt_id": attempt_id,
        "revision": revision,
    }
    completed = {
        **base,
        "revision": int(session.get("revision") or revision),
        "session": session,
        "message": message,
        "quick_actions": result.get("quick_actions") or [],
    }
    events: list[tuple[str, dict]] = [
        ("ack", base),
        ("route", {**base, "route": route}),
    ]
    if message and message.get("content"):
        events.append(("delta", {**base, "text": message["content"]}))
    events.append(("completed", completed))
    return events


def _sse_error_data(exc: Exception, *, session_id: str = "", turn_id: str = "", attempt_id: str = "") -> dict:
    if isinstance(exc, ApiError):
        code = exc.error_code
        message = exc.message
    else:
        code = str(getattr(exc, "error_code", "chat_stream_failed"))
        message = str(exc) if str(exc) else "chat stream failed"
    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "attempt_id": attempt_id,
        "revision": 0,
        "code": code,
        "message": message,
    }
