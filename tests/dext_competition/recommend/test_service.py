from __future__ import annotations

import hashlib

from dext_competition import (
    CatalogEvidenceStatus,
    CompetitionCard,
    CompetitionFieldEvidence,
    CompetitionPreferences,
    CompetitionRecommendRequest,
)
from dext_competition.contracts.knowledge import Chunk
from dext_competition.ports import FakeCompetitionCatalogPort, FakeKnowledgeIndexPort
from dext_competition.recommend import (
    CompetitionRecommendDeps,
    CompetitionRecommendationService,
)
from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult


def _chunk(text: str, doc: str = "竞赛信息总览.md") -> Chunk:
    return Chunk(
        doc_path=doc,
        heading_path=f"{doc} > 赛事",
        chunk_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        source_links=("https://example.edu/rules",),
        last_verified="2026-06-30",
    )


def _card(
    name: str,
    *,
    category: str,
    chunk: Chunk,
    team_policy: str = "team",
    in_2024_catalog: bool = True,
    summary: str = "算法与工程实践",
) -> CompetitionCard:
    ref = chunk.to_source_ref()
    evidence = (
        CompetitionFieldEvidence("display_name", (name,), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("category", (category,), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("tags", ("算法",), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("summary", (summary,), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("eligibility", ("本科生可参加",), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("schedule", ("通常上半年启动",), CatalogEvidenceStatus.UNCERTAIN, (ref,), "需复核当届通知"),
        CompetitionFieldEvidence("team_policy", (team_policy,), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("materials", ("作品",), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("ai_compliance", ("按赛事规则披露 AI 使用",), CatalogEvidenceStatus.UNCERTAIN, (ref,), "需复核当届通知"),
        CompetitionFieldEvidence("preparation_focus", ("算法训练",), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("risk_flags", ("学校认定需另查本校文件",) if not in_2024_catalog else (), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("official_links", ("https://example.edu/rules",), CatalogEvidenceStatus.GROUNDED, (ref,)),
        CompetitionFieldEvidence("in_2024_catalog", (str(in_2024_catalog).lower(),), CatalogEvidenceStatus.GROUNDED, (ref,)),
    )
    return CompetitionCard(
        competition_id="cmp_" + hashlib.sha1(name.encode("utf-8")).hexdigest(),
        display_name=name,
        category=category,
        tags=("算法",),
        summary=summary,
        eligibility="本科生可参加",
        schedule="通常上半年启动",
        team_policy=team_policy,
        materials=("作品",),
        ai_compliance="按赛事规则披露 AI 使用",
        preparation_focus=("算法训练",),
        risk_flags=("学校认定需另查本校文件",) if not in_2024_catalog else (),
        official_links=("https://example.edu/rules",),
        internal_source_refs=(ref,),
        in_2024_catalog=in_2024_catalog,
        last_verified="2026-06-30",
        field_evidence=evidence,
    )


def _service(cards: tuple[CompetitionCard, ...], chunks: tuple[Chunk, ...],
             pipeline: ConstrainedGenerationPipeline | None = None):
    catalog = FakeCompetitionCatalogPort(cards)
    knowledge = FakeKnowledgeIndexPort(chunks)
    service = CompetitionRecommendationService(
        CompetitionRecommendDeps(
            catalog_port=catalog,
            knowledge_index=knowledge,
            generation_pipeline=pipeline,
        )
    )
    return service, catalog, knowledge


async def test_invalid_request_returns_warning_without_recall():
    c = _chunk("算法竞赛")
    card = _card("算法竞赛", category="计算机", chunk=c)
    service, catalog, knowledge = _service((card,), (c,))
    response = await service.recommend(
        CompetitionRecommendRequest(
            query_text="算法",
            preferences=CompetitionPreferences(categories=("不存在",)),
        )
    )
    assert response.results == ()
    assert response.warnings[0].code == "invalid_request"
    assert catalog.calls == []
    assert knowledge.calls == []


async def test_needs_clarification_from_pipeline_does_not_recall():
    c = _chunk("算法竞赛")
    card = _card("算法竞赛", category="计算机", chunk=c)
    pipeline = ConstrainedGenerationPipeline(
        FakeLLMGenerationPort(
            GenerationResult(
                output={
                    "interests": [],
                    "needs_clarification": True,
                    "missing_information": ["target_goal"],
                    "confidence": 0.2,
                }
            )
        )
    )
    service, catalog, knowledge = _service((card,), (c,), pipeline)
    response = await service.recommend(
        CompetitionRecommendRequest(
            query_text="想参赛",
            preferences=CompetitionPreferences(categories=("计算机",)),
        )
    )
    assert response.results == ()
    assert response.query_understanding is not None
    assert response.query_understanding.needs_clarification is True
    assert response.warnings[0].code == "needs_clarification"
    assert catalog.calls == []
    assert knowledge.calls == []


async def test_missing_generation_pipeline_uses_heuristic_understanding():
    c = _chunk("算法竞赛")
    card = _card("算法竞赛", category="计算机", chunk=c)
    service, catalog, knowledge = _service((card,), (c,))
    response = await service.recommend(
        CompetitionRecommendRequest(
            query_text="算法竞赛",
            preferences=CompetitionPreferences(categories=("计算机",)),
        )
    )
    assert [item.display_name for item in response.results] == ["算法竞赛"]
    assert response.generation_profile_version == "competition.query-understanding.heuristic-v1"
    assert response.warnings == ()
    assert catalog.calls == [{"op": "list_competitions"}]
    assert knowledge.calls and knowledge.calls[0]["op"] == "query"


async def test_service_filters_ranks_and_preserves_internal_source_refs():
    c1 = _chunk("算法 程序设计 团队")
    c2 = _chunk("数学建模 个人")
    computer = _card("程序设计竞赛", category="计算机", chunk=c1, team_policy="team")
    math = _card("数学建模竞赛", category="数学建模", chunk=c2, team_policy="solo")
    pipeline = ConstrainedGenerationPipeline(FakeLLMGenerationPort(GenerationResult(output={
        "interests": ["算法", "程序设计"],
        "needs_clarification": False,
        "missing_information": [],
        "confidence": 0.9,
    })))
    service, catalog, knowledge = _service((math, computer), (c1, c2), pipeline)
    response = await service.recommend(
        CompetitionRecommendRequest(
            query_text="算法和程序设计",
            preferences=CompetitionPreferences(
                categories=("计算机",),
                team_preference="team",
                weekly_hours=8,
                target_goal="portfolio",
            ),
            limit=3,
            diagnostics_level="debug",
        )
    )
    assert [item.display_name for item in response.results] == ["程序设计竞赛"]
    result = response.results[0]
    assert result.internal_source_refs
    assert result.evidence_status in {"grounded", "partial"}
    assert "教育部白名单" not in " ".join(result.short_reasons)
    assert catalog.calls == [{"op": "list_competitions"}]
    assert knowledge.calls and knowledge.calls[0]["op"] == "query"
