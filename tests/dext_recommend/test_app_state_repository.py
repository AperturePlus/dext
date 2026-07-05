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
    ConversationSession,
    ConversationSummaryModel,
    ConversationTurn,
    IdempotencyRecord,
)
from dext_recommend.app_state.repositories import AppStateRepository, ConflictError, canonical_hash


def _recommendation(professor_id: str, name: str) -> dict:
    return {
        "professor_id": professor_id,
        "name": name,
        "university": "测试大学",
        "college": "计算机学院",
        "title": "教授",
        "research_fields": ["机器学习"],
        "reason": "方向匹配",
        "limitations": ["招生信息以官网为准"],
        "homepage_url": f"https://example.test/{professor_id}",
    }


async def _complete_recommendation_turn(
    repository: AppStateRepository,
    owner_id: str,
    session_id: str,
    *,
    request_id: str,
    expected_revision: int = 0,
    route: str = "recommendation",
    assistant_content: str = "已根据你的问题推荐了合适的导师。",
    related_recommendations: list[dict] | None = None,
) -> dict:
    text = f"推荐导师 {request_id[-2:]}"
    request_hash = canonical_hash({
        "owner_id": owner_id,
        "session_id": session_id,
        "text": text,
        "request_id": request_id,
        "expected_revision": expected_revision,
    })
    admitted = await repository.admit_turn(
        owner_id,
        session_id,
        text=text,
        request_id=request_id,
        expected_revision=expected_revision,
        idempotency_key=request_id,
        request_hash=request_hash,
    )
    recommendations = related_recommendations or [
        _recommendation("p1", "张老师"),
        _recommendation("p2", "李老师"),
    ]
    await repository.complete_attempt(
        owner_id,
        session_id=session_id,
        turn_id=admitted["turn_id"],
        attempt_id=admitted["attempt_id"],
        status="completed",
        route=route,
        assistant_content=assistant_content,
        related_recommendations=recommendations,
        context_json={},
        snapshot_json={"result_entity_ids": [item["professor_id"] for item in recommendations]},
    )
    return admitted


def _retry_request_hash(
    owner_id: str,
    session_id: str,
    turn_id: str,
    request_id: str,
    expected_revision: int,
) -> str:
    return canonical_hash({
        "owner_id": owner_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "request_id": request_id,
        "expected_revision": expected_revision,
    })


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


@pytest.mark.asyncio
async def test_create_fork_validates_source_recommendation_and_reuses_per_professor():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        source = await repository.create_session(owner_id)
        admitted = await _complete_recommendation_turn(
            repository,
            owner_id,
            source["id"],
            request_id="00000000-0000-0000-0000-0000000000c1",
        )

        fork = await repository.create_fork(owner_id, source["id"], admitted["turn_id"], "p1")
        same = await repository.create_fork(owner_id, source["id"], admitted["turn_id"], "p1")
        other = await repository.create_fork(owner_id, source["id"], admitted["turn_id"], "p2")

        assert fork["id"] == same["id"]
        assert other["id"] != fork["id"]
        assert fork["kind"] == "fork"
        assert fork["root_session_id"] == source["root_session_id"]
        assert fork["source_session_id"] == source["id"]
        assert fork["source_turn_id"] == admitted["turn_id"]
        assert fork["professor_id"] == "p1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_fork_rejects_invalid_source_or_professor():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        source = await repository.create_session(owner_id)
        admitted = await _complete_recommendation_turn(
            repository,
            owner_id,
            source["id"],
            request_id="00000000-0000-0000-0000-0000000000c2",
        )
        other_session = await repository.create_session(owner_id)

        with pytest.raises(ConflictError, match="does not belong"):
            await repository.create_fork(owner_id, other_session["id"], admitted["turn_id"], "p1")
        with pytest.raises(ConflictError, match="does not contain professor"):
            await repository.create_fork(owner_id, source["id"], admitted["turn_id"], "missing")

        queued = await repository.create_session(owner_id)
        request_id = "00000000-0000-0000-0000-0000000000c3"
        request_hash = canonical_hash({
            "owner_id": owner_id,
            "session_id": queued["id"],
            "text": "推荐导师",
            "request_id": request_id,
            "expected_revision": 0,
        })
        queued_turn = await repository.admit_turn(
            owner_id,
            queued["id"],
            text="推荐导师",
            request_id=request_id,
            expected_revision=0,
            idempotency_key=request_id,
            request_hash=request_hash,
        )
        with pytest.raises(ConflictError, match="completed recommendation"):
            await repository.create_fork(owner_id, queued["id"], queued_turn["turn_id"], "p1")

        conversation = await repository.create_session(owner_id)
        conversation_turn = await _complete_recommendation_turn(
            repository,
            owner_id,
            conversation["id"],
            request_id="00000000-0000-0000-0000-0000000000c4",
            route="conversation",
        )
        with pytest.raises(ConflictError, match="completed recommendation"):
            await repository.create_fork(
                owner_id,
                conversation["id"],
                conversation_turn["turn_id"],
                "p1",
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("route", "request_id", "retry_request_id"),
    [
        (
            "recommendation",
            "00000000-0000-0000-0000-0000000010a1",
            "00000000-0000-0000-0000-0000000010a2",
        ),
        (
            "conversation",
            "00000000-0000-0000-0000-0000000010b1",
            "00000000-0000-0000-0000-0000000010b2",
        ),
    ],
)
@pytest.mark.asyncio
async def test_create_retry_attempt_allows_latest_completed_recommendation_or_conversation(
    route,
    request_id,
    retry_request_id,
):
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        created_session = await repository.create_session(owner_id)
        admitted = await _complete_recommendation_turn(
            repository,
            owner_id,
            created_session["id"],
            request_id=request_id,
            route=route,
        )

        retry = await repository.create_retry_attempt(
            owner_id,
            admitted["turn_id"],
            session_id=created_session["id"],
            request_id=retry_request_id,
            expected_revision=1,
            idempotency_key=retry_request_id,
            request_hash=_retry_request_hash(
                owner_id,
                created_session["id"],
                admitted["turn_id"],
                retry_request_id,
                1,
            ),
        )

        assert retry["turn_id"] == admitted["turn_id"]
        assert retry["attempt_id"]
        async with sessionmaker() as session:
            turn = await session.get(
                ConversationTurn,
                {"owner_id": owner_id, "id": admitted["turn_id"]},
            )
            attempt = await session.get(
                ConversationAttempt,
                {"owner_id": owner_id, "id": retry["attempt_id"]},
            )
        assert turn is not None
        assert attempt is not None
        assert turn.status == "queued"
        assert turn.route == route
        assert turn.active_attempt_id == retry["attempt_id"]
        assert attempt.status == "queued"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_retry_attempt_rejects_completed_fork_reroute_and_non_latest_turn():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"

        fork_reroute_session = await repository.create_session(owner_id)
        fork_reroute = await _complete_recommendation_turn(
            repository,
            owner_id,
            fork_reroute_session["id"],
            request_id="00000000-0000-0000-0000-0000000011a1",
            route="forkReroute",
        )
        retry_request_id = "00000000-0000-0000-0000-0000000011a2"
        with pytest.raises(ConflictError, match="completed turn cannot be retried"):
            await repository.create_retry_attempt(
                owner_id,
                fork_reroute["turn_id"],
                session_id=fork_reroute_session["id"],
                request_id=retry_request_id,
                expected_revision=1,
                idempotency_key=retry_request_id,
                request_hash=_retry_request_hash(
                    owner_id,
                    fork_reroute_session["id"],
                    fork_reroute["turn_id"],
                    retry_request_id,
                    1,
                ),
            )

        conversation = await repository.create_session(owner_id)
        first = await _complete_recommendation_turn(
            repository,
            owner_id,
            conversation["id"],
            request_id="00000000-0000-0000-0000-0000000011b1",
        )
        await _complete_recommendation_turn(
            repository,
            owner_id,
            conversation["id"],
            request_id="00000000-0000-0000-0000-0000000011b2",
            expected_revision=1,
        )
        stale_retry_request_id = "00000000-0000-0000-0000-0000000011b3"
        with pytest.raises(ConflictError, match="completed turn cannot be retried"):
            await repository.create_retry_attempt(
                owner_id,
                first["turn_id"],
                session_id=conversation["id"],
                request_id=stale_retry_request_id,
                expected_revision=2,
                idempotency_key=stale_retry_request_id,
                request_hash=_retry_request_hash(
                    owner_id,
                    conversation["id"],
                    first["turn_id"],
                    stale_retry_request_id,
                    2,
                ),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_retry_success_replaces_visible_assistant_but_keeps_history():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        created_session = await repository.create_session(owner_id)
        admitted = await _complete_recommendation_turn(
            repository,
            owner_id,
            created_session["id"],
            request_id="00000000-0000-0000-0000-0000000012a1",
            assistant_content="first answer",
            related_recommendations=[_recommendation("p1", "张老师")],
        )
        retry_request_id = "00000000-0000-0000-0000-0000000012a2"
        retry = await repository.create_retry_attempt(
            owner_id,
            admitted["turn_id"],
            session_id=created_session["id"],
            request_id=retry_request_id,
            expected_revision=1,
            idempotency_key=retry_request_id,
            request_hash=_retry_request_hash(
                owner_id,
                created_session["id"],
                admitted["turn_id"],
                retry_request_id,
                1,
            ),
        )
        await repository.complete_attempt(
            owner_id,
            session_id=created_session["id"],
            turn_id=admitted["turn_id"],
            attempt_id=retry["attempt_id"],
            status="completed",
            route="recommendation",
            assistant_content="second answer",
            related_recommendations=[_recommendation("p3", "王老师")],
            context_json={},
            snapshot_json={"result_entity_ids": ["p3"]},
        )

        projection = await repository.get_session_projection(owner_id, created_session["id"])
        assistant_messages = [
            message for message in projection["messages"]
            if message["role"] == "assistant"
        ]
        assert [message["content"] for message in assistant_messages] == ["second answer"]
        assert assistant_messages[0]["related_recommendations"][0]["professor_id"] == "p3"
        async with sessionmaker() as session:
            assistant_count = await session.scalar(
                select(func.count()).select_from(ConversationMessage).where(
                    ConversationMessage.owner_id == owner_id,
                    ConversationMessage.turn_id == admitted["turn_id"],
                    ConversationMessage.role == "assistant",
                )
            )
        assert assistant_count == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_retry_failure_preserves_old_answer_and_shows_latest_error():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        created_session = await repository.create_session(owner_id)
        admitted = await _complete_recommendation_turn(
            repository,
            owner_id,
            created_session["id"],
            request_id="00000000-0000-0000-0000-0000000013a1",
            assistant_content="stable answer",
            related_recommendations=[_recommendation("p1", "张老师")],
        )
        retry_request_id = "00000000-0000-0000-0000-0000000013a2"
        retry = await repository.create_retry_attempt(
            owner_id,
            admitted["turn_id"],
            session_id=created_session["id"],
            request_id=retry_request_id,
            expected_revision=1,
            idempotency_key=retry_request_id,
            request_hash=_retry_request_hash(
                owner_id,
                created_session["id"],
                admitted["turn_id"],
                retry_request_id,
                1,
            ),
        )

        completed = await repository.complete_attempt(
            owner_id,
            session_id=created_session["id"],
            turn_id=admitted["turn_id"],
            attempt_id=retry["attempt_id"],
            status="failed",
            route="recommendation",
            assistant_content="retry failed",
            related_recommendations=[],
            context_json={"intent": "new_search"},
            snapshot_json={"result_entity_ids": ["failed"]},
        )

        assert completed["turn"]["status"] == "completed"
        assert completed["assistant_message"]["status"] == "error"
        assert completed["assistant_message"]["kind"] == "recommendation"
        projection = await repository.get_session_projection(owner_id, created_session["id"])
        assistant_messages = [
            message for message in projection["messages"]
            if message["role"] == "assistant"
        ]
        assert [message["content"] for message in assistant_messages] == [
            "stable answer",
            "retry failed",
        ]
        assert [message["status"] for message in assistant_messages] == ["done", "error"]
        async with sessionmaker() as session:
            turn = await session.get(
                ConversationTurn,
                {"owner_id": owner_id, "id": admitted["turn_id"]},
            )
        assert turn is not None
        assert turn.status == "completed"
        assert turn.snapshot_json == {"result_entity_ids": ["p1"]}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fork_model_context_resolves_professor_from_historical_recommendation():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        source = await repository.create_session(owner_id)
        source_turn = await _complete_recommendation_turn(
            repository,
            owner_id,
            source["id"],
            request_id="00000000-0000-0000-0000-0000000014a1",
            related_recommendations=[_recommendation("p1", "张老师")],
        )
        fork = await repository.create_fork(owner_id, source["id"], source_turn["turn_id"], "p1")
        retry_request_id = "00000000-0000-0000-0000-0000000014a2"
        retry = await repository.create_retry_attempt(
            owner_id,
            source_turn["turn_id"],
            session_id=source["id"],
            request_id=retry_request_id,
            expected_revision=1,
            idempotency_key=retry_request_id,
            request_hash=_retry_request_hash(
                owner_id,
                source["id"],
                source_turn["turn_id"],
                retry_request_id,
                1,
            ),
        )
        await repository.complete_attempt(
            owner_id,
            session_id=source["id"],
            turn_id=source_turn["turn_id"],
            attempt_id=retry["attempt_id"],
            status="completed",
            route="recommendation",
            assistant_content="new answer",
            related_recommendations=[_recommendation("p3", "王老师")],
            context_json={},
            snapshot_json={"result_entity_ids": ["p3"]},
        )
        fork_question_id = "00000000-0000-0000-0000-0000000014a3"
        fork_question_hash = canonical_hash({
            "owner_id": owner_id,
            "session_id": fork["id"],
            "text": "这位导师适合我吗",
            "request_id": fork_question_id,
            "expected_revision": 0,
        })
        fork_turn = await repository.admit_turn(
            owner_id,
            fork["id"],
            text="这位导师适合我吗",
            request_id=fork_question_id,
            expected_revision=0,
            idempotency_key=fork_question_id,
            request_hash=fork_question_hash,
        )

        context = await repository.get_fork_model_context(
            owner_id,
            fork["id"],
            fork_turn["turn_id"],
            "这位导师适合我吗",
        )

        assert context is not None
        assert context["selected_recommendation"]["professor_id"] == "p1"
        assert context["selected_recommendation"]["name"] == "张老师"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_soft_delete_all_session_trees_marks_sessions_and_clears_chat_rows():
    engine = create_async_engine_from_settings(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True)
    )
    try:
        await ensure_schema_ready(engine, bootstrap=True)
        sessionmaker = create_sessionmaker(engine)
        repository = AppStateRepository(sessionmaker)
        owner_id = "00000000-0000-0000-0000-000000000001"
        other_owner_id = "00000000-0000-0000-0000-000000000002"

        source = await repository.create_session(owner_id)
        source_turn = await _complete_recommendation_turn(
            repository,
            owner_id,
            source["id"],
            request_id="00000000-0000-0000-0000-0000000000d1",
        )
        fork = await repository.create_fork(owner_id, source["id"], source_turn["turn_id"], "p1")
        fork_turn = await _complete_recommendation_turn(
            repository,
            owner_id,
            fork["id"],
            request_id="00000000-0000-0000-0000-0000000000d2",
        )
        second = await repository.create_session(owner_id)
        second_turn = await _complete_recommendation_turn(
            repository,
            owner_id,
            second["id"],
            request_id="00000000-0000-0000-0000-0000000000d3",
        )
        other = await repository.create_session(other_owner_id)
        other_turn = await _complete_recommendation_turn(
            repository,
            other_owner_id,
            other["id"],
            request_id="00000000-0000-0000-0000-0000000000e1",
        )
        async with sessionmaker() as db_session, db_session.begin():
            db_session.add_all([
                ConversationSummaryModel(
                    owner_id=owner_id,
                    id="00000000-0000-0000-0000-000000000101",
                    session_id=source["id"],
                    through_turn_id=source_turn["turn_id"],
                    text="source summary",
                ),
                ConversationSummaryModel(
                    owner_id=owner_id,
                    id="00000000-0000-0000-0000-000000000102",
                    session_id=fork["id"],
                    through_turn_id=fork_turn["turn_id"],
                    text="fork summary",
                ),
                ConversationSummaryModel(
                    owner_id=owner_id,
                    id="00000000-0000-0000-0000-000000000103",
                    session_id=second["id"],
                    through_turn_id=second_turn["turn_id"],
                    text="second summary",
                ),
                ConversationSummaryModel(
                    owner_id=other_owner_id,
                    id="00000000-0000-0000-0000-000000000104",
                    session_id=other["id"],
                    through_turn_id=other_turn["turn_id"],
                    text="other summary",
                ),
            ])

        deleted_count = await repository.soft_delete_all_session_trees(owner_id)

        assert deleted_count == 3
        assert await repository.list_sessions(owner_id) == []
        assert await repository.soft_delete_all_session_trees(owner_id) == 0
        async with sessionmaker() as db_session:
            owner_sessions = (await db_session.execute(
                select(ConversationSession).where(ConversationSession.owner_id == owner_id)
            )).scalars().all()
            assert {row.id for row in owner_sessions} == {source["id"], fork["id"], second["id"]}
            assert all(row.deleted_at is not None for row in owner_sessions)

            for model in (
                ConversationTurn,
                ConversationAttempt,
                ConversationMessage,
                ConversationSummaryModel,
                IdempotencyRecord,
            ):
                remaining = await db_session.scalar(
                    select(func.count()).select_from(model).where(model.owner_id == owner_id)
                )
                assert remaining == 0

            assert await db_session.scalar(
                select(func.count()).select_from(ConversationSession).where(
                    ConversationSession.owner_id == other_owner_id,
                    ConversationSession.deleted_at.is_(None),
                )
            ) == 1
            for model in (
                ConversationTurn,
                ConversationAttempt,
                ConversationSummaryModel,
                IdempotencyRecord,
            ):
                remaining = await db_session.scalar(
                    select(func.count()).select_from(model).where(model.owner_id == other_owner_id)
                )
                assert remaining == 1
            assert await db_session.scalar(
                select(func.count()).select_from(ConversationMessage).where(
                    ConversationMessage.owner_id == other_owner_id,
                )
            ) == 2
    finally:
        await engine.dispose()
