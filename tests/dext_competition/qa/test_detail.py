from __future__ import annotations

import datetime

import pytest

from dext_competition import CompetitionCard, CompetitionCatalogManifest, SourceRef
from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionFieldEvidence,
)
from dext_competition.ports import FakeCompetitionCatalogPort
from dext_competition.qa import FRESHNESS_NOTICE, get_competition_detail


def _ref(name: str = "a") -> SourceRef:
    return SourceRef(
        doc_path=f"{name}.md",
        heading_path=f"{name} > rules",
        chunk_hash=name,
        quote_or_summary=f"{name} rules",
        official_url=f"https://example.edu/{name}",
        last_verified="2026-06-30",
    )


def _evidence(field: str, value: str, ref: SourceRef | None = None):
    return CompetitionFieldEvidence(
        field=field,
        values=(value,),
        status=CatalogEvidenceStatus.GROUNDED,
        source_refs=(ref or _ref(field),),
    )


def _card() -> CompetitionCard:
    ref = _ref()
    return CompetitionCard(
        competition_id="cmp_test",
        display_name="测试竞赛",
        category="计算机",
        tags=("算法",),
        summary="规则摘要",
        eligibility="本科生可参加",
        schedule="通常春季报名",
        team_policy="team",
        materials=("报名表", "作品"),
        ai_compliance="按当届规则披露",
        preparation_focus=("算法基础",),
        risk_flags=("报名时间需复核",),
        official_links=("https://example.edu/rules",),
        internal_source_refs=(ref,),
        in_2024_catalog=True,
        field_evidence=(
            _evidence("display_name", "测试竞赛", ref),
            _evidence("category", "计算机", ref),
            _evidence("summary", "规则摘要", ref),
            _evidence("eligibility", "本科生可参加", ref),
            _evidence("schedule", "通常春季报名", ref),
            _evidence("team_policy", "team", ref),
            _evidence("materials", "报名表", ref),
            _evidence("ai_compliance", "按当届规则披露", ref),
            _evidence("preparation_focus", "算法基础", ref),
            _evidence("risk_flags", "报名时间需复核", ref),
            _evidence("official_links", "https://example.edu/rules", ref),
            _evidence("in_2024_catalog", "true", ref),
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


@pytest.mark.asyncio
async def test_detail_is_deterministic_catalog_mapping_without_llm():
    catalog = FakeCompetitionCatalogPort((_card(),), manifest=_manifest())

    detail = await get_competition_detail(catalog, "cmp_test")

    assert detail.competition_id == "cmp_test"
    assert detail.display_name == "测试竞赛"
    assert detail.materials == ("报名表", "作品")
    assert detail.internal_source_refs == (_ref(),)
    assert detail.freshness_notice == FRESHNESS_NOTICE
    assert catalog.calls == [{"op": "get", "competition_id": "cmp_test"}]


@pytest.mark.asyncio
async def test_detail_missing_competition_raises_keyerror():
    catalog = FakeCompetitionCatalogPort((), manifest=_manifest())

    with pytest.raises(KeyError):
        await get_competition_detail(catalog, "missing")

