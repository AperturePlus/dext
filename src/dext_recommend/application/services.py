"""Application-level orchestration for mentor recommendation HTTP flows."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from dext_recommend.api.adapters import (
    conversation_dispatch_to_answer,
    recommend_request_from_public,
)
from dext_recommend.api.auth import Principal
from dext_recommend.api.schemas import UserProfile
from dext_recommend.app_state.conversation_store import bind_conversation_owner
from dext_recommend.app_state.repositories import (
    AppStateRepository,
    canonical_hash,
)
from dext_recommend.generation.conversation_title import fallback_title


logger = logging.getLogger(__name__)


class AttemptRegistry:
    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}

    def register(self, owner_id: str, attempt_id: str, task: asyncio.Task) -> None:
        key = (owner_id, attempt_id)
        if key in self._tasks:
            raise RuntimeError("attempt task already registered")
        self._tasks[key] = task
        task.add_done_callback(lambda _: self._tasks.pop(key, None))

    def cancel(self, owner_id: str, attempt_id: str) -> bool:
        task = self._tasks.get((owner_id, attempt_id))
        if task is None:
            return False
        task.cancel()
        return True

    async def shutdown(self, timeout: float) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                pass
        self._tasks.clear()


@dataclass(slots=True)
class ApplicationServices:
    repository: AppStateRepository
    runtime: Any
    attempts: AttemptRegistry

    async def create_turn_and_dispatch(
        self,
        principal: Principal,
        *,
        session_id: str,
        text: str,
        request_id: str,
        expected_revision: int,
        idempotency_key: str,
        profile: UserProfile | None = None,
    ) -> dict[str, Any]:
        admitted = await self.admit_turn(
            principal,
            session_id=session_id,
            text=text,
            request_id=request_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        return await self.dispatch_existing_attempt(
            principal,
            session_id=admitted["session_id"],
            turn_id=admitted["turn_id"],
            attempt_id=admitted["attempt_id"],
            text=text,
            profile=profile,
        )

    async def admit_turn(
        self,
        principal: Principal,
        *,
        session_id: str,
        text: str,
        request_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        owner_id = str(principal.owner_id)
        request_hash = canonical_hash({
            "owner_id": owner_id,
            "session_id": session_id,
            "text": text,
            "request_id": request_id,
            "expected_revision": expected_revision,
        })
        admitted = await self.repository.admit_turn(
            owner_id,
            session_id,
            text=text,
            request_id=request_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        return {
            "session_id": str(admitted.get("session_id") or session_id),
            "turn_id": str(admitted.get("turn_id") or admitted.get("resource_id") or ""),
            "attempt_id": str(admitted.get("attempt_id") or admitted.get("active_attempt_id") or ""),
            "revision": expected_revision,
            "replay": admitted.get("_idempotency_replay") == "completed",
        }

    async def retry_turn_and_dispatch(
        self,
        principal: Principal,
        *,
        turn_id: str,
        session_id: str,
        request_id: str,
        expected_revision: int,
        idempotency_key: str,
        profile: UserProfile | None = None,
    ) -> dict[str, Any]:
        admitted = await self.admit_retry_attempt(
            principal,
            turn_id=turn_id,
            session_id=session_id,
            request_id=request_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        return await self.dispatch_existing_attempt(
            principal,
            session_id=admitted["session_id"],
            turn_id=admitted["turn_id"],
            attempt_id=admitted["attempt_id"],
            text=admitted["text"],
            profile=profile,
        )

    async def admit_retry_attempt(
        self,
        principal: Principal,
        *,
        turn_id: str,
        session_id: str,
        request_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        owner_id = str(principal.owner_id)
        request_hash = canonical_hash({
            "owner_id": owner_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "request_id": request_id,
            "expected_revision": expected_revision,
        })
        admitted = await self.repository.create_retry_attempt(
            owner_id,
            turn_id,
            session_id=session_id,
            request_id=request_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        _, text = await self.repository.turn_user_text(owner_id, turn_id)
        return {
            "session_id": str(admitted.get("session_id") or session_id),
            "turn_id": str(admitted.get("turn_id") or turn_id),
            "attempt_id": str(admitted.get("attempt_id") or admitted.get("resource_id") or ""),
            "revision": expected_revision,
            "text": text,
            "replay": admitted.get("_idempotency_replay") == "completed",
        }

    async def completed_attempt_result(
        self,
        principal: Principal,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        owner_id = str(principal.owner_id)
        completed = await self.repository.get_attempt_result(
            owner_id,
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
        )
        session_obj = await self.repository.get_session(owner_id, session_id)
        return {
            **completed,
            "session": session_obj,
            "attempt_id": attempt_id,
            "revision": session_obj.get("revision", 0),
            "quick_actions": [],
        }

    async def dispatch_existing_attempt(
        self,
        principal: Principal,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str,
        text: str,
        profile: UserProfile | None,
    ) -> dict[str, Any]:
        owner_id = str(principal.owner_id)
        request = recommend_request_from_public(
            prompt=text,
            profile=profile,
            session_id=session_id,
            turn_id=turn_id,
            limit=10,
        )
        with bind_conversation_owner(owner_id):
            result = await self.runtime.conversation.dispatch(
                request,
                viewer_permissions=principal.viewer_permissions(),
            )
        answer, related, snapshot = conversation_dispatch_to_answer(result)
        route = _conversation_route(result.kind)
        status = "failed" if _has_terminal_error(result) else "completed"
        diagnostics = _completion_diagnostics(result, related)
        completed = await self.repository.complete_attempt(
            owner_id,
            session_id=session_id,
            turn_id=turn_id,
            attempt_id=attempt_id,
            status=status,
            route=route,
            assistant_content=answer,
            related_recommendations=related,
            context_json={
                "session_id": session_id,
                "turn_id": turn_id,
                "intent": getattr(result.context, "intent", None) if result.context else None,
                "intent_source": getattr(result.context, "intent_source", None) if result.context else None,
                "intent_confidence": getattr(result.context, "intent_confidence", None) if result.context else None,
                "anchor_entity_id": getattr(result.context, "anchor_entity_id", None) if result.context else None,
                "prior_result_entity_ids": list(
                    getattr(result.context, "prior_result_entity_ids", ()) if result.context else ()
                ),
            },
            snapshot_json=snapshot,
        )
        logger.info(
            "chat attempt completed session_id=%s turn_id=%s attempt_id=%s "
            "route=%s status=%s related_count=%d warning_codes=%s "
            "recall_count=%s post_filter_count=%s returned_count=%s build_id=%s",
            session_id,
            turn_id,
            attempt_id,
            route,
            status,
            diagnostics["related_count"],
            ",".join(diagnostics["warning_codes"]),
            diagnostics["recall_count"],
            diagnostics["post_filter_count"],
            diagnostics["returned_count"],
            diagnostics["build_id"],
        )
        session_obj = await self.repository.get_session(owner_id, session_id)
        session_obj = await self._ensure_initial_session_title(
            owner_id,
            session_id=session_id,
            turn_id=turn_id,
            status=status,
            completed=completed,
            session_obj=session_obj,
            first_user_message=text,
            assistant_answer=answer,
        )
        return {
            **completed,
            "session": session_obj,
            "attempt_id": attempt_id,
            "revision": session_obj.get("revision", 0),
            "quick_actions": [],
        }

    async def _ensure_initial_session_title(
        self,
        owner_id: str,
        *,
        session_id: str,
        turn_id: str,
        status: str,
        completed: dict[str, Any],
        session_obj: dict[str, Any],
        first_user_message: str,
        assistant_answer: str,
    ) -> dict[str, Any]:
        turn = completed.get("turn") or {}
        if (
            status != "completed"
            or turn.get("ordinal") != 0
            or str(session_obj.get("title") or "").strip()
        ):
            return session_obj
        generator = getattr(self.runtime, "conversation_titles", None)
        title = fallback_title(first_user_message)
        try:
            if generator is None:
                logger.warning(
                    "chat title generator unavailable session_id=%s turn_id=%s",
                    session_id,
                    turn_id,
                )
            else:
                title = await generator.generate(first_user_message, assistant_answer)
        except Exception:
            logger.warning(
                "chat title generation failed session_id=%s turn_id=%s",
                session_id,
                turn_id,
                exc_info=True,
            )
            title = fallback_title(first_user_message)
        try:
            return await self.repository.set_initial_session_title(
                owner_id,
                session_id,
                turn_id,
                title,
            )
        except Exception:
            logger.warning(
                "chat title persistence failed session_id=%s turn_id=%s",
                session_id,
                turn_id,
                exc_info=True,
            )
            return session_obj


__all__ = ["ApplicationServices", "AttemptRegistry"]


def _conversation_route(kind: str) -> str:
    if kind == "recommendation":
        return "recommendation"
    if kind == "fork_reroute":
        return "forkReroute"
    return "conversation"


def _has_terminal_error(result) -> bool:
    if result.kind == "error":
        return True
    if result.kind == "recommendation" and result.recommendation is not None:
        return any(w.severity == "error" for w in result.recommendation.warnings)
    if result.kind == "detail_followup" and result.detail_followup is not None:
        return any(w.severity == "error" for w in result.detail_followup.warnings)
    return False


def _completion_diagnostics(result, related: list[dict[str, Any]]) -> dict[str, Any]:
    if result.kind == "recommendation" and result.recommendation is not None:
        response = result.recommendation
        return {
            "related_count": len(related),
            "warning_codes": [str(w.code) for w in response.warnings],
            "recall_count": response.query.recall_count,
            "post_filter_count": response.query.post_filter_count,
            "returned_count": response.query.returned_count,
            "build_id": response.build_id,
        }
    warnings = tuple(getattr(result, "issues", ()) or ())
    return {
        "related_count": len(related),
        "warning_codes": [str(w.code) for w in warnings],
        "recall_count": None,
        "post_filter_count": None,
        "returned_count": len(related),
        "build_id": None,
    }
