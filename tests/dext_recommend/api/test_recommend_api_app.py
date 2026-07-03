from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dext_recommend.api import AppSettings, create_recommendation_app
from dext_recommend.models import (
    ConversationDispatchResult,
    QueryDiagnostics,
    QueryUnderstanding,
    RecommendResponse,
    RecommendedProfessor,
)


class FakeCore:
    async def recommend(self, request, *, viewer_permissions=None):
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
                query_length=len(request.query_text),
                language_summary="zh",
                filter_summary=None,
            ),
            results=(
                RecommendedProfessor(
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
                ),
            ),
            suggested_followups=("了解招生要求",),
            warnings=(),
        )


class FakeConversation:
    async def dispatch(self, request, *, viewer_permissions=None):
        response = await FakeCore().recommend(request, viewer_permissions=viewer_permissions)
        return ConversationDispatchResult(
            kind="recommendation",
            context=request.conversation_context,
            recommendation=response,
            detail_followup=None,
            issues=(),
        )


class FakeQuickActions:
    def __init__(self):
        self.calls = []

    async def generate(self, follow_up, last_recommendations=None):
        self.calls.append({
            "follow_up": follow_up,
            "last_recommendations": list(last_recommendations or []),
        })
        return ["动态筛选", "论文方向"] if follow_up else ["开始推荐"]


class FakeRuntime:
    def __init__(self):
        self.core = FakeCore()
        self.conversation = FakeConversation()
        self.auxiliary_generation = SimpleNamespace()
        self.quick_actions = FakeQuickActions()
        self.readiness = SimpleNamespace(get_snapshot=lambda: None)
        self.closed = False

    async def aclose(self):
        self.closed = True


async def fake_runtime_factory(*args, **kwargs):
    return FakeRuntime()


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
        text = await resp.text()
        assert "event: ack" in text
        assert "event: route" in text
        assert "event: delta" in text
        assert "event: completed" in text
        assert '"session_id":' in text
        assert '"turn_id":' in text
        assert '"attempt_id":' in text
        assert '"message":' in text


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
