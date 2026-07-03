"""ConversationStorePort implementation backed by app-state tables."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dext_recommend.app_state.models import (
    ConversationSession,
    ConversationSummaryModel,
    ConversationTurn,
    utcnow,
)
from dext_recommend.app_state.repositories import new_uuid
from dext_recommend.models import ConversationContext, ConversationSummary
from dext_recommend.ports.conversation_store import TurnSnapshot


_OWNER: ContextVar[str | None] = ContextVar("recommend_conversation_owner", default=None)


def _owner() -> str:
    owner_id = _OWNER.get()
    if owner_id is None:
        raise RuntimeError("conversation store owner context is not bound")
    return owner_id


@contextmanager
def bind_conversation_owner(owner_id: str) -> Iterator[None]:
    token = _OWNER.set(owner_id)
    try:
        yield
    finally:
        _OWNER.reset(token)


class PostgresConversationStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def load_context(self, session_id: str, turn_id: str | None) -> ConversationContext | None:
        owner_id = _owner()
        async with self._sessionmaker() as session:
            srow = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            if srow is None or srow.deleted_at is not None:
                return None
            prior = await self.list_prior_entity_ids(session_id)
            anchor = srow.professor_id
            context_json = {}
            if turn_id is not None:
                trow = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
                context_json = trow.context_json or {} if trow is not None else {}
            return ConversationContext(
                session_id=session_id if turn_id else None,
                turn_id=turn_id,
                main_session_id=srow.source_session_id,
                source_turn_id=srow.source_turn_id,
                anchor_entity_id=context_json.get("anchor_entity_id") or anchor,
                intent=context_json.get("intent"),
                intent_source=context_json.get("intent_source"),
                intent_confidence=context_json.get("intent_confidence"),
                prior_result_entity_ids=tuple(
                    context_json.get("prior_result_entity_ids") or prior
                ),
            )

    async def load_summary(
        self, session_id: str, through_turn_id: str | None,
    ) -> ConversationSummary | None:
        owner_id = _owner()
        async with self._sessionmaker() as session:
            row = (await session.execute(select(ConversationSummaryModel).where(
                ConversationSummaryModel.owner_id == owner_id,
                ConversationSummaryModel.session_id == session_id,
                ConversationSummaryModel.through_turn_id == through_turn_id,
            ))).scalar_one_or_none()
            if row is None:
                return None
            return ConversationSummary(
                session_id=row.session_id,
                through_turn_id=row.through_turn_id,
                text=row.text,
                created_at=row.created_at.isoformat(),
            )

    async def save_turn(
        self,
        session_id: str,
        turn_id: str,
        context: ConversationContext,
        snapshot: TurnSnapshot,
    ) -> None:
        owner_id = _owner()
        async with self._sessionmaker() as session, session.begin():
            trow = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
            if trow is not None and trow.session_id == session_id:
                trow.context_json = _context_to_json(context)
                trow.snapshot_json = asdict(snapshot)
            text = (snapshot.sanitized_summary or "")[:2000]
            if text:
                session.add(ConversationSummaryModel(
                    owner_id=owner_id,
                    id=new_uuid(),
                    session_id=session_id,
                    through_turn_id=turn_id,
                    text=text,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                ))

    async def list_prior_entity_ids(self, session_id: str, limit: int = 50) -> tuple[str, ...]:
        owner_id = _owner()
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(ConversationTurn).where(
                ConversationTurn.owner_id == owner_id,
                ConversationTurn.session_id == session_id,
            ).order_by(ConversationTurn.ordinal))).scalars().all()
            out: list[str] = []
            seen: set[str] = set()
            for row in rows:
                snapshot = row.snapshot_json or {}
                for entity_id in snapshot.get("result_entity_ids") or []:
                    if entity_id not in seen:
                        seen.add(entity_id)
                        out.append(str(entity_id))
                    if len(out) >= limit:
                        return tuple(out)
            return tuple(out)

    async def resolve_fork(
        self, main_session_id: str, source_turn_id: str,
    ) -> ConversationContext | None:
        owner_id = _owner()
        async with self._sessionmaker() as session:
            source = await session.get(ConversationTurn, {"owner_id": owner_id, "id": source_turn_id})
            main = await session.get(ConversationSession, {"owner_id": owner_id, "id": main_session_id})
            if source is None or main is None:
                return None
            snapshot = source.snapshot_json or {}
            return ConversationContext(
                main_session_id=main_session_id,
                source_turn_id=source_turn_id,
                anchor_entity_id=(source.context_json or {}).get("anchor_entity_id") or main.professor_id,
                prior_result_entity_ids=tuple(snapshot.get("result_entity_ids") or ()),
            )


def _context_to_json(context: ConversationContext) -> dict[str, object]:
    return {
        "session_id": context.session_id,
        "turn_id": context.turn_id,
        "main_session_id": context.main_session_id,
        "source_turn_id": context.source_turn_id,
        "anchor_entity_id": context.anchor_entity_id,
        "intent": context.intent,
        "intent_source": context.intent_source,
        "intent_confidence": context.intent_confidence,
        "prior_result_entity_ids": list(context.prior_result_entity_ids),
    }


__all__ = ["PostgresConversationStore", "bind_conversation_owner"]
