from __future__ import annotations

import datetime
import hashlib

import pytest

from dext_competition import (
    Chunk,
    CompetitionCard,
    CompetitionCatalogManifest,
    SourceRef,
)
from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionFieldEvidence,
)
from dext_competition.ports import FakeCompetitionCatalogPort, FakeKnowledgeIndexPort
from dext_competition.qa import FRESHNESS_NOTICE, answer_competition_question
from dext_grounded import (
    Claim,
    ConstrainedGenerationPipeline,
    ContentClass,
    FakeLLMGenerationPort,
    GenerationResult,
)


def _ref(name: str = "a") -> SourceRef:
    return SourceRef(
        doc_path=f"{name}.md",
        heading_path=f"{name} > rules",
        chunk_hash=name,
        quote_or_summary=f"{name} rules",
        official_url=f"https://example.edu/{name}",
        last_verified="2026-06-30",
    )


def _field(field: str, value: str, ref: SourceRef) -> CompetitionFieldEvidence:
    return CompetitionFieldEvidence(
        field=field,
        values=(value,),
        status=CatalogEvidenceStatus.GROUNDED,
        source_refs=(ref,),
    )


def _card() -> CompetitionCard:
    ref = _ref("card")
    return CompetitionCard(
        competition_id="cmp_test",
        display_name="测试竞赛",
        category="计算机",
        tags=("算法",),
        summary="规则摘要",
        eligibility="本科生可参加",
        schedule="通常春季报名",
        team_policy="team",
        materials=("报名表",),
        ai_compliance="按当届规则披露",
        preparation_focus=("算法基础",),
        risk_flags=(),
        official_links=("https://example.edu/rules",),
        internal_source_refs=(ref,),
        in_2024_catalog=True,
        field_evidence=(
            _field("summary", "规则摘要", ref),
            _field("eligibility", "本科生可参加", ref),
            _field("schedule", "通常春季报名", ref),
            _field("team_policy", "team", ref),
        ),
    )


def _manifest() -> CompetitionCatalogManifest:
    return CompetitionCatalogManifest(
        version_id="catalog-v1",
        knowledge_base_version="kb-v1",
        rules_version="catalog-rules-v1",
        card_count=1,
        in_2024_catalog_count=1,
        content_hash="hash",
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )


def _chunk(text: str = "本科生可参加测试竞赛") -> Chunk:
    return Chunk(
        "rules.md",
        "rules > eligibility",
        hashlib.sha256(text.encode()).hexdigest(),
        text,
        ("https://example.edu/rules",),
        "2026-06-30",
    )


def _ports(raw: GenerationResult):
    catalog = FakeCompetitionCatalogPort((_card(),), manifest=_manifest())
    index = FakeKnowledgeIndexPort((_chunk(),))
    llm = FakeLLMGenerationPort(raw)
    pipeline = ConstrainedGenerationPipeline(llm)
    return catalog, index, llm, pipeline


@pytest.mark.asyncio
async def test_answer_uses_pipeline_with_catalog_and_index_facts():
    ref = _card().internal_source_refs[0]
    raw = GenerationResult(
        output={
            "answer": "测试竞赛本科生可参加。",
            "claims": [{
                "text": "测试竞赛本科生可参加。",
                "content_class": "fact",
                "fact_indices": [1],
            }],
        },
        claims=(Claim(
            text="测试竞赛本科生可参加。",
            content_class=ContentClass.FACT,
            fact_refs=(ref,),
        ),),
    )
    catalog, index, llm, pipeline = _ports(raw)

    answer = await answer_competition_question(
        question="本科生能参加吗？",
        competition_id="cmp_test",
        catalog=catalog,
        index=index,
        pipeline=pipeline,
    )

    assert answer.answer == "测试竞赛本科生可参加。"
    assert answer.evidence_status == "grounded"
    assert answer.internal_source_refs == (ref,)
    assert llm.calls[0]["system_prompt_id"] == "dext_competition.qa.answer.v1"
    assert llm.calls[0]["generation_profile_version"] == "competition.qa.v1"
    assert llm.calls[0]["fact_bundle"].subject_id == "cmp_test"
    assert catalog.calls[0] == {"op": "get", "competition_id": "cmp_test"}
    assert index.calls[0]["op"] == "query"


@pytest.mark.asyncio
async def test_answer_attaches_refs_from_support_map_indices():
    raw = GenerationResult(
        output={
            "answer": "测试竞赛本科生可参加。",
            "claims": [{
                "text": "测试竞赛本科生可参加。",
                "content_class": "fact",
                "fact_indices": [0],
            }],
        },
        claims=(Claim(
            text="测试竞赛本科生可参加。",
            content_class=ContentClass.FACT,
            fact_refs=(),
        ),),
    )
    catalog, index, _llm, pipeline = _ports(raw)

    answer = await answer_competition_question(
        question="本科生能参加吗？",
        competition_id="cmp_test",
        catalog=catalog,
        index=index,
        pipeline=pipeline,
    )

    assert answer.evidence_status == "grounded"
    assert answer.internal_source_refs == (_card().internal_source_refs[0],)


@pytest.mark.asyncio
async def test_answer_downgrades_fact_without_source_refs_to_uncertain():
    raw = GenerationResult(
        output={
            "answer": "没有引用的断言。",
            "claims": [{
                "text": "没有引用的断言。",
                "content_class": "fact",
                "fact_indices": [],
            }],
        },
        claims=(Claim(
            text="没有引用的断言。",
            content_class=ContentClass.FACT,
            fact_refs=(),
        ),),
    )
    catalog, index, _llm, pipeline = _ports(raw)

    answer = await answer_competition_question(
        question="给我一个规则结论",
        competition_id="cmp_test",
        catalog=catalog,
        index=index,
        pipeline=pipeline,
    )

    assert answer.evidence_status == "uncertain"
    assert answer.internal_source_refs == ()


@pytest.mark.asyncio
async def test_answer_adds_freshness_notice_for_stale_or_time_questions():
    ref = _card().internal_source_refs[0]
    raw = GenerationResult(
        output={
            "answer": "2024 年报名在春季。",
            "claims": [{
                "text": "2024 年报名在春季。",
                "content_class": "fact",
                "fact_indices": [0],
            }],
        },
        claims=(Claim(
            text="2024 年报名在春季。",
            content_class=ContentClass.FACT,
            fact_refs=(ref,),
        ),),
    )
    catalog, index, _llm, pipeline = _ports(raw)

    answer = await answer_competition_question(
        question="报名时间是什么时候？",
        competition_id="cmp_test",
        catalog=catalog,
        index=index,
        pipeline=pipeline,
    )

    assert answer.evidence_status == "partial"
    assert answer.freshness_notice == FRESHNESS_NOTICE
    assert "2024 年报名[uncertain: 往届信息]" in answer.answer


@pytest.mark.asyncio
async def test_answer_without_evidence_does_not_call_llm():
    catalog = FakeCompetitionCatalogPort((), manifest=_manifest())
    index = FakeKnowledgeIndexPort(())
    raw = GenerationResult(output={"answer": "should not run", "claims": []})
    llm = FakeLLMGenerationPort(raw)

    answer = await answer_competition_question(
        question="不存在的规则？",
        catalog=catalog,
        index=index,
        pipeline=ConstrainedGenerationPipeline(llm),
    )

    assert answer.evidence_status == "uncertain"
    assert answer.internal_source_refs == ()
    assert llm.calls == []

