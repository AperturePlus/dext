from __future__ import annotations

import datetime

import pytest

from dext_competition import CompetitionCard, CompetitionCatalogManifest, SourceRef
from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionFieldEvidence,
)
from dext_competition.ports import FakeCompetitionCatalogPort
from dext_competition.qa import compare_competitions
from dext_grounded import (
    Claim,
    ConstrainedGenerationPipeline,
    ContentClass,
    FakeLLMGenerationPort,
    GenerationResult,
)


def _ref(name: str) -> SourceRef:
    return SourceRef(
        doc_path=f"{name}.md",
        heading_path=name,
        chunk_hash=name,
        quote_or_summary=name,
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


def _card(idx: int, category: str = "计算机") -> CompetitionCard:
    ref = _ref(f"cmp_{idx}")
    return CompetitionCard(
        competition_id=f"cmp_{idx}",
        display_name=f"竞赛 {idx}",
        category=category,
        tags=("实践",),
        summary=f"摘要 {idx}",
        eligibility="本科生",
        schedule=f"窗口 {idx}",
        team_policy="team",
        materials=("作品",),
        ai_compliance="按当届规则披露",
        preparation_focus=(f"重点 {idx}",),
        risk_flags=(),
        official_links=(f"https://example.edu/{idx}",),
        internal_source_refs=(ref,),
        in_2024_catalog=True,
        field_evidence=(
            _field("summary", f"摘要 {idx}", ref),
            _field("eligibility", "本科生", ref),
            _field("schedule", f"窗口 {idx}", ref),
            _field("team_policy", "team", ref),
            _field("preparation_focus", f"重点 {idx}", ref),
        ),
    )


def _manifest() -> CompetitionCatalogManifest:
    return CompetitionCatalogManifest(
        version_id="catalog-v1",
        knowledge_base_version="kb-v1",
        rules_version="catalog-rules-v1",
        card_count=3,
        in_2024_catalog_count=3,
        content_hash="hash",
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )


@pytest.mark.asyncio
async def test_compare_supports_two_to_four_and_uses_pipeline():
    ref = _card(1).internal_source_refs[0]
    raw = GenerationResult(
        output={
            "summary": "竞赛 1 更偏算法，竞赛 2/3 更偏实践。",
            "claims": [{
                "text": "竞赛 1 更偏算法。",
                "content_class": "fact",
                "fact_indices": [0],
            }],
        },
        claims=(Claim(
            text="竞赛 1 更偏算法。",
            content_class=ContentClass.FACT,
            fact_refs=(ref,),
        ),),
    )
    llm = FakeLLMGenerationPort(raw)
    catalog = FakeCompetitionCatalogPort(
        (_card(1), _card(2, "工学"), _card(3, "综合与创业")),
        manifest=_manifest(),
    )

    comparison = await compare_competitions(
        competition_ids=("cmp_1", "cmp_2", "cmp_3"),
        catalog=catalog,
        pipeline=ConstrainedGenerationPipeline(llm),
    )

    assert comparison.competition_ids == ("cmp_1", "cmp_2", "cmp_3")
    assert comparison.summary.startswith("竞赛 1 更偏算法")
    assert len(comparison.dimensions) == 5
    assert comparison.internal_source_refs == (ref,)
    assert llm.calls[0]["system_prompt_id"] == "dext_competition.qa.compare.v1"
    assert llm.calls[0]["generation_profile_version"] == "competition.qa.v1"


@pytest.mark.asyncio
async def test_compare_rejects_invalid_card_count():
    catalog = FakeCompetitionCatalogPort((_card(1),), manifest=_manifest())
    raw = GenerationResult(output={"summary": "", "claims": []})

    with pytest.raises(ValueError):
        await compare_competitions(
            competition_ids=("cmp_1",),
            catalog=catalog,
            pipeline=ConstrainedGenerationPipeline(FakeLLMGenerationPort(raw)),
        )

