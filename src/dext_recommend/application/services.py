"""Application-level orchestration for mentor recommendation HTTP flows."""
from __future__ import annotations

import asyncio
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
        status = "completed" if result.kind not in {"error"} else "failed"
        route = _conversation_route(result.kind)
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
        session_obj = await self.repository.get_session(owner_id, session_id)
        return {
            **completed,
            "session": session_obj,
            "attempt_id": attempt_id,
            "revision": session_obj.get("revision", 0),
            "quick_actions": [],
        }


__all__ = ["ApplicationServices", "AttemptRegistry"]


def _conversation_route(kind: str) -> str:
    if kind == "recommendation":
        return "recommendation"
    if kind == "fork_reroute":
        return "forkReroute"
    return "conversation"
