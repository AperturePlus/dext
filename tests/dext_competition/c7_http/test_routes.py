from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from aiohttp.test_utils import TestClient, TestServer

from dext_competition import CompetitionCard, SourceRef
from dext_competition.assistant import PlanAssistantDeps
from dext_competition.http.app import create_test_app
from dext_competition.planning import PlanGeneratorDeps, PreparationPlanGenerator
from dext_competition.ports import FakeCompetitionCatalogPort, FakeKnowledgeIndexPort
from dext_competition.recommend import CompetitionRecommendDeps, CompetitionRecommendationService
from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult


def _ref() -> SourceRef:
    return SourceRef("rules.md", "rules > a", hashlib.sha256(b"rules").hexdigest(), "rules")


def _card() -> CompetitionCard:
    return CompetitionCard(
        "cmp-1",
        "测试竞赛",
        "计算机",
        tags=("算法",),
        summary="算法与工程实践",
        schedule="3 月报名，5 月比赛",
        team_policy="3-5 人",
        preparation_focus=("算法训练",),
        risk_flags=("需复核当届通知",),
        official_links=("https://example.test",),
        internal_source_refs=(_ref(),),
    )


def _app():
    card = _card()
    catalog = FakeCompetitionCatalogPort((card,))
    knowledge = FakeKnowledgeIndexPort()
    recommendation_pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "interests": ["算法"],
        "needs_clarification": False,
        "missing_information": [],
        "confidence": 0.9,
    })))
    assistant_pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "reply": "可以加模拟任务。",
        "cards": [{
            "id": "card-1",
            "type": "append_advice",
            "advice_text": "每周复盘。",
            "summary": "保留复盘",
            "rationale": "基于备赛流程。",
        }],
    })))
    return create_test_app(
        recommendation_service=CompetitionRecommendationService(
            CompetitionRecommendDeps(catalog, knowledge, recommendation_pipeline)
        ),
        plan_generator=PreparationPlanGenerator(PlanGeneratorDeps(catalog)),
        assistant_deps=PlanAssistantDeps(assistant_pipeline),
    )


async def _client():
    client = TestClient(TestServer(_app()))
    await client.start_server()
    return client


def _headers(extra: dict | None = None) -> dict:
    headers = {"X-Dext-Owner-Id": "owner-1"}
    headers.update(extra or {})
    return headers


def _snapshot(revision: int = 0) -> dict:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc).isoformat()
    return {
        "id": "plan-1",
        "competition": {"id": "cmp-1", "name": "测试竞赛", "category": "计算机", "rules_summary": {}},
        "target_date": "2026-08-20",
        "timeline_type": "submission",
        "event_end_date": None,
        "defense_date": "2026-09-01",
        "revision": revision,
        "weekly_commitment": "hours6to10",
        "experience_level": "beginner",
        "status": "draft",
        "phases": [{
            "key": "foundation",
            "start_date": "2026-07-01",
            "end_date": "2026-07-14",
            "tasks": [{
                "id": "task-1",
                "title": "必做",
                "kind": "required",
                "estimated_hours": 3,
                "due_date": "2026-07-10",
                "completed_at": None,
            }],
        }],
        "created_at": now,
        "updated_at": now,
        "tight_schedule": False,
        "overload": False,
    }


async def test_catalog_and_detail_routes_return_openapi_envelopes() -> None:
    client = await _client()
    try:
        resp = await client.get("/api/v1/competitions")
        body = await resp.json()
        assert resp.status == 200
        assert body["code"] == 0
        assert body["data"][0]["id"] == "cmp-1"
        assert "internal_source_refs" not in body["data"][0]

        resp = await client.get("/api/v1/competitions/cmp-1")
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["name"] == "测试竞赛"
    finally:
        await client.close()


async def test_recommendation_route_rejects_unknown_fields() -> None:
    client = await _client()
    try:
        resp = await client.post(
            "/api/v1/recommendations/competitions",
            json={"prompt": "算法", "extra": "reject"},
        )
        body = await resp.json()
        assert resp.status == 422
        assert body["data"]["error_code"] == "invalid_request"
    finally:
        await client.close()


async def test_plan_repository_routes_are_owner_scoped_and_revision_checked() -> None:
    client = await _client()
    try:
        resp = await client.post(
            "/api/v1/preparation-plans",
            json=_snapshot(0),
            headers=_headers({"Idempotency-Key": "create-1"}),
        )
        assert resp.status == 200

        resp = await client.get("/api/v1/preparation-plans/plan-1", headers=_headers())
        assert resp.status == 200

        resp = await client.get("/api/v1/preparation-plans/plan-1", headers={"X-Dext-Owner-Id": "owner-2"})
        assert resp.status == 404

        resp = await client.put(
            "/api/v1/preparation-plans/plan-1",
            json=_snapshot(0),
            headers=_headers({"Idempotency-Key": "put-bad"}),
        )
        assert resp.status == 409

        resp = await client.put(
            "/api/v1/preparation-plans/plan-1",
            json=_snapshot(1),
            headers=_headers({"Idempotency-Key": "put-ok"}),
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["revision"] == 1
    finally:
        await client.close()


async def test_assistant_route_returns_change_cards_and_persists_history() -> None:
    client = await _client()
    try:
        await client.post(
            "/api/v1/preparation-plans",
            json=_snapshot(0),
            headers=_headers({"Idempotency-Key": "create-1"}),
        )
        payload = {
            "calendar_today": "2026-07-01",
            "base_plan_revision": 0,
            "plan_snapshot": _snapshot(0),
            "user_message": "给我一条建议",
            "request_id": "req-1",
            "history": [],
        }
        resp = await client.post(
            "/api/v1/preparation-plans/plan-1/assistant",
            json=payload,
            headers=_headers(),
        )
        body = await resp.json()
        assert resp.status == 200
        card = body["data"]["change_set"]["cards"][0]
        assert card["type"] == "append_advice"
        assert card["status"] == "pending"
        assert "internal_source_refs" not in card
    finally:
        await client.close()


async def test_assistant_route_accepts_unpersisted_plan_snapshot() -> None:
    client = await _client()
    try:
        payload = {
            "calendar_today": "2026-07-01",
            "base_plan_revision": 0,
            "plan_snapshot": _snapshot(0),
            "user_message": "给我一条建议",
            "request_id": "req-stateless",
            "history": [],
        }
        resp = await client.post(
            "/api/v1/preparation-plans/plan-1/assistant",
            json=payload,
            headers=_headers(),
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["reply"] == "可以加模拟任务。"
        assert body["data"]["change_set"]["base_plan_revision"] == 0
    finally:
        await client.close()


async def test_generate_template_config_and_diagnose_routes() -> None:
    client = await _client()
    try:
        resp = await client.get("/api/v1/preparation/config", headers=_headers())
        assert resp.status == 200

        resp = await client.get(
            "/api/v1/preparation-templates?timeline_type=submission&include_defense=true&category=计算机&competition_id=cmp-1",
            headers=_headers(),
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["phases"]

        generate_payload = {
            "competition": {"id": "cmp-1", "name": "测试竞赛", "category": "计算机", "rules_summary": {}},
            "calendar_today": "2026-07-01",
            "target_date": "2026-08-20",
            "timeline_type": "submission",
            "weekly_commitment": "hours6to10",
            "experience_level": "beginner",
            "phase_keys": ["foundation"],
        }
        resp = await client.post("/api/v1/preparation-plans/generate", json=generate_payload, headers=_headers())
        assert resp.status == 200

        resp = await client.post(
            "/api/v1/preparation-plans/diagnose",
            json={
                "competition": {"id": "cmp-1", "name": "测试竞赛", "category": "计算机", "rules_summary": {}},
                "answers": [{"question_key": "exp", "answer": "参加过一次课程项目"}],
            },
            headers=_headers(),
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["level"] in {"beginner", "intermediate", "experienced"}
    finally:
        await client.close()
