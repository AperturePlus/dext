from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy import func, select

from dext_recommend.api.settings import AppSettings
from dext_recommend.app_state.db import (
    create_async_engine_from_settings,
    create_sessionmaker,
    ensure_schema_ready,
)
from dext_recommend.app_state.models import (
    ConversationAttempt,
    ConversationMessage,
    ConversationTurn,
)
from dext_recommend.app_state.repositories import AppStateRepository, canonical_hash


@pytest.mark.asyncio
async def test_admit_turn_satisfies_foreign_keys_when_creating_dependent_rows():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        created_session = await repository.create_session(owner_id)
        request_id = "00000000-0000-0000-0000-0000000000aa"
        request_hash = canonical_hash({
            "owner_id": owner_id,
            "session_id": created_session["id"],
            "text": "推荐机器学习导师",
            "request_id": request_id,
            "expected_revision": 0,
        })

        admitted = await repository.admit_turn(
            owner_id,
            created_session["id"],
            text="推荐机器学习导师",
            request_id=request_id,
            expected_revision=0,
            idempotency_key=request_id,
            request_hash=request_hash,
        )

        assert admitted["session_id"] == created_session["id"]
        assert admitted["turn_id"]
        assert admitted["attempt_id"]
        assert admitted["user_message_id"]

        async with sessionmaker() as session:
            turn = await session.get(
                ConversationTurn,
                {"owner_id": owner_id, "id": admitted["turn_id"]},
            )
            attempt = await session.get(
                ConversationAttempt,
                {"owner_id": owner_id, "id": admitted["attempt_id"]},
            )
            message = await session.get(
                ConversationMessage,
                {"owner_id": owner_id, "id": admitted["user_message_id"]},
            )

        assert turn is not None
        assert attempt is not None
        assert message is not None
        assert attempt.turn_id == turn.id
        assert message.turn_id == turn.id
        assert message.attempt_id == attempt.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_complete_attempt_is_idempotent_and_replayable():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        created_session = await repository.create_session(owner_id)
        request_id = "00000000-0000-0000-0000-0000000000ab"
        request_hash = canonical_hash({
            "owner_id": owner_id,
            "session_id": created_session["id"],
            "text": "推荐机器学习导师",
            "request_id": request_id,
            "expected_revision": 0,
        })
        admitted = await repository.admit_turn(
            owner_id,
            created_session["id"],
            text="推荐机器学习导师",
            request_id=request_id,
            expected_revision=0,
            idempotency_key=request_id,
            request_hash=request_hash,
        )

        first = await repository.complete_attempt(
            owner_id,
            session_id=created_session["id"],
            turn_id=admitted["turn_id"],
            attempt_id=admitted["attempt_id"],
            status="completed",
            route="recommendation",
            assistant_content="first answer",
            related_recommendations=[],
            context_json={},
            snapshot_json={},
        )
        second = await repository.complete_attempt(
            owner_id,
            session_id=created_session["id"],
            turn_id=admitted["turn_id"],
            attempt_id=admitted["attempt_id"],
            status="completed",
            route="recommendation",
            assistant_content="second answer",
            related_recommendations=[],
            context_json={},
            snapshot_json={},
        )

        assert second["assistant_message"] == first["assistant_message"]
        async with sessionmaker() as session:
            assistant_count = await session.scalar(
                select(func.count()).select_from(ConversationMessage).where(
                    ConversationMessage.owner_id == owner_id,
                    ConversationMessage.turn_id == admitted["turn_id"],
                    ConversationMessage.role == "assistant",
                )
            )
        assert assistant_count == 1

        replay = await repository.admit_turn(
            owner_id,
            created_session["id"],
            text="推荐机器学习导师",
            request_id=request_id,
            expected_revision=0,
            idempotency_key=request_id,
            request_hash=request_hash,
        )
        assert replay["_idempotency_replay"] == "completed"
        assert replay["turn_id"] == admitted["turn_id"]
        assert replay["attempt_id"] == admitted["attempt_id"]
        assert replay["assistant_message_id"] == first["assistant_message"]["id"]
    finally:
        await engine.dispose()
