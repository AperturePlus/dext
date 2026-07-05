"""Owner-scoped repositories for recommendation application state."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dext_recommend.app_state.models import (
    AppFavorite,
    AppHistory,
    AppIdentity,
    AppProfile,
    ConversationAttempt,
    ConversationMessage,
    ConversationSession,
    ConversationSummaryModel,
    ConversationTurn,
    IdempotencyRecord,
    utcnow,
)


class AppStateError(RuntimeError):
    status = 400
    error_code = "app_state_error"


class NotFoundError(AppStateError):
    status = 404
    error_code = "not_found"


class ConflictError(AppStateError):
    status = 409
    error_code = "conflict"


class IdempotencyConflictError(ConflictError):
    error_code = "idempotency_conflict"


class RequestInProgressError(ConflictError):
    error_code = "request_in_progress"


TERMINAL_ATTEMPT_STATUSES = frozenset({"completed", "failed", "interrupted"})
RETRYABLE_TURN_STATUSES = frozenset({"failed", "interrupted"})
REGENERABLE_COMPLETED_ROUTES = frozenset({"conversation", "recommendation"})


def new_uuid() -> str:
    return str(uuid.uuid4())


def canonical_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _session_dict(row: ConversationSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "root_session_id": row.root_session_id,
        "source_session_id": row.source_session_id,
        "source_turn_id": row.source_turn_id,
        "professor_id": row.professor_id,
        "revision": row.revision,
        "title": row.title,
        "created_at": dt_to_iso(row.created_at),
        "updated_at": dt_to_iso(row.updated_at),
        "deleted_at": dt_to_iso(row.deleted_at),
        "legacy_context_incomplete": False,
    }


def _turn_dict(row: ConversationTurn) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "ordinal": row.ordinal,
        "status": row.status,
        "route": row.route,
        "user_message_id": row.user_message_id,
        "active_attempt_id": row.active_attempt_id,
        "created_at": dt_to_iso(row.created_at),
        "updated_at": dt_to_iso(row.updated_at),
    }


def _message_dict(row: ConversationMessage) -> dict[str, Any]:
    kind = row.kind if row.kind in {"conversation", "recommendation", "forkReroute"} else "conversation"
    return {
        "id": row.id,
        "turn_id": row.turn_id,
        "role": row.role,
        "content": row.content,
        "created_at": dt_to_iso(row.created_at),
        "status": row.status,
        "kind": kind,
        "feedback": row.feedback,
        "related_recommendations": row.related_recommendations_json or [],
    }


def _favorite_item(row: AppFavorite) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    return {
        "professor_id": row.professor_id,
        "name": str(snapshot.get("name") or snapshot.get("display_name") or row.professor_id),
        "university": str(snapshot.get("university") or ""),
        "college": str(snapshot.get("college") or ""),
        "title": str(snapshot.get("title") or ""),
        "research_fields": list(snapshot.get("research_fields") or []),
        "homepage_url": snapshot.get("homepage_url"),
        "favorited_at": snapshot.get("favorited_at") or dt_to_iso(row.favorited_at),
    }


def _split_turns_messages(turns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flat_turns: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for turn in turns:
        item = dict(turn)
        messages.extend(item.pop("messages", []))
        flat_turns.append(item)
    return flat_turns, messages


_RECOMMENDATION_CONTEXT_FIELDS = (
    "professor_id",
    "name",
    "university",
    "college",
    "title",
    "research_fields",
    "reason",
    "limitations",
    "homepage_url",
)


def _selected_recommendation_snapshot(
    recommendations: list[dict[str, Any]],
    professor_id: str,
) -> dict[str, Any] | None:
    for item in recommendations:
        if str(item.get("professor_id") or "") != professor_id:
            continue
        return {field: item.get(field) for field in _RECOMMENDATION_CONTEXT_FIELDS}
    return None


def _selected_recommendation_from_messages(
    messages: list[ConversationMessage],
    professor_id: str,
) -> dict[str, Any] | None:
    for message in messages:
        if (
            message.role != "assistant"
            or message.kind != "recommendation"
            or message.status != "done"
        ):
            continue
        snapshot = _selected_recommendation_snapshot(
            list(message.related_recommendations_json or []),
            professor_id,
        )
        if snapshot is not None:
            return snapshot
    return None


def _message_order(row: ConversationMessage) -> tuple[datetime, str]:
    return row.created_at, row.id


def _visible_turn_messages(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    visible = [message for message in messages if message.role != "assistant"]
    assistants = [message for message in messages if message.role == "assistant"]
    latest_done = next((m for m in reversed(assistants) if m.status == "done"), None)
    latest_error = next((m for m in reversed(assistants) if m.status == "error"), None)
    selected: list[ConversationMessage] = []
    if latest_done is not None:
        selected.append(latest_done)
    if latest_error is not None and (
        latest_done is None or _message_order(latest_error) > _message_order(latest_done)
    ):
        selected.append(latest_error)
    elif latest_done is None and latest_error is None and assistants:
        selected.append(assistants[-1])
    visible.extend(selected)
    return sorted(visible, key=_message_order)


def _model_context_message(row: ConversationMessage) -> dict[str, Any]:
    message = _message_dict(row)
    return {
        "id": message["id"],
        "role": message["role"],
        "kind": message["kind"],
        "content": message["content"],
        "related_recommendations": message["related_recommendations"],
    }


@dataclass(slots=True)
class AppStateRepository:
    sessionmaker: async_sessionmaker[AsyncSession]

    async def create_identity(
        self, *, owner_id: str, kind: str, token_digest: str, expires_at: datetime,
    ) -> AppIdentity:
        async with self.sessionmaker() as session, session.begin():
            row = AppIdentity(
                owner_id=owner_id,
                kind=kind,
                token_digest=token_digest,
                expires_at=expires_at,
            )
            session.add(row)
            await session.flush()
            return row

    async def get_identity_by_digest(self, token_digest: str) -> AppIdentity | None:
        async with self.sessionmaker() as session:
            result = await session.execute(
                select(AppIdentity).where(AppIdentity.token_digest == token_digest)
            )
            return result.scalar_one_or_none()

    async def revoke_identity(self, owner_id: str) -> None:
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(AppIdentity, owner_id)
            if row is not None and row.revoked_at is None:
                row.revoked_at = utcnow()

    async def get_profile(self, owner_id: str) -> dict[str, Any] | None:
        async with self.sessionmaker() as session:
            row = await session.get(AppProfile, owner_id)
            return None if row is None else dict(row.profile_json)

    async def put_profile(self, owner_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(AppProfile, owner_id)
            if row is None:
                row = AppProfile(owner_id=owner_id, profile_json=profile, revision=0)
                session.add(row)
            else:
                row.profile_json = profile
                row.revision += 1
            await session.flush()
            return dict(row.profile_json)

    async def delete_profile(self, owner_id: str) -> None:
        async with self.sessionmaker() as session, session.begin():
            await session.execute(delete(AppProfile).where(AppProfile.owner_id == owner_id))

    async def list_favorites(self, owner_id: str) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            rows = (await session.execute(
                select(AppFavorite)
                .where(AppFavorite.owner_id == owner_id)
                .order_by(AppFavorite.favorited_at.desc(), AppFavorite.professor_id)
            )).scalars().all()
            return [
                _favorite_item(row)
                for row in rows
            ]

    async def put_favorite(
        self, owner_id: str, professor_id: str, snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(AppFavorite, {"owner_id": owner_id, "professor_id": professor_id})
            now = utcnow()
            if row is None:
                row = AppFavorite(
                    owner_id=owner_id,
                    professor_id=professor_id,
                    snapshot_json=snapshot or {},
                    favorited_at=now,
                )
                session.add(row)
            else:
                row.snapshot_json = snapshot or row.snapshot_json or {}
                row.favorited_at = row.favorited_at or now
            await session.flush()
            return {"favorited": True, "item": _favorite_item(row)}

    async def delete_favorite(self, owner_id: str, professor_id: str) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            await session.execute(delete(AppFavorite).where(
                AppFavorite.owner_id == owner_id,
                AppFavorite.professor_id == professor_id,
            ))
            return {"favorited": False}

    async def list_history(self, owner_id: str) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            rows = (await session.execute(
                select(AppHistory)
                .where(AppHistory.owner_id == owner_id)
                .order_by(AppHistory.created_at.desc(), AppHistory.session_id)
            )).scalars().all()
            return [dict(row.snapshot_json or {}) for row in rows]

    async def put_history(
        self, owner_id: str, item: dict[str, Any],
    ) -> dict[str, Any]:
        session_id = str(item["session_id"])
        item_type = str(item.get("type") or "mentor")
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(AppHistory, {
                "owner_id": owner_id,
                "session_id": session_id,
                "type": item_type,
            })
            if row is None:
                row = AppHistory(
                    owner_id=owner_id,
                    session_id=session_id,
                    type=item_type,
                    snapshot_json=item,
                    version_pins_json=None,
                )
                session.add(row)
            else:
                row.snapshot_json = item
            await session.flush()
            return dict(row.snapshot_json)

    async def delete_history(self, owner_id: str, session_id: str | None = None) -> None:
        async with self.sessionmaker() as session, session.begin():
            stmt = delete(AppHistory).where(AppHistory.owner_id == owner_id)
            if session_id is not None:
                stmt = stmt.where(AppHistory.session_id == session_id)
            await session.execute(stmt)

    async def create_session(
        self,
        owner_id: str,
        *,
        kind: str = "general",
        professor_id: str | None = None,
        source_session_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> dict[str, Any]:
        session_id = new_uuid()
        root_id = session_id
        if kind == "fork":
            if not source_session_id or not source_turn_id:
                raise ConflictError("fork requires source session and turn")
            source = await self.get_session(owner_id, source_session_id, include_deleted=False)
            root_id = source["root_session_id"]
        async with self.sessionmaker() as session, session.begin():
            row = ConversationSession(
                owner_id=owner_id,
                id=session_id,
                kind=kind,
                root_session_id=root_id,
                source_session_id=source_session_id,
                source_turn_id=source_turn_id,
                professor_id=professor_id,
                revision=0,
            )
            session.add(row)
            await session.flush()
            return _session_dict(row)

    async def list_sessions(self, owner_id: str) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            rows = (await session.execute(
                select(ConversationSession)
                .where(
                    ConversationSession.owner_id == owner_id,
                    ConversationSession.deleted_at.is_(None),
                    ConversationSession.kind != "fork",
                )
                .order_by(ConversationSession.updated_at.desc(), ConversationSession.id)
            )).scalars().all()
            return [_session_dict(row) for row in rows]

    async def get_session(
        self, owner_id: str, session_id: str, *, include_deleted: bool = False,
    ) -> dict[str, Any]:
        async with self.sessionmaker() as session:
            row = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            if row is None or (row.deleted_at is not None and not include_deleted):
                raise NotFoundError("session not found")
            return _session_dict(row)

    async def set_initial_session_title(
        self,
        owner_id: str,
        session_id: str,
        turn_id: str,
        title: str,
    ) -> dict[str, Any]:
        normalized = " ".join(str(title or "").strip().split())[:256]
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
            if (
                row is None
                or turn is None
                or row.deleted_at is not None
                or turn.session_id != session_id
            ):
                raise NotFoundError("session not found")
            if turn.ordinal != 0 or str(row.title or "").strip() or not normalized:
                return _session_dict(row)
            row.title = normalized
            await session.flush()
            return _session_dict(row)

    async def soft_delete_session_tree(self, owner_id: str, session_id: str) -> None:
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            if row is None or row.deleted_at is not None:
                raise NotFoundError("session not found")
            now = utcnow()
            root = row.root_session_id
            rows = (await session.execute(select(ConversationSession).where(
                ConversationSession.owner_id == owner_id,
                ConversationSession.root_session_id == root,
            ))).scalars().all()
            for item in rows:
                item.deleted_at = item.deleted_at or now
                item.revision += 1

    async def soft_delete_all_session_trees(self, owner_id: str) -> int:
        async with self.sessionmaker() as session, session.begin():
            active_rows = (await session.execute(select(ConversationSession).where(
                ConversationSession.owner_id == owner_id,
                ConversationSession.deleted_at.is_(None),
            ))).scalars().all()
            if not active_rows:
                return 0

            root_ids = {row.root_session_id for row in active_rows}
            rows = (await session.execute(select(ConversationSession).where(
                ConversationSession.owner_id == owner_id,
                ConversationSession.root_session_id.in_(root_ids),
            ))).scalars().all()
            now = utcnow()
            deleted_count = 0
            session_ids: list[str] = []
            for item in rows:
                session_ids.append(item.id)
                if item.deleted_at is None:
                    item.deleted_at = now
                    item.revision += 1
                    deleted_count += 1

            turn_ids = (await session.execute(
                select(ConversationTurn.id).where(
                    ConversationTurn.owner_id == owner_id,
                    ConversationTurn.session_id.in_(session_ids),
                )
            )).scalars().all()
            idempotency_scopes = [f"turn:{session_id}" for session_id in session_ids]
            idempotency_scopes.extend(f"attempt:{turn_id}" for turn_id in turn_ids)

            if idempotency_scopes:
                await session.execute(delete(IdempotencyRecord).where(
                    IdempotencyRecord.owner_id == owner_id,
                    IdempotencyRecord.scope.in_(idempotency_scopes),
                ))
            await session.execute(delete(ConversationSummaryModel).where(
                ConversationSummaryModel.owner_id == owner_id,
                ConversationSummaryModel.session_id.in_(session_ids),
            ))
            await session.execute(delete(ConversationMessage).where(
                ConversationMessage.owner_id == owner_id,
                ConversationMessage.session_id.in_(session_ids),
            ))
            if turn_ids:
                await session.execute(delete(ConversationAttempt).where(
                    ConversationAttempt.owner_id == owner_id,
                    ConversationAttempt.turn_id.in_(turn_ids),
                ))
            await session.execute(delete(ConversationTurn).where(
                ConversationTurn.owner_id == owner_id,
                ConversationTurn.session_id.in_(session_ids),
            ))
            await session.flush()
            return deleted_count

    async def list_forks(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            rows = (await session.execute(select(ConversationSession).where(
                ConversationSession.owner_id == owner_id,
                ConversationSession.source_session_id == session_id,
                ConversationSession.kind == "fork",
                ConversationSession.deleted_at.is_(None),
            ).order_by(ConversationSession.created_at.desc()))).scalars().all()
            return [_session_dict(row) for row in rows]

    async def create_fork(
        self, owner_id: str, session_id: str, source_turn_id: str, professor_id: str | None,
    ) -> dict[str, Any]:
        if not professor_id:
            raise ConflictError("fork requires professor_id")
        async with self.sessionmaker() as session, session.begin():
            source_session = await session.get(
                ConversationSession,
                {"owner_id": owner_id, "id": session_id},
            )
            if source_session is None or source_session.deleted_at is not None:
                raise NotFoundError("session not found")
            source_turn = await session.get(
                ConversationTurn,
                {"owner_id": owner_id, "id": source_turn_id},
            )
            if source_turn is None:
                raise NotFoundError("source turn not found")
            if source_turn.session_id != session_id:
                raise ConflictError("source turn does not belong to session")
            if source_turn.status != "completed" or source_turn.route != "recommendation":
                raise ConflictError("source turn is not a completed recommendation")
            source_assistants = (await session.execute(
                select(ConversationMessage)
                .where(
                    ConversationMessage.owner_id == owner_id,
                    ConversationMessage.turn_id == source_turn_id,
                    ConversationMessage.role == "assistant",
                    ConversationMessage.kind == "recommendation",
                )
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            )).scalars().all()
            if _selected_recommendation_from_messages(source_assistants, professor_id) is None:
                raise ConflictError("source recommendation does not contain professor")
            existing = (await session.execute(select(ConversationSession).where(
                ConversationSession.owner_id == owner_id,
                ConversationSession.source_session_id == session_id,
                ConversationSession.source_turn_id == source_turn_id,
                ConversationSession.professor_id == professor_id,
                ConversationSession.kind == "fork",
                ConversationSession.deleted_at.is_(None),
            ))).scalar_one_or_none()
            if existing is not None:
                return _session_dict(existing)
            row = ConversationSession(
                owner_id=owner_id,
                id=new_uuid(),
                kind="fork",
                root_session_id=source_session.root_session_id,
                source_session_id=session_id,
                source_turn_id=source_turn_id,
                professor_id=professor_id,
                revision=0,
            )
            session.add(row)
            await session.flush()
            return _session_dict(row)

    async def get_session_projection(self, owner_id: str, session_id: str) -> dict[str, Any]:
        session_obj = await self.get_session(owner_id, session_id)
        turns = await self.list_turns(owner_id, session_id)
        flat_turns, messages = _split_turns_messages(turns)
        return {"session": session_obj, "turns": flat_turns, "messages": messages}

    async def get_fork_model_context(
        self,
        owner_id: str,
        session_id: str,
        current_turn_id: str,
        current_question: str,
    ) -> dict[str, Any] | None:
        async with self.sessionmaker() as session:
            fork = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            if fork is None or fork.deleted_at is not None:
                raise NotFoundError("session not found")
            if fork.kind != "fork":
                return None
            if not fork.source_session_id or not fork.source_turn_id or not fork.professor_id:
                raise ConflictError("fork session is missing source context")
            current_turn = await session.get(
                ConversationTurn,
                {"owner_id": owner_id, "id": current_turn_id},
            )
            if current_turn is None or current_turn.session_id != session_id:
                raise NotFoundError("turn not found")
            source_turn = await session.get(
                ConversationTurn,
                {"owner_id": owner_id, "id": fork.source_turn_id},
            )
            if source_turn is None or source_turn.session_id != fork.source_session_id:
                raise ConflictError("fork source turn is unavailable")
            source_assistants = (await session.execute(
                select(ConversationMessage)
                .where(
                    ConversationMessage.owner_id == owner_id,
                    ConversationMessage.turn_id == fork.source_turn_id,
                    ConversationMessage.role == "assistant",
                    ConversationMessage.kind == "recommendation",
                )
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            )).scalars().all()
            selected = _selected_recommendation_from_messages(source_assistants, fork.professor_id)
            if selected is None:
                raise ConflictError("fork source recommendation is unavailable")
            source_turns = (await session.execute(
                select(ConversationTurn)
                .where(
                    ConversationTurn.owner_id == owner_id,
                    ConversationTurn.session_id == fork.source_session_id,
                    ConversationTurn.ordinal <= source_turn.ordinal,
                )
                .order_by(ConversationTurn.ordinal)
            )).scalars().all()
            fork_turns = (await session.execute(
                select(ConversationTurn)
                .where(
                    ConversationTurn.owner_id == owner_id,
                    ConversationTurn.session_id == session_id,
                    ConversationTurn.ordinal < current_turn.ordinal,
                )
                .order_by(ConversationTurn.ordinal)
            )).scalars().all()
            return {
                "session": {
                    "id": fork.id,
                    "kind": fork.kind,
                    "root_session_id": fork.root_session_id,
                    "source_session_id": fork.source_session_id,
                    "source_turn_id": fork.source_turn_id,
                    "professor_id": fork.professor_id,
                },
                "source_prefix": await self._turns_for_model_context(
                    session, owner_id, source_turns,
                ),
                "selected_recommendation": selected,
                "fork_history": await self._turns_for_model_context(
                    session, owner_id, fork_turns,
                ),
                "current_question": current_question,
            }

    async def list_turns(self, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        async with self.sessionmaker() as session:
            # Check ownership first to make cross-owner access indistinguishable.
            srow = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            if srow is None or srow.deleted_at is not None:
                raise NotFoundError("session not found")
            turns = (await session.execute(select(ConversationTurn).where(
                ConversationTurn.owner_id == owner_id,
                ConversationTurn.session_id == session_id,
            ).order_by(ConversationTurn.ordinal))).scalars().all()
            out: list[dict[str, Any]] = []
            for turn in turns:
                messages = (await session.execute(select(ConversationMessage).where(
                    ConversationMessage.owner_id == owner_id,
                    ConversationMessage.turn_id == turn.id,
                ).order_by(ConversationMessage.created_at, ConversationMessage.id))).scalars().all()
                td = _turn_dict(turn)
                td["messages"] = [
                    _message_dict(msg) for msg in _visible_turn_messages(list(messages))
                ]
                out.append(td)
            return out

    async def admit_turn(
        self,
        owner_id: str,
        session_id: str,
        *,
        text: str,
        request_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, str]:
        scope = f"turn:{session_id}"
        async with self.sessionmaker() as session, session.begin():
            replay = await self._check_idempotency(
                session, owner_id, scope, idempotency_key, request_hash,
            )
            if replay is not None:
                return replay
            srow = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            if srow is None or srow.deleted_at is not None:
                raise NotFoundError("session not found")
            if srow.revision != expected_revision:
                raise ConflictError("session revision conflict")
            ordinal = await self._next_turn_ordinal(session, owner_id, session_id)
            turn_id = new_uuid()
            user_message_id = new_uuid()
            attempt_id = new_uuid()
            turn = ConversationTurn(
                owner_id=owner_id,
                id=turn_id,
                session_id=session_id,
                ordinal=ordinal,
                status="queued",
                route=None,
                user_message_id=user_message_id,
                active_attempt_id=attempt_id,
                context_json={},
            )
            session.add(turn)
            srow.revision += 1
            await session.flush()
            attempt = ConversationAttempt(
                owner_id=owner_id,
                id=attempt_id,
                turn_id=turn_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="queued",
            )
            message = ConversationMessage(
                owner_id=owner_id,
                id=user_message_id,
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                role="user",
                status="done",
                kind="text",
                content=text,
                related_recommendations_json=[],
            )
            idem = IdempotencyRecord(
                owner_id=owner_id,
                id=new_uuid(),
                scope=scope,
                key=idempotency_key,
                request_hash=request_hash,
                state="running",
                resource_id=turn_id,
                expires_at=utcnow() + timedelta(hours=24),
            )
            session.add_all([attempt, message, idem])
            await session.flush()
            return {
                "turn_id": turn_id,
                "attempt_id": attempt_id,
                "user_message_id": user_message_id,
                "session_id": session_id,
            }

    async def complete_attempt(
        self,
        owner_id: str,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str,
        status: str,
        route: str | None,
        assistant_content: str,
        related_recommendations: list[dict[str, Any]],
        context_json: dict[str, Any],
        snapshot_json: dict[str, Any],
    ) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
            attempt = await session.get(ConversationAttempt, {"owner_id": owner_id, "id": attempt_id})
            if turn is None or attempt is None or turn.session_id != session_id:
                raise NotFoundError("attempt not found")
            if attempt.status in TERMINAL_ATTEMPT_STATUSES:
                assistant = await self._assistant_for_attempt(
                    session, owner_id, turn_id, attempt_id,
                )
                await self._finish_idempotency(
                    session,
                    owner_id,
                    session_id,
                    turn_id,
                    attempt,
                    assistant.id if assistant is not None else None,
                )
                return {
                    "turn": _turn_dict(turn),
                    "assistant_message": (
                        None if assistant is None else _message_dict(assistant)
                    ),
                }
            prior_done_assistant = await self._latest_done_assistant_for_turn(
                session, owner_id, turn_id,
            )
            preserve_completed_result = (
                prior_done_assistant is not None
                and turn.route in REGENERABLE_COMPLETED_ROUTES
            )
            if attempt.cancel_requested_at is not None:
                attempt.status = "interrupted"
                turn.status = "completed" if preserve_completed_result else "interrupted"
                turn.active_attempt_id = None
                attempt.finished_at = attempt.finished_at or utcnow()
                await self._finish_idempotency(
                    session, owner_id, session_id, turn_id, attempt, None,
                )
                return {"turn": _turn_dict(turn), "assistant_message": None}
            if status == "completed" or not preserve_completed_result:
                turn.status = status
                turn.route = route
                turn.context_json = context_json
                turn.snapshot_json = snapshot_json
            else:
                turn.status = "completed"
            turn.active_attempt_id = None
            attempt.status = "completed" if status == "completed" else status
            attempt.finished_at = utcnow()
            assistant_kind = (
                turn.route
                if status != "completed" and preserve_completed_result
                else route
            ) or "text"
            assistant = ConversationMessage(
                owner_id=owner_id,
                id=new_uuid(),
                session_id=session_id,
                turn_id=turn_id,
                attempt_id=attempt_id,
                role="assistant",
                status="done" if status == "completed" else "error",
                kind=assistant_kind,
                content=assistant_content,
                related_recommendations_json=related_recommendations,
                feedback="none",
            )
            session.add(assistant)
            await self._finish_idempotency(
                session, owner_id, session_id, turn_id, attempt, assistant.id,
            )
            await session.flush()
            return {
                "turn": _turn_dict(turn),
                "assistant_message": _message_dict(assistant),
            }

    async def get_attempt_result(
        self,
        owner_id: str,
        *,
        session_id: str,
        turn_id: str,
        attempt_id: str,
    ) -> dict[str, Any]:
        async with self.sessionmaker() as session:
            srow = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
            attempt = await session.get(ConversationAttempt, {"owner_id": owner_id, "id": attempt_id})
            if (
                srow is None
                or turn is None
                or attempt is None
                or turn.session_id != session_id
                or attempt.turn_id != turn_id
            ):
                raise NotFoundError("attempt not found")
            assistant = await self._assistant_for_attempt(
                session, owner_id, turn_id, attempt_id,
            )
            return {
                "turn": _turn_dict(turn),
                "assistant_message": (
                    None if assistant is None else _message_dict(assistant)
                ),
            }

    async def create_retry_attempt(
        self,
        owner_id: str,
        turn_id: str,
        *,
        session_id: str,
        request_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, str]:
        scope = f"attempt:{turn_id}"
        async with self.sessionmaker() as session, session.begin():
            replay = await self._check_idempotency(
                session, owner_id, scope, idempotency_key, request_hash,
            )
            if replay is not None:
                return replay
            srow = await session.get(ConversationSession, {"owner_id": owner_id, "id": session_id})
            turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
            if srow is None or turn is None or turn.session_id != session_id:
                raise NotFoundError("turn not found")
            if srow.revision != expected_revision:
                raise ConflictError("session revision conflict")
            if turn.active_attempt_id is not None:
                raise RequestInProgressError("request already in progress")
            if turn.status == "completed":
                latest_ordinal = await session.scalar(select(func.max(ConversationTurn.ordinal)).where(
                    ConversationTurn.owner_id == owner_id,
                    ConversationTurn.session_id == session_id,
                ))
                has_completed_answer = await self._latest_done_assistant_for_turn(
                    session, owner_id, turn_id,
                ) is not None
                if (
                    turn.route not in REGENERABLE_COMPLETED_ROUTES
                    or turn.ordinal != latest_ordinal
                    or not has_completed_answer
                ):
                    raise ConflictError("completed turn cannot be retried")
            elif turn.status not in RETRYABLE_TURN_STATUSES:
                raise ConflictError("turn cannot be retried")
            attempt_id = new_uuid()
            attempt = ConversationAttempt(
                owner_id=owner_id,
                id=attempt_id,
                turn_id=turn_id,
                request_id=request_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="queued",
            )
            turn.active_attempt_id = attempt_id
            turn.status = "queued"
            session.add_all([
                attempt,
                IdempotencyRecord(
                    owner_id=owner_id,
                    id=new_uuid(),
                    scope=scope,
                    key=idempotency_key,
                    request_hash=request_hash,
                    state="running",
                    resource_id=attempt_id,
                    expires_at=utcnow() + timedelta(hours=24),
                ),
            ])
            await session.flush()
            return {"turn_id": turn_id, "attempt_id": attempt_id, "session_id": session_id}

    async def cancel_attempt(self, owner_id: str, attempt_id: str) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            attempt = await session.get(ConversationAttempt, {"owner_id": owner_id, "id": attempt_id})
            if attempt is None:
                raise NotFoundError("attempt not found")
            turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": attempt.turn_id})
            now = utcnow()
            attempt.cancel_requested_at = attempt.cancel_requested_at or now
            if attempt.status not in {"completed", "failed", "interrupted"}:
                attempt.status = "interrupted"
            attempt.finished_at = attempt.finished_at or now
            if turn is not None and turn.status not in {"completed", "failed", "interrupted"}:
                prior_done_assistant = await self._latest_done_assistant_for_turn(
                    session, owner_id, turn.id,
                )
                preserve_completed_result = (
                    prior_done_assistant is not None
                    and turn.route in REGENERABLE_COMPLETED_ROUTES
                )
                turn.status = "completed" if preserve_completed_result else "interrupted"
                turn.active_attempt_id = None
            if turn is not None:
                await self._finish_idempotency(
                    session, owner_id, turn.session_id, turn.id, attempt, None,
                )
            return {"attempt_id": attempt.id, "status": attempt.status}

    async def patch_feedback(self, owner_id: str, message_id: str, feedback: str) -> dict[str, Any]:
        async with self.sessionmaker() as session, session.begin():
            row = await session.get(ConversationMessage, {"owner_id": owner_id, "id": message_id})
            if row is None:
                raise NotFoundError("message not found")
            if row.role != "assistant" or row.status != "done":
                raise ConflictError("feedback only applies to completed assistant messages")
            row.feedback = feedback
            await session.flush()
            return _message_dict(row)

    async def turn_user_text(self, owner_id: str, turn_id: str) -> tuple[str, str]:
        async with self.sessionmaker() as session:
            turn = await session.get(ConversationTurn, {"owner_id": owner_id, "id": turn_id})
            if turn is None:
                raise NotFoundError("turn not found")
            msg = await session.get(ConversationMessage, {"owner_id": owner_id, "id": turn.user_message_id})
            if msg is None:
                raise NotFoundError("user message not found")
            return turn.session_id, msg.content

    async def remote_counts(self, owner_id: str) -> dict[str, int]:
        async with self.sessionmaker() as session:
            counts = {
                "profile": await self._count(session, AppProfile, owner_id),
                "conversation_sessions": await self._count(session, ConversationSession, owner_id),
                "conversation_turns": await self._count(session, ConversationTurn, owner_id),
                "conversation_messages": await self._count(session, ConversationMessage, owner_id),
                "favorites": await self._count(session, AppFavorite, owner_id),
                "search_history": await self._count(session, AppHistory, owner_id),
                "idempotency_records": await self._count(session, IdempotencyRecord, owner_id),
            }
            counts["anonymous_identity"] = 1 if await session.get(AppIdentity, owner_id) else 0
            return counts

    async def delete_remote_data(self, owner_id: str) -> dict[str, int]:
        async with self.sessionmaker() as session, session.begin():
            before = {
                "profile": await self._count(session, AppProfile, owner_id),
                "conversation_sessions": await self._count(session, ConversationSession, owner_id),
                "conversation_turns": await self._count(session, ConversationTurn, owner_id),
                "conversation_messages": await self._count(session, ConversationMessage, owner_id),
                "conversation_attempts": await self._count(session, ConversationAttempt, owner_id),
                "conversation_summaries": await self._count(session, ConversationSummaryModel, owner_id),
                "favorites": await self._count(session, AppFavorite, owner_id),
                "search_history": await self._count(session, AppHistory, owner_id),
                "idempotency_records": await self._count(session, IdempotencyRecord, owner_id),
            }
            for model in (
                ConversationMessage,
                ConversationAttempt,
                ConversationTurn,
                ConversationSummaryModel,
                ConversationSession,
                AppFavorite,
                AppHistory,
                IdempotencyRecord,
                AppProfile,
            ):
                await session.execute(delete(model).where(model.owner_id == owner_id))
            identity = await session.get(AppIdentity, owner_id)
            if identity is not None and identity.kind == "anonymous":
                identity.revoked_at = identity.revoked_at or utcnow()
                before["anonymous_identity"] = 1
            else:
                before["anonymous_identity"] = 0
            return before

    async def _check_idempotency(
        self,
        session: AsyncSession,
        owner_id: str,
        scope: str,
        key: str,
        request_hash: str,
    ) -> dict[str, str] | None:
        existing = (await session.execute(select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        ))).scalar_one_or_none()
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError("idempotency key reused with different request")
        if existing.state == "running":
            raise RequestInProgressError("request already in progress")
        if existing.response_json:
            replay = {k: str(v) for k, v in existing.response_json.items() if v is not None}
            replay["_idempotency_replay"] = "completed"
            return replay
        return {
            "resource_id": str(existing.resource_id),
            "_idempotency_replay": str(existing.state),
        }

    async def _next_turn_ordinal(self, session: AsyncSession, owner_id: str, session_id: str) -> int:
        result = await session.execute(select(func.max(ConversationTurn.ordinal)).where(
            ConversationTurn.owner_id == owner_id,
            ConversationTurn.session_id == session_id,
        ))
        current = result.scalar_one_or_none()
        return int(current) + 1 if current is not None else 0

    async def _count(self, session: AsyncSession, model, owner_id: str) -> int:
        result = await session.execute(select(func.count()).select_from(model).where(model.owner_id == owner_id))
        return int(result.scalar_one())

    async def _turns_for_model_context(
        self,
        session: AsyncSession,
        owner_id: str,
        turns: list[ConversationTurn],
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for turn in turns:
            messages = (await session.execute(
                select(ConversationMessage)
                .where(
                    ConversationMessage.owner_id == owner_id,
                    ConversationMessage.turn_id == turn.id,
                )
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )).scalars().all()
            out.append({
                "turn_id": turn.id,
                "ordinal": turn.ordinal,
                "status": turn.status,
                "route": turn.route,
                "messages": [
                    _model_context_message(message)
                    for message in _visible_turn_messages(list(messages))
                ],
            })
        return out

    async def _assistant_for_attempt(
        self,
        session: AsyncSession,
        owner_id: str,
        turn_id: str,
        attempt_id: str,
    ) -> ConversationMessage | None:
        return (await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.owner_id == owner_id,
                ConversationMessage.turn_id == turn_id,
                ConversationMessage.attempt_id == attempt_id,
                ConversationMessage.role == "assistant",
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        )).scalars().first()

    async def _latest_done_assistant_for_turn(
        self,
        session: AsyncSession,
        owner_id: str,
        turn_id: str,
    ) -> ConversationMessage | None:
        return (await session.execute(
            select(ConversationMessage)
            .where(
                ConversationMessage.owner_id == owner_id,
                ConversationMessage.turn_id == turn_id,
                ConversationMessage.role == "assistant",
                ConversationMessage.status == "done",
            )
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        )).scalars().first()

    async def _finish_idempotency(
        self,
        session: AsyncSession,
        owner_id: str,
        session_id: str,
        turn_id: str,
        attempt: ConversationAttempt,
        assistant_message_id: str | None,
    ) -> None:
        if not attempt.idempotency_key:
            return
        rows = (await session.execute(select(IdempotencyRecord).where(
            IdempotencyRecord.owner_id == owner_id,
            IdempotencyRecord.key == attempt.idempotency_key,
            IdempotencyRecord.scope.in_((f"turn:{session_id}", f"attempt:{turn_id}")),
        ))).scalars().all()
        for idem in rows:
            idem.state = "completed"
            idem.response_json = {
                "session_id": session_id,
                "turn_id": turn_id,
                "attempt_id": attempt.id,
                "assistant_message_id": assistant_message_id,
            }


__all__ = [
    "AppStateError",
    "AppStateRepository",
    "ConflictError",
    "IdempotencyConflictError",
    "NotFoundError",
    "RequestInProgressError",
    "canonical_hash",
    "dt_to_iso",
    "new_uuid",
]
