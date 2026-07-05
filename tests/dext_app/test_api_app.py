from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dext_app.api import create_app
from dext_app.api.app import _new_competition_generation_pipeline
from dext_competition import CompetitionCard, SourceRef
from dext_competition.assistant import PlanAssistantDeps
from dext_competition.http.app import CompetitionHttpDeps
from dext_competition.planning import PlanGeneratorDeps, PreparationPlanGenerator
from dext_competition.ports import FakeCompetitionCatalogPort, FakeKnowledgeIndexPort
from dext_competition.recommend import CompetitionRecommendDeps, CompetitionRecommendationService
from dext_competition.repositories import (
    InMemoryPreparationAssistantHistoryRepository,
    InMemoryPreparationPlanRepository,
)
from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult
from dext_recommend.api import AppSettings
from dext_recommend.config import RecommendSettings


class _FakeRuntime:
    def __init__(self):
        self.readiness = SimpleNamespace(get_snapshot=lambda: None)
        self.closed = False

    async def aclose(self):
        self.closed = True


async def _runtime_factory(*args, **kwargs):
    return _FakeRuntime()


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


def _competition_deps() -> CompetitionHttpDeps:
    catalog = FakeCompetitionCatalogPort((_card(),))
    knowledge = FakeKnowledgeIndexPort()
    pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "interests": ["算法"],
        "needs_clarification": False,
        "missing_information": [],
        "confidence": 0.9,
    })))
    return CompetitionHttpDeps(
        recommendation_service=CompetitionRecommendationService(
            CompetitionRecommendDeps(catalog, knowledge, pipeline)
        ),
        plan_generator=PreparationPlanGenerator(PlanGeneratorDeps(catalog, pipeline)),
        assistant_deps=PlanAssistantDeps(pipeline),
        plan_repository=InMemoryPreparationPlanRepository(),
        assistant_history_repository=InMemoryPreparationAssistantHistoryRepository(),
    )


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


@pytest.mark.asyncio
async def test_combined_app_uses_recommend_auth_for_competition_owner() -> None:
    app = create_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=_runtime_factory,
        competition_deps=_competition_deps(),
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/v1/preparation/config")
        body = await resp.json()
        assert resp.status == 401
        assert body["error_code"] == "unauthorized"

        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}

        resp = await client.get("/api/v1/preparation/config", headers=headers)
        assert resp.status == 200

        resp = await client.post(
            "/api/v1/preparation-plans",
            headers={**headers, "Idempotency-Key": "create-1"},
            json=_snapshot(0),
        )
        assert resp.status == 200

        resp = await client.get("/api/v1/preparation-plans", headers=headers)
        body = await resp.json()
        assert resp.status == 200
        assert body["data"][0]["id"] == "plan-1"

        resp = await client.get("/api/v1/competitions")
        body = await resp.json()
        assert resp.status == 200
        assert body["data"][0]["id"] == "cmp-1"


def test_live_competition_deps_injects_llm_pipeline_when_configured() -> None:
    pipeline = _new_competition_generation_pipeline(
        RecommendSettings(
            llm_api_key="test-key",
            llm_base_url="https://llm.test/v1",
            llm_model="test-model",
            _env_file=None,
        )
    )
    assert pipeline is not None
