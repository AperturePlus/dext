from __future__ import annotations

from pathlib import Path

from dext_competition import CompetitionPreferences, CompetitionRecommendRequest
from dext_competition.catalog import FileCompetitionCatalog, build_catalog
from dext_competition.index import FileKnowledgeIndex, build_index
from dext_competition.recommend import (
    CompetitionRecommendDeps,
    CompetitionRecommendationService,
)
from dext_grounded import ConstrainedGenerationPipeline, FakeLLMGenerationPort, GenerationResult


CORPUS_ROOT = Path(__file__).resolve().parents[3] / "data" / "竞赛助手"


async def test_real_corpus_recommendation_smoke(tmp_path: Path):
    index_dir = tmp_path / "index"
    catalog_dir = tmp_path / "catalog"
    build_index(CORPUS_ROOT, index_dir)
    knowledge = FileKnowledgeIndex(index_dir)
    await build_catalog(knowledge, catalog_dir)
    catalog = FileCompetitionCatalog(
        catalog_dir,
        expected_knowledge_base_version=knowledge.manifest().version_id,
    )
    service = CompetitionRecommendationService(
        CompetitionRecommendDeps(
            catalog_port=catalog,
            knowledge_index=knowledge,
            generation_pipeline=ConstrainedGenerationPipeline(FakeLLMGenerationPort(
                GenerationResult(output={
                    "interests": ["计算机", "算法", "程序设计"],
                    "major_fit": "计算机",
                    "weekly_hours": 8,
                    "needs_clarification": False,
                    "missing_information": [],
                    "confidence": 0.9,
                })
            )),
        )
    )

    response = await service.recommend(
        CompetitionRecommendRequest(
            query_text="我想找适合计算机专业的算法和程序设计竞赛",
            preferences=CompetitionPreferences(
                categories=("计算机",),
                major="计算机",
                weekly_hours=8,
                target_goal="portfolio",
                team_preference="team",
            ),
            limit=5,
            diagnostics_level="debug",
        )
    )

    assert response.knowledge_base_version == knowledge.manifest().version_id
    assert response.competition_ranking_profile_version == "competition.ranking.v1"
    assert response.results
    assert {result.category for result in response.results} == {"计算机"}
    assert all(result.internal_source_refs for result in response.results)
