from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dext_recommend.api import AppSettings, create_recommendation_app
from dext_recommend.models import (
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


class FakeRuntime:
    def __init__(self):
        self.core = FakeCore()
        self.conversation = SimpleNamespace()
        self.auxiliary_generation = SimpleNamespace()
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
            json={"degree_stage": "本科", "score": {"gpa_bucket": "high"}},
        )
        assert resp.status == 200
        assert (await resp.json())["data"]["score"] == {"gpa_bucket": "high"}

        resp = await client.put(
            "/api/v1/favorites/p1",
            headers=headers,
            json={"professor_id": "p1", "snapshot": {"name": "张老师"}},
        )
        assert resp.status == 200
        resp = await client.get("/api/v1/favorites", headers=headers)
        assert (await resp.json())["data"]["items"][0]["professor_id"] == "p1"

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
        assert (await resp.json())["data"]["items"][0]["prompt"] == "机器学习"


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
            json={"prompt": "想找机器学习导师", "profile": {"score": {"rank_bucket": "top10"}}},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["data"]["recommendations"][0]["professor_id"] == "p1"


def test_public_profile_rejects_raw_score_fields():
    from pydantic import ValidationError

    from dext_recommend.api.schemas import UserProfile

    with pytest.raises(ValidationError):
        UserProfile.model_validate({"score": {"gpa": 3.9, "scale": 4.0}})
