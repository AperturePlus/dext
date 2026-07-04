from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from aiohttp import web

from dext_recommend.api.adapters import (
    conversation_dispatch_to_answer,
    recommend_request_from_public,
)
from dext_recommend.api.auth import require_principal
from dext_recommend.api.middleware import ApiError, ok, read_json
from dext_recommend.api.keys import REQUEST_ID_KEY, SETTINGS_KEY
from dext_recommend.api.routes._utils import (
    SseWriter,
    idempotency_key,
    prepare_sse,
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

logger = logging.getLogger(__name__)


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
    idem = idempotency_key(request)
    if idem != str(dto.request_id):
        raise ApiError(422, "idempotency_key_mismatch", "Idempotency-Key must equal request_id")
    admitted = await services(request).admit_turn(
        principal,
        session_id=request.match_info["session_id"],
        text=dto.text,
        request_id=str(dto.request_id),
        expected_revision=dto.expected_revision,
        idempotency_key=idem,
    )
    return await _stream_admitted_attempt(request, principal, admitted, text=dto.text)


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
    idem = idempotency_key(request)
    if idem != str(dto.request_id):
        raise ApiError(422, "idempotency_key_mismatch", "Idempotency-Key must equal request_id")
    admitted = await services(request).admit_retry_attempt(
        principal,
        turn_id=request.match_info["turn_id"],
        session_id=str(dto.session_id),
        request_id=str(dto.request_id),
        expected_revision=dto.expected_revision,
        idempotency_key=idem,
    )
    return await _stream_admitted_attempt(request, principal, admitted, text=admitted["text"])


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


async def _stream_admitted_attempt(
    request: web.Request,
    principal,
    admitted: dict,
    *,
    text: str,
) -> web.StreamResponse:
    response = await prepare_sse(request)
    writer = SseWriter(response)
    setattr(response, "_dext_sse_writer", writer)
    base = _sse_base(admitted)
    owner_id = str(principal.owner_id)
    start = time.perf_counter()
    heartbeat_count = 0
    terminal = "unknown"
    route = None
    replay = bool(admitted.get("replay"))
    result_ready = replay
    await writer.event("ack", base)
    try:
        if replay:
            result = await services(request).completed_attempt_result(
                principal,
                session_id=base["session_id"],
                turn_id=base["turn_id"],
                attempt_id=base["attempt_id"],
            )
        else:
            result, heartbeat_count = await _dispatch_with_heartbeats(
                request,
                writer,
                principal,
                owner_id=owner_id,
                base=base,
                text=text,
            )
        result_ready = True
        for event, data in _sse_events(result, include_ack=False, base_revision=base["revision"]):
            terminal = event
            if event == "route":
                route = data.get("route")
            await writer.event(event, data)
    except asyncio.CancelledError:
        terminal = "cancelled"
        await _mark_attempt_interrupted(request, owner_id, base["attempt_id"])
        raise
    except (ConnectionResetError, OSError):
        terminal = "disconnected"
        if not result_ready:
            await _mark_attempt_interrupted(request, owner_id, base["attempt_id"])
        return response
    except Exception as exc:
        terminal = "error"
        await writer.event(
            "error",
            _sse_error_data(
                exc,
                session_id=base["session_id"],
                turn_id=base["turn_id"],
                attempt_id=base["attempt_id"],
                revision=base["revision"],
            ),
        )
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "chat sse attempt finished request_id=%s session_id=%s turn_id=%s "
            "attempt_id=%s terminal=%s route=%s elapsed_ms=%.1f "
            "heartbeats=%d replay=%s",
            request.get(REQUEST_ID_KEY, ""),
            base["session_id"],
            base["turn_id"],
            base["attempt_id"],
            terminal,
            route,
            elapsed_ms,
            heartbeat_count,
            replay,
        )
    with suppress(ConnectionResetError, OSError, RuntimeError):
        await writer.eof()
    return response


async def _dispatch_with_heartbeats(
    request: web.Request,
    writer: SseWriter,
    principal,
    *,
    owner_id: str,
    base: dict,
    text: str,
) -> tuple[dict, int]:
    svc = services(request)
    heartbeat_seconds = request.app[SETTINGS_KEY].sse_heartbeat_seconds
    task = asyncio.create_task(
        svc.dispatch_existing_attempt(
            principal,
            session_id=base["session_id"],
            turn_id=base["turn_id"],
            attempt_id=base["attempt_id"],
            text=text,
            profile=None,
        ),
        name=f"dext-chat-attempt-{base['attempt_id']}",
    )
    svc.attempts.register(owner_id, base["attempt_id"], task)
    heartbeats = 0
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=heartbeat_seconds)
            if done:
                break
            await writer.heartbeat()
            heartbeats += 1
        return await task, heartbeats
    except asyncio.CancelledError:
        if task.done() and task.cancelled():
            await _mark_attempt_interrupted(request, owner_id, base["attempt_id"])
            result = await svc.completed_attempt_result(
                principal,
                session_id=base["session_id"],
                turn_id=base["turn_id"],
                attempt_id=base["attempt_id"],
            )
            return result, heartbeats
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await _mark_attempt_interrupted(request, owner_id, base["attempt_id"])
        raise
    except (ConnectionResetError, OSError):
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await _mark_attempt_interrupted(request, owner_id, base["attempt_id"])
        raise


async def _mark_attempt_interrupted(
    request: web.Request,
    owner_id: str,
    attempt_id: str,
) -> None:
    if not attempt_id:
        return
    with suppress(Exception):
        await repository(request).cancel_attempt(owner_id, attempt_id)


def _sse_base(data: dict) -> dict:
    return {
        "session_id": str(data.get("session_id") or ""),
        "turn_id": str(data.get("turn_id") or ""),
        "attempt_id": str(data.get("attempt_id") or ""),
        "revision": int(data.get("revision") or 0),
    }


def _sse_events(
    result: dict,
    *,
    include_ack: bool = True,
    base_revision: int | None = None,
) -> list[tuple[str, dict]]:
    turn = result.get("turn") or {}
    message = result.get("assistant_message")
    session = result.get("session") or {}
    session_id = str(session.get("id") or turn.get("session_id") or "")
    turn_id = str(turn.get("id") or "")
    attempt_id = str(result.get("attempt_id") or result.get("active_attempt_id") or "")
    completed_revision = int(session.get("revision") or result.get("revision") or 0)
    revision = int(base_revision if base_revision is not None else completed_revision)
    route = turn.get("route") or "conversation"
    base = {
        "session_id": session_id,
        "turn_id": turn_id,
        "attempt_id": attempt_id,
        "revision": revision,
    }
    completed = {
        **base,
        "revision": completed_revision,
        "session": session,
        "message": message,
        "quick_actions": result.get("quick_actions") or [],
    }
    events: list[tuple[str, dict]] = []
    if include_ack:
        events.append(("ack", base))
    if turn.get("status") == "interrupted":
        events.append(("interrupted", completed))
        return events
    events.append(("route", {**base, "route": route}))
    if message and message.get("content"):
        events.append(("delta", {**base, "text": message["content"]}))
    events.append(("completed", completed))
    return events


def _sse_error_data(
    exc: Exception,
    *,
    session_id: str = "",
    turn_id: str = "",
    attempt_id: str = "",
    revision: int = 0,
) -> dict:
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
        "revision": revision,
        "code": code,
        "message": message,
    }
