from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import dext_app.api.app as api_app
import dext_app.api.cli as api_cli
from dext_app.api import create_app
from dext_app.api.app import _new_competition_generation_pipeline
from dext_competition import CompetitionCard, SourceRef
from dext_competition.assistant import PlanAssistantDeps
from dext_competition.catalog import CatalogArtifactError
from dext_competition.config import CompetitionSettings
from dext_competition.http.app import CompetitionHttpDeps
from dext_competition.index import IndexArtifactError
from dext_competition.planning import PlanGeneratorDeps, PreparationPlanGenerator
from dext_competition.ports import FakeCompetitionCatalogPort, FakeKnowledgeIndexPort
from dext_competition.recommend import CompetitionRecommendDeps, CompetitionRecommendationService
from dext_competition.repositories import (
    InMemoryPreparationAssistantHistoryRepository,
    InMemoryPreparationPlanRepository,
)
from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult
from dext_recommend.api import AppSettings
from dext_recommend.models import (
    QueryDiagnostics,
    QueryUnderstanding,
    RecommendResponse,
    RecommendedProfessor,
)


class _ArtifactRepo:
    def __init__(self, version_id: str):
        self._manifest = SimpleNamespace(version_id=version_id)

    def manifest(self):
        return self._manifest


def _competition_settings(tmp_path) -> CompetitionSettings:
    return CompetitionSettings(
        knowledge_source_root=str(tmp_path / "source"),
        index_artifact_dir=str(tmp_path / "index"),
        catalog_artifact_dir=str(tmp_path / "catalog"),
    )


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


def _recommend_response(query_length: int = 4) -> RecommendResponse:
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
            recall_count=1,
            post_filter_count=1,
            returned_count=1,
            steps_used=1,
        ),
        results=(_recommended_professor(),),
        suggested_followups=("了解招生要求",),
        warnings=(),
    )


class _FakeCore:
    async def recommend(self, request, *, viewer_permissions=None):
        return _recommend_response(query_length=len(request.query_text))


class _FakeRuntime:
    def __init__(self):
        self.core = _FakeCore()
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
async def test_prepare_competition_artifacts_builds_missing_index(monkeypatch, tmp_path) -> None:
    settings = _competition_settings(tmp_path)
    calls: list[tuple] = []
    state = {"index_ready": False}

    def fake_verify_index(path):
        calls.append(("verify_index", path))
        if not state["index_ready"]:
            raise IndexArtifactError("missing index artifact: manifest.json")
        return _ArtifactRepo("kb-v1")

    def fake_build_index(source_root, output_dir):
        calls.append(("build_index", source_root, output_dir))
        state["index_ready"] = True

    def fake_verify_catalog(path, *, expected_knowledge_base_version):
        calls.append(("verify_catalog", path, expected_knowledge_base_version))
        return _ArtifactRepo("catalog-v1")

    async def fake_build_catalog(index, output_dir):
        calls.append(("build_catalog", index, output_dir))

    monkeypatch.setattr(api_app, "verify_index", fake_verify_index)
    monkeypatch.setattr(api_app, "build_index", fake_build_index)
    monkeypatch.setattr(api_app, "verify_catalog", fake_verify_catalog)
    monkeypatch.setattr(api_app, "build_catalog", fake_build_catalog)

    result = await api_app.prepare_competition_artifacts(settings)

    assert result.index_built is True
    assert result.catalog_built is False
    assert ("build_index", settings.knowledge_source_root, settings.index_artifact_dir) in calls
    assert not any(call[0] == "build_catalog" for call in calls)


@pytest.mark.asyncio
async def test_prepare_competition_artifacts_builds_stale_catalog(monkeypatch, tmp_path) -> None:
    settings = _competition_settings(tmp_path)
    calls: list[tuple] = []
    state = {"catalog_ready": False}
    knowledge = _ArtifactRepo("kb-v1")

    def fake_verify_index(path):
        calls.append(("verify_index", path))
        return knowledge

    def fake_build_index(source_root, output_dir):
        calls.append(("build_index", source_root, output_dir))

    def fake_verify_catalog(path, *, expected_knowledge_base_version):
        calls.append(("verify_catalog", path, expected_knowledge_base_version))
        if not state["catalog_ready"]:
            raise CatalogArtifactError("knowledge base version mismatch")
        return _ArtifactRepo("catalog-v1")

    async def fake_build_catalog(index, output_dir):
        calls.append(("build_catalog", index, output_dir))
        state["catalog_ready"] = True

    monkeypatch.setattr(api_app, "verify_index", fake_verify_index)
    monkeypatch.setattr(api_app, "build_index", fake_build_index)
    monkeypatch.setattr(api_app, "verify_catalog", fake_verify_catalog)
    monkeypatch.setattr(api_app, "build_catalog", fake_build_catalog)

    result = await api_app.prepare_competition_artifacts(settings)

    assert result.index_built is False
    assert result.catalog_built is True
    assert ("build_catalog", knowledge, settings.catalog_artifact_dir) in calls
    assert not any(call[0] == "build_index" for call in calls)


@pytest.mark.asyncio
async def test_prepare_competition_artifacts_does_not_rebuild_ready_artifacts(
    monkeypatch, tmp_path,
) -> None:
    settings = _competition_settings(tmp_path)
    calls: list[tuple] = []

    def fake_verify_index(path):
        calls.append(("verify_index", path))
        return _ArtifactRepo("kb-v1")

    def fake_verify_catalog(path, *, expected_knowledge_base_version):
        calls.append(("verify_catalog", path, expected_knowledge_base_version))
        return _ArtifactRepo("catalog-v1")

    def fail_build_index(source_root, output_dir):
        raise AssertionError("index should not be rebuilt")

    async def fail_build_catalog(index, output_dir):
        raise AssertionError("catalog should not be rebuilt")

    monkeypatch.setattr(api_app, "verify_index", fake_verify_index)
    monkeypatch.setattr(api_app, "build_index", fail_build_index)
    monkeypatch.setattr(api_app, "verify_catalog", fake_verify_catalog)
    monkeypatch.setattr(api_app, "build_catalog", fail_build_catalog)

    result = await api_app.prepare_competition_artifacts(settings)

    assert result.index_built is False
    assert result.catalog_built is False
    assert calls == [
        ("verify_index", settings.index_artifact_dir),
        ("verify_catalog", settings.catalog_artifact_dir, "kb-v1"),
    ]


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
@pytest.mark.asyncio
async def test_combined_app_routes_mentor_and_competition_recommendations() -> None:
    app = create_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=_runtime_factory,
        competition_deps=_competition_deps(),
    )
    async with TestClient(TestServer(app)) as client:
        identity = await (await client.post("/api/v1/identity/anonymous")).json()
        headers = {"Authorization": f"Bearer {identity['data']['access_token']}"}

        resp = await client.post(
            "/api/v1/recommendations/mentors",
            headers=headers,
            json={"prompt": "机器学习导师"},
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["recommendations"][0]["professor_id"] == "p1"

        resp = await client.post(
            "/api/v1/recommendations/competitions",
            json={"prompt": "算法竞赛"},
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["recommendations"][0]["id"] == "cmp-1"


@pytest.mark.asyncio
async def test_combined_app_competition_recommendation_uses_heuristic_fallback() -> None:
    card = _card()
    catalog = FakeCompetitionCatalogPort((card,))
    knowledge = FakeKnowledgeIndexPort()
    competition_deps = CompetitionHttpDeps(
        recommendation_service=CompetitionRecommendationService(
            CompetitionRecommendDeps(catalog, knowledge)
        ),
        plan_generator=PreparationPlanGenerator(PlanGeneratorDeps(catalog)),
        assistant_deps=PlanAssistantDeps(None),
        plan_repository=InMemoryPreparationPlanRepository(),
        assistant_history_repository=InMemoryPreparationAssistantHistoryRepository(),
    )
    app = create_app(
        AppSettings(database_url="sqlite+aiosqlite:///:memory:", schema_bootstrap=True),
        runtime_factory=_runtime_factory,
        competition_deps=competition_deps,
    )
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/v1/recommendations/competitions",
            json={"prompt": "推荐适合零基础组队准备的数学建模竞赛。"},
        )
        body = await resp.json()
        assert resp.status == 200
        assert body["data"]["recommendations"][0]["id"] == "cmp-1"
        assert body["data"]["warnings"][0]["code"] == "generation_unavailable"


def test_dext_api_serve_prepares_competition_artifacts_before_running_app(monkeypatch) -> None:
    events: list[object] = []

    async def fake_prepare():
        events.append("prepare")

    def fake_create_app(settings):
        events.append(("create_app", settings.http_port))
        return object()

    def fake_run_app(app, *, host, port, print, access_log, access_log_class):
        events.append(("run_app", host, port, access_log is None, access_log_class.__name__))

    monkeypatch.setattr(api_cli, "prepare_competition_artifacts", fake_prepare)
    monkeypatch.setattr(api_cli, "create_app", fake_create_app)
    monkeypatch.setattr(api_cli.web, "run_app", fake_run_app)

    api_cli.main(["serve", "--port", "21540", "--no-access-log"])

    assert events == [
        "prepare",
        ("create_app", 21540),
        ("run_app", "127.0.0.1", 21540, True, "RecommendAccessLogger"),
    ]
