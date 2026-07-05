from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dext_recommend.api import AppSettings, create_recommendation_app
from dext_recommend.generation.achievement_extraction import fallback_achievement_draft
from dext_recommend.models import (
    AuxiliaryGenerationResult,
    ConversationDispatchResult,
    QueryDiagnostics,
    QueryUnderstanding,
    RecommendResponse,
    RecommendedProfessor,
    RecommendationWarning,
)
from dext_recommend.ports.release_readback import ReadinessSourceError
from dext_recommend.readiness import ActiveBuildSnapshot


def _recommended_professor() -> RecommendedProfessor:
    return RecommendedProfessor(
        entity_id="p1",
        display_name="张老师",
        university="测试大学",
        org_units=("计算机学院",),
        title="教授",
        title_family="professor",
        master_eligibility="confirmed",
        phd_eligibility="confirmed",
        role_status="included",
        profile_url="https://example.test/p1",
        research_summary="机器学习",
        match_level="strong",
        short_reasons=("方向匹配",),
        score=0.86,
        score_components={},
        matched_topics=("机器学习",),
        matched_statements=(),
        matched_publications=(),
        evidence_refs=(),
        risk_flags=(),
        available_actions=("detail",),
    )


def _recommend_response(
    *,
    query_length: int = 4,
    results: tuple[RecommendedProfessor, ...] | None = None,
    warnings: tuple[RecommendationWarning, ...] = (),
    recall_count: int = 1,
    post_filter_count: int = 1,
    returned_count: int | None = None,
) -> RecommendResponse:
    resolved_results = (_recommended_professor(),) if results is None else results
    return RecommendResponse(
        build_id="build-1",
        ranking_profile_version="rank-v1",
        embedding_fingerprint="fp",
        taxonomy_version="tax-v1",
        generation_profile_version="gen-v1",
        query_understanding=QueryUnderstanding(
            research_interests=("机器学习",),
            preferred_universities=(),
            preferred_cities=(),
            preferred_org_units=(),
            degree_goal="master",
            mentor_eligibility_requirement=None,
            missing_information=(),
            needs_clarification=False,
            confidence=0.9,
        ),
        query=QueryDiagnostics(
            query_length=query_length,
            language_summary="zh",
            filter_summary=None,
            recall_count=recall_count,
            post_filter_count=post_filter_count,
            returned_count=len(resolved_results) if returned_count is None else returned_count,
            steps_used=1,
        ),
        results=resolved_results,
        suggested_followups=("了解招生要求",),
        warnings=warnings,
    )


class FakeCore:
    async def recommend(self, request, *, viewer_permissions=None):
        return _recommend_response(query_length=len(request.query_text))


class FakeConversation:
    def __init__(self):
        self.calls = 0

    async def dispatch(self, request, *, viewer_permissions=None):
        self.calls += 1
        response = await FakeCore().recommend(request, viewer_permissions=viewer_permissions)
        return ConversationDispatchResult(
            kind="recommendation",
            context=request.conversation_context,
            recommendation=response,
            detail_followup=None,
            issues=(),
        )


class ExplodingConversation:
    async def dispatch(self, request, *, viewer_permissions=None):
        raise RuntimeError("dispatch exploded")


class SlowConversation(FakeConversation):
    async def dispatch(self, request, *, viewer_permissions=None):
        await asyncio.sleep(0.05)
        return await super().dispatch(request, viewer_permissions=viewer_permissions)


class EmptyRecommendationConversation:
    async def dispatch(self, request, *, viewer_permissions=None):
        response = _recommend_response(
            query_length=len(request.query_text),
            results=(),
            warnings=(
                RecommendationWarning(
                    code="no_candidates_after_filters",
                    message="no candidates after filters",
                ),
            ),
            recall_count=8,
            post_filter_count=0,
            returned_count=0,
        )
        return ConversationDispatchResult(
            kind="recommendation",
            context=request.conversation_context,
            recommendation=response,
            detail_followup=None,
            issues=(),
        )


class ErrorWarningRecommendationConversation:
    async def dispatch(self, request, *, viewer_permissions=None):
        response = _recommend_response(
            query_length=len(request.query_text),
            results=(),
            warnings=(
                RecommendationWarning(
                    code="llm_unavailable",
                    message="LLM failed",
                    severity="error",
                ),
            ),
            recall_count=0,
            post_filter_count=0,
            returned_count=0,
        )
        return ConversationDispatchResult(
            kind="recommendation",
            context=request.conversation_context,
            recommendation=response,
            detail_followup=None,
            issues=(),
        )


class HangingConversation:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def dispatch(self, request, *, viewer_permissions=None):
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class FakeQuickActions:
    def __init__(self):
        self.calls = []

    async def generate(self, follow_up, last_recommendations=None):
        self.calls.append({
            "follow_up": follow_up,
            "last_recommendations": list(last_recommendations or []),
        })
        return ["动态筛选", "论文方向"] if follow_up else ["开始推荐"]


class FakeAchievementExtraction:
    def __init__(self):
        self.calls = []

    async def extract(self, raw_text):
        self.calls.append(raw_text)
        return fallback_achievement_draft(raw_text)


class ErrorAuxiliaryGeneration:
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        self.calls = []

    async def analyze_match(
        self,
        entity_id,
        student_context,
        evidence_policy,
        *,
        viewer_permissions=None,
        request_id=None,
    ):
        self.calls.append({
            "entity_id": entity_id,
            "student_context": student_context,
            "evidence_policy": evidence_policy,
            "request_id": request_id,
            "viewer_permissions": viewer_permissions,
        })
        return AuxiliaryGenerationResult(
            kind="error",
            issues=(RecommendationWarning(
                code=self.code,
                message=self.message,
                severity="error",
            ),),
            generation_profile_version="gen-v1",
        )


class FakeConversationTitles:
    def __init__(self, *, raise_error: bool = False):
        self.calls = []
        self.raise_error = raise_error

    async def generate(self, first_user_message, assistant_answer=""):
        self.calls.append({
            "first_user_message": first_user_message,
            "assistant_answer": assistant_answer,
        })
        if self.raise_error:
            raise RuntimeError("title generation failed")
        return "机器学习导师"


class FakeRuntime:
    def __init__(self, *, title_error: bool = False):
        self.core = FakeCore()
        self.conversation = FakeConversation()
        self.auxiliary_generation = SimpleNamespace()
        self.achievement_extraction = FakeAchievementExtraction()
        self.conversation_titles = FakeConversationTitles(raise_error=title_error)
        self.quick_actions = FakeQuickActions()
        self.readiness = SimpleNamespace(get_snapshot=lambda: None)
        self.closed = False

    async def aclose(self):
        self.closed = True


class ReadinessErrorFacts:
    async def get_detail(self, snapshot, entity_id, include_contacts, viewer_permissions):
        raise ReadinessSourceError(
            "catalog", "read timed out after 5.0s", retryable=True,
        )


async def fake_runtime_factory(*args, **kwargs):
    return FakeRuntime()


def _snapshot() -> ActiveBuildSnapshot:
    return ActiveBuildSnapshot(
        build_id="build-1",
        catalog_schema_version=6,
        neo4j_active_build_id="build-1",
        qdrant_alias_target="dext_professors__build_1",
        qdrant_payload_schema_version=2,
        embedding_provider="test",
        embedding_model="test-model",
        embedding_dimension=1024,
        embedding_fingerprint="fp",
        taxonomy_version="tax-v1",
        ranking_profile_version="rank-v1",
        created_at=datetime.now(timezone.utc),
    )


async def exploding_runtime_factory(*args, **kwargs):
    runtime = FakeRuntime()
    runtime.conversation = ExplodingConversation()
    return runtime


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for raw_event in text.strip().split("\n\n"):
        if not raw_event.strip():
            continue
        event_name = "message"
        data_lines: list[str] = []
        for line in raw_event.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        if data_lines:
            events.append((event_name, json.loads("\n".join(data_lines))))
    return events


async def _read_raw_sse_event(response) -> str:
    lines: list[str] = []
    while True:
        line = await response.content.readline()
        assert line
        decoded = line.decode("utf-8")
        lines.append(decoded)
        if decoded in {"\n", "\r\n"}:
            return "".join(lines)


def _assert_sse_sequence(events: list[tuple[str, dict]]) -> None:
    for seq, (_, data) in enumerate(events):
        assert data["seq"] == seq


def _assert_sse_context(data: dict, *, session_id: str, turn_id: str, attempt_id: str) -> None:
    assert data["session_id"] == session_id
    assert data["turn_id"] == turn_id
    assert data["attempt_id"] == attempt_id
    assert isinstance(data["revision"], int)
    assert isinstance(data["seq"], int)


@pytest.mark.asyncio
async def test_identity_profile_favorite_history_flow():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/v1/identity/anonymous")
        assert resp.status == 200
        identity = await resp.json()
        token = identity["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.put(
            "/api/v1/profile",
            headers=headers,
            json={"degree_stage": "本科", "score": {"gpa": 3.8, "scale": 4.0, "rank_mode": "percent", "percent": 10}},
        )
        assert resp.status == 200
        assert (await resp.json())["data"]["score"]["gpa"] == 3.8

        resp = await client.put(
            "/api/v1/favorites/p1",
            headers=headers,
            json={
                "professor_id": "p1",
                "name": "张老师",
                "university": "测试大学",
                "college": "计算机学院",
                "title": "教授",
                "research_fields": ["机器学习"],
            },
        )
        assert resp.status == 200
        assert (await resp.json())["data"]["favorited"] is True
        resp = await client.get("/api/v1/favorites", headers=headers)
        assert (await resp.json())["data"][0]["professor_id"] == "p1"

        resp = await client.post(
            "/api/v1/history",
            headers=headers,
            json={
                "type": "mentor",
                "session_id": "00000000-0000-0000-0000-000000000001",
                "prompt": "机器学习",
                "created_at": "2026-07-03T00:00:00Z",
                "summary": "推荐摘要",
                "research_interests": ["机器学习"],
                "preferred_locations": [],
                "recommendation_count": 1,
            },
        )
        assert resp.status == 200
        resp = await client.get("/api/v1/history", headers=headers)
        assert (await resp.json())["data"][0]["prompt"] == "机器学习"


@pytest.mark.asyncio
async def test_extract_achievements_splits_competition_and_paper():
    runtime = FakeRuntime()

    async def runtime_factory(*args, **kwargs):
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        resp = await client.post(
            "/api/v1/profile/achievements/extract",
            headers=headers,
            json={"raw_text": "ACM区域赛银牌；一篇CCF-B论文"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["code"] == 0
        assert runtime.achievement_extraction.calls == ["ACM区域赛银牌；一篇CCF-B论文"]

        data = body["data"]
        assert len(data["competitions"]) >= 1
        competition = data["competitions"][0]
        assert "ACM" in competition["name"]
        assert "区域赛" in competition["name"]
        assert competition["award"] == "银牌"
        assert set(competition) >= {"name", "level", "award", "year"}

        assert len(data["research"]) >= 1
        paper = next(item for item in data["research"] if item["type"] == "paper")
        assert "CCF-B" in paper["title"]
        assert paper["title"] != "科研经历"
        assert set(paper) >= {"type", "title", "role", "venue_or_status", "year"}


@pytest.mark.asyncio
async def test_recommendations_route_uses_fake_runtime():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/api/v1/identity/anonymous")
        token = (await resp.json())["data"]["access_token"]
        resp = await client.post(
            "/api/v1/recommendations/mentors",
            headers={"Authorization": f"Bearer {token}"},
            json={"prompt": "想找机器学习导师", "profile": {"score": {"rank_mode": "percent", "percent": 10}}},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["recommendations"][0]["professor_id"] == "p1"


@pytest.mark.asyncio
async def test_professor_detail_readiness_error_returns_503():
    async def runtime_factory(*args, **kwargs):
        runtime = FakeRuntime()
        runtime.readiness = SimpleNamespace(get_snapshot=_snapshot)
        runtime.core = SimpleNamespace(
            deps=SimpleNamespace(facts_port=ReadinessErrorFacts()),
        )
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/professors/p1")
        body = await resp.json()

    assert resp.status == 503
    assert body["error_code"] == "readiness_source_unavailable"
    assert body["message"] == "导师详情暂时不可用，请稍后重试"
    assert body["data"] == {"source": "catalog", "retryable": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "message", "status"), [
    ("llm_unavailable", "match_analysis provider unavailable", 503),
    ("request_timeout", "match_analysis generation timed out", 504),
])
async def test_match_analysis_generation_errors_map_http_and_pass_request_id(
    code: str,
    message: str,
    status: int,
):
    auxiliary = ErrorAuxiliaryGeneration(code, message)

    async def runtime_factory(*args, **kwargs):
        runtime = FakeRuntime()
        runtime.auxiliary_generation = auxiliary
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        token = identity["data"]["access_token"]
        request_id = "019f2e47-cf3c-7eb5-afce-e200d135fbe7"
        resp = await client.post(
            "/api/v1/professors/p1/match-analysis",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Request-ID": request_id,
            },
            json={"profile": {"research_interests": ["NLP"]}},
        )
        body = await resp.json()

    assert resp.status == status
    assert resp.headers["X-Request-ID"] == request_id
    assert body["code"] == status * 100 + 1
    assert body["error_code"] == code
    assert body["message"] == message
    assert auxiliary.calls[0]["entity_id"] == "p1"
    assert auxiliary.calls[0]["request_id"] == request_id


@pytest.mark.asyncio
async def test_home_config_supports_competition_mode():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/home/config?mode=competition")
        body = await resp.json()
        assert resp.status == 200
        assert "竞赛" in body["data"]["taglines"][0]
        assert "数学建模" in body["data"]["quick_tags"]
        assert "导师" not in body["data"]["prompts"][0]["text"]

        resp = await client.get("/api/v1/home/prompts?mode=competition")
        body = await resp.json()
        assert resp.status == 200
        assert "竞赛" in body["data"][0]["text"] or "比赛" in body["data"][0]["text"]

        resp = await client.get("/api/v1/home/config?mode=unknown")
        body = await resp.json()
        assert resp.status == 422
        assert body["error_code"] == "invalid_mode"


def test_public_profile_accepts_flutter_score_fields():
    from dext_recommend.api.schemas import UserProfile

    profile = UserProfile.model_validate({"score": {"gpa": 3.9, "scale": 4.0, "rank_mode": "percent", "percent": 8}})
    assert profile.score.gpa == 3.9


@pytest.mark.asyncio
async def test_request_id_cors_and_feedback_envelope():
    app = create_recommendation_app(
        AppSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            schema_bootstrap=True,
            cors_allowed_origins=("https://app.example",),
        ),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        request_id = "0197b000-0000-7000-8000-000000000001"
        resp = await client.options(
            "/api/v1/profile",
            headers={
                "Origin": "https://app.example",
                "Access-Control-Request-Headers": "authorization,x-request-id",
                "X-Request-ID": request_id,
            },
        )
        assert resp.status == 204
        assert resp.headers["X-Request-ID"] == request_id
        assert resp.headers["Access-Control-Allow-Origin"] == "https://app.example"
        assert resp.headers["Access-Control-Allow-Credentials"] == "true"
        assert resp.headers["Access-Control-Expose-Headers"] == "X-Request-ID"

        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        token = identity["data"]["access_token"]
        resp = await client.post(
            "/api/v1/feedback",
            headers={"Authorization": f"Bearer {token}", "X-Request-ID": request_id},
            json={
                "id": "fb_1",
                "type": "bug",
                "content": "按钮无响应",
                "context": {"route": "/profile", "data_source_mode": "http"},
                "created_at": "2026-07-03T00:00:00Z",
            },
        )
        body = await resp.json()
        assert resp.headers["X-Request-ID"] == request_id
        assert body["code"] == 0
        assert body["data"]["status"] == "received"


@pytest.mark.asyncio
async def test_new_turn_sse_contract():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000aa"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id, "X-Request-ID": request_id},
            json={"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 0},
        )
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        events = _parse_sse(await resp.text())
        assert [name for name, _ in events] == ["ack", "route", "delta", "completed"]
        _assert_sse_sequence(events)

        ack = events[0][1]
        turn_id = ack["turn_id"]
        attempt_id = ack["attempt_id"]
        assert {key: ack[key] for key in ("session_id", "turn_id", "attempt_id", "revision")} == {
            "session_id": session_id,
            "turn_id": turn_id,
            "attempt_id": attempt_id,
            "revision": 0,
        }
        route = events[1][1]
        _assert_sse_context(route, session_id=session_id, turn_id=turn_id, attempt_id=attempt_id)
        assert route["revision"] == 0
        assert route["route"] == "recommendation"
        delta = events[2][1]
        _assert_sse_context(delta, session_id=session_id, turn_id=turn_id, attempt_id=attempt_id)
        assert delta["revision"] == 0
        completed = events[3][1]
        _assert_sse_context(completed, session_id=session_id, turn_id=turn_id, attempt_id=attempt_id)
        assert completed["revision"] == 1
        assert completed["session"]["revision"] == 1
        assert completed["session"]["title"] == "机器学习导师"
        assert completed["message"]["role"] == "assistant"
        assert completed["message"]["status"] == "done"
        assert completed["message"]["kind"] == "recommendation"
        assert completed["message"]["related_recommendations"][0]["professor_id"] == "p1"
        listed = await (await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
        )).json()
        assert listed["data"]["items"][0]["title"] == "机器学习导师"

        aggregate = await (await client.get(
            f"/api/v1/chat/sessions/{session_id}",
            headers=headers,
        )).json()
        assert aggregate["data"]["session"]["title"] == "机器学习导师"
        assistant_messages = [
            message for message in aggregate["data"]["messages"]
            if message["role"] == "assistant"
        ]
        assert len(assistant_messages) == 1
        assert assistant_messages[0]["related_recommendations"][0]["professor_id"] == "p1"

        second_request_id = "00000000-0000-0000-0000-0000000000ab"
        second_resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": second_request_id, "X-Request-ID": second_request_id},
            json={"text": "再推荐几个", "request_id": second_request_id, "expected_revision": 1},
        )
        assert second_resp.status == 200
        second_events = _parse_sse(await second_resp.text())
        second_completed = second_events[-1][1]
        assert second_completed["session"]["revision"] == 2
        assert second_completed["session"]["title"] == "机器学习导师"


@pytest.mark.asyncio
async def test_new_turn_sse_title_generation_failure_uses_message_fallback():
    async def runtime_factory(*args, **kwargs):
        return FakeRuntime(title_error=True)

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        assert session["data"]["title"] is None
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000ac"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "  推荐\n南开大模型导师  ", "request_id": request_id, "expected_revision": 0},
        )
        assert resp.status == 200
        events = _parse_sse(await resp.text())
        completed = events[-1][1]
        assert completed["session"]["title"] == "推荐 南开大模型导师"

        listed = await (await client.get(
            "/api/v1/chat/sessions",
            headers=headers,
        )).json()
        assert listed["data"]["items"][0]["title"] == "推荐 南开大模型导师"


@pytest.mark.asyncio
async def test_new_turn_sse_recommendation_can_complete_without_delta(monkeypatch):
    import dext_recommend.application.services as services_module

    def no_delta_answer(result):
        return "", [], {}

    monkeypatch.setattr(services_module, "conversation_dispatch_to_answer", no_delta_answer)
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000ab"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 0},
        )
        assert resp.status == 200
        events = _parse_sse(await resp.text())
        assert [name for name, _ in events] == ["ack", "route", "completed"]
        _assert_sse_sequence(events)
        assert events[1][1]["route"] == "recommendation"
        assert events[1][1]["revision"] == 0
        assert events[2][1]["revision"] == 1
        assert events[2][1]["message"]["content"] == ""


@pytest.mark.asyncio
async def test_new_turn_sse_empty_recommendation_uses_empty_state_copy(caplog):
    async def runtime_factory(*args, **kwargs):
        runtime = FakeRuntime()
        runtime.conversation = EmptyRecommendationConversation()
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000b1"
        with caplog.at_level("INFO", logger="dext_recommend.application.services"):
            resp = await client.post(
                f"/api/v1/chat/sessions/{session_id}/turns",
                headers={**headers, "Idempotency-Key": request_id},
                json={"text": "推荐南开大模型导师", "request_id": request_id, "expected_revision": 0},
            )
            raw = await resp.text()
        assert resp.status == 200
        events = _parse_sse(raw)
        assert [name for name, _ in events] == ["ack", "route", "delta", "completed"]
        _assert_sse_sequence(events)
        completed = events[3][1]
        message = completed["message"]
        assert message["status"] == "done"
        assert message["kind"] == "recommendation"
        assert message["related_recommendations"] == []
        assert message["content"] != "已根据你的问题推荐了合适的导师。"
        assert "没有找到足够匹配的导师" in message["content"]
        assert "related_count=0" in caplog.text
        assert "warning_codes=no_candidates_after_filters" in caplog.text
        assert "recall_count=8" in caplog.text
        assert "post_filter_count=0" in caplog.text
        assert "returned_count=0" in caplog.text


@pytest.mark.asyncio
async def test_new_turn_sse_recommendation_error_warning_marks_attempt_failed():
    async def runtime_factory(*args, **kwargs):
        runtime = FakeRuntime()
        runtime.conversation = ErrorWarningRecommendationConversation()
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000b2"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "推荐导师", "request_id": request_id, "expected_revision": 0},
        )
        assert resp.status == 200
        events = _parse_sse(await resp.text())
        assert [name for name, _ in events] == ["ack", "route", "delta", "completed"]
        completed = events[3][1]
        message = completed["message"]
        assert message["status"] == "error"
        assert message["kind"] == "recommendation"
        assert message["related_recommendations"] == []
        assert message["content"] == "LLM failed"

        aggregate = await (await client.get(
            f"/api/v1/chat/sessions/{session_id}",
            headers=headers,
        )).json()
        assert aggregate["data"]["turns"][0]["status"] == "failed"
        assistant_messages = [
            item for item in aggregate["data"]["messages"]
            if item["role"] == "assistant"
        ]
        assert assistant_messages[0]["status"] == "error"


@pytest.mark.asyncio
async def test_new_turn_sse_dispatch_error_still_sends_ack_first():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=exploding_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000ac"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 0},
        )
        assert resp.status == 200
        events = _parse_sse(await resp.text())
        assert [name for name, _ in events] == ["ack", "error"]
        _assert_sse_sequence(events)
        ack = events[0][1]
        error = events[1][1]
        _assert_sse_context(
            error,
            session_id=session_id,
            turn_id=ack["turn_id"],
            attempt_id=ack["attempt_id"],
        )
        assert error["revision"] == 0
        assert error["code"] == "chat_stream_failed"
        assert error["message"] == "dispatch exploded"


@pytest.mark.asyncio
async def test_new_turn_sse_sends_comment_heartbeats_during_slow_dispatch():
    async def slow_runtime_factory(*args, **kwargs):
        runtime = FakeRuntime()
        runtime.conversation = SlowConversation()
        return runtime

    app = create_recommendation_app(
        AppSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            schema_bootstrap=True,
            sse_heartbeat_seconds=0.01,
        ),
        runtime_factory=slow_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000ae"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 0},
        )
        raw = await resp.text()
        assert ": heartbeat\n\n" in raw
        events = _parse_sse(raw)
        assert [name for name, _ in events] == ["ack", "route", "delta", "completed"]
        _assert_sse_sequence(events)


@pytest.mark.asyncio
async def test_new_turn_completed_idempotency_replay_does_not_dispatch_again():
    runtime = FakeRuntime()

    async def runtime_factory(*args, **kwargs):
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000af"
        payload = {"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 0}

        first = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json=payload,
        )
        assert [name for name, _ in _parse_sse(await first.text())] == [
            "ack", "route", "delta", "completed",
        ]

        second = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json=payload,
        )
        second_events = _parse_sse(await second.text())
        assert [name for name, _ in second_events] == ["ack", "route", "delta", "completed"]
        assert runtime.conversation.calls == 1

        aggregate = await (await client.get(
            f"/api/v1/chat/sessions/{session_id}",
            headers=headers,
        )).json()
        assistant_messages = [
            message for message in aggregate["data"]["messages"]
            if message["role"] == "assistant"
        ]
        assert len(assistant_messages) == 1


@pytest.mark.asyncio
async def test_new_turn_cancel_interrupts_registered_dispatch_task():
    hanging = HangingConversation()

    async def runtime_factory(*args, **kwargs):
        runtime = FakeRuntime()
        runtime.conversation = hanging
        return runtime

    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000b0"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 0},
        )
        ack_raw = await _read_raw_sse_event(resp)
        ack = _parse_sse(ack_raw)[0][1]
        await asyncio.wait_for(hanging.started.wait(), timeout=1)

        cancel = await client.post(
            f"/api/v1/chat/attempts/{ack['attempt_id']}/cancel",
            headers=headers,
        )
        assert cancel.status == 200
        tail = (await asyncio.wait_for(resp.content.read(), timeout=1)).decode("utf-8")
        assert "event: interrupted" in tail
        assert hanging.cancelled.is_set()

        aggregate = await (await client.get(
            f"/api/v1/chat/sessions/{session_id}",
            headers=headers,
        )).json()
        turn = aggregate["data"]["turns"][0]
        assert turn["status"] == "interrupted"


@pytest.mark.asyncio
async def test_new_turn_revision_conflict_is_json_error_before_sse_ack():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        session = await (await client.post("/api/v1/chat/sessions", headers=headers, json={})).json()
        session_id = session["data"]["id"]
        request_id = "00000000-0000-0000-0000-0000000000ad"
        resp = await client.post(
            f"/api/v1/chat/sessions/{session_id}/turns",
            headers={**headers, "Idempotency-Key": request_id},
            json={"text": "推荐机器学习导师", "request_id": request_id, "expected_revision": 99},
        )
        assert resp.status == 409
        assert not resp.headers["Content-Type"].startswith("text/event-stream")
        body = await resp.json()
        assert body["error_code"] == "conflict"
        assert body["message"] == "session revision conflict"


@pytest.mark.asyncio
async def test_quick_actions_route_uses_runtime_generation():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        resp = await client.post(
            "/api/v1/chat/quick-actions",
            headers=headers,
            json={
                "follow_up": "只看上海的导师",
                "last_recommendations": [{
                    "professor_id": "p1",
                    "name": "张老师",
                    "university": "测试大学",
                    "research_fields": ["机器学习"],
                }],
            },
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["quick_actions"] == ["动态筛选", "论文方向"]
        assert body["data"]["quick_actions"] != ["换城市", "只看985", "偏应用", "招生要求"]


@pytest.mark.asyncio
async def test_quick_actions_requires_follow_up_but_accepts_empty_string():
    app = create_recommendation_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=fake_runtime_factory,
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}
        missing = await client.post(
            "/api/v1/chat/quick-actions",
            headers=headers,
            json={"last_recommendations": []},
        )
        assert missing.status == 422

        empty = await client.post(
            "/api/v1/chat/quick-actions",
            headers=headers,
            json={"follow_up": "", "last_recommendations": []},
        )
        assert empty.status == 200
        body = await empty.json()
        assert body["data"]["quick_actions"] == ["开始推荐"]
