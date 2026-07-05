"""G0 contract-immutability tests for dext_competition.

Pins the deep-immutability rule (grounded spec §3): every contract dataclass
is frozen + slot-based, tuple-typed collection fields, and coerces list input
to tuple so callers cannot mutate shared state after construction. Also pins
the field shapes C1/C2 will consume so their signatures cannot drift without
breaking G0.
"""
from __future__ import annotations

import dataclasses

import pytest

from dext_competition import (
    CatalogEvidenceStatus,
    CompetitionCard,
    CompetitionCatalogManifest,
    CompetitionFieldEvidence,
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
    CompetitionRecommendRequest,
    CompetitionRecommendResponse,
    CompetitionWarning,
    GroundedAnswer,
    KnowledgeBaseManifest,
    KnowledgeHit,
    PlanAssistantRequest,
    PlanAssistantResult,
    PlanAssistantServiceResult,
    PlanChangeCard,
    PlanChangeSet,
    PreparationPlanDraft,
    RecommendedCompetition,
)
from dext_competition.contracts.assistant import (
    AssistantHistoryTurn,
    CardResult,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
)
from dext_competition.contracts.knowledge import Chunk
from dext_grounded import SourceRef

_FROZEN_MODELS = [
    Chunk, KnowledgeBaseManifest, KnowledgeHit, CompetitionCard,
    CompetitionCatalogManifest, CompetitionFieldEvidence,
    CompetitionPreferences, CompetitionQueryUnderstanding, CompetitionRecommendRequest,
    CompetitionRecommendResponse, RecommendedCompetition, CompetitionWarning,
    GroundedAnswer, PreparationPlanDraft, PlanChangeCard, PlanChangeSet,
    PlanAssistantRequest, PlanAssistantResult, PlanAssistantServiceResult,
    AssistantHistoryTurn, CardResult, PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
]


@pytest.mark.parametrize("model", _FROZEN_MODELS)
def test_contracts_are_frozen_with_slots(model):
    # frozen: dataclasses.fields exposes the flag
    assert getattr(model.__dataclass_params__, "frozen", False) is True
    # slots: the class itself has no __dict__ on instances
    assert "__slots__" in model.__dict__


def _ref(doc="竞赛信息总览.md", heading="总览") -> SourceRef:
    return SourceRef(
        doc_path=doc, heading_path=heading, chunk_hash="h1",
        quote_or_summary="snippet", official_url=None, last_verified=None,
    )


def test_chunk_coerces_list_fields_to_tuple_and_is_immutable():
    chunk = Chunk(
        doc_path="竞赛信息总览.md",
        heading_path="竞赛信息总览.md > 总览",
        chunk_hash="abc",
        text="some text",
        source_links=["http://a", "http://b"],
        last_verified=None,
    )
    assert isinstance(chunk.source_links, tuple)
    assert chunk.source_links == ("http://a", "http://b")
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "mutated"  # type: ignore[misc]


def test_chunk_exposes_canonical_source_ref():
    chunk = Chunk(
        doc_path="rules.md",
        heading_path="规则",
        chunk_hash="abc",
        text="原始规则",
        source_links=("https://example.edu/rules",),
        last_verified="2026-06-30",
    )
    ref = chunk.to_source_ref()
    assert (ref.doc_path, ref.heading_path, ref.chunk_hash) == (
        chunk.doc_path,
        chunk.heading_path,
        chunk.chunk_hash,
    )
    assert ref.quote_or_summary == chunk.text
    assert ref.official_url == chunk.source_links[0]
    assert ref.last_verified == chunk.last_verified


def test_knowledge_base_manifest_fields():
    import datetime
    m = KnowledgeBaseManifest(
        version_id="kb-v1",
        source_root="data/竞赛助手/",
        file_count=18,
        markdown_file_count=18,
        content_hash="aggregate-hash",
        generated_at=datetime.datetime(2026, 7, 2, 12, 0, 0),
    )
    assert m.version_id == "kb-v1"
    assert m.file_count == 18


def test_knowledge_hit_is_immutable_and_carries_canonical_ref():
    chunk = Chunk("a.md", "a.md > A", "h", "text")
    hit = KnowledgeHit(chunk=chunk, source_ref=_ref("a.md", "a.md > A"), score=1.2)
    assert hit.chunk is chunk
    with pytest.raises(dataclasses.FrozenInstanceError):
        hit.score = 0.0  # type: ignore[misc]


def test_competition_card_internal_source_refs_are_tuple_and_sourcerefs():
    card = CompetitionCard(
        competition_id="icpc",
        display_name="ICPC",
        category="计算机",
        tags=["算法"],
        summary="s",
        eligibility="e",
        schedule="window",
        team_policy="team",
        materials=["m"],
        ai_compliance="allowed-with-disclosure",
        preparation_focus=["algo"],
        risk_flags=["time"],
        official_links=["http://icpc"],
        internal_source_refs=[_ref()],
        in_2024_catalog=True,
        last_verified=None,
    )
    assert isinstance(card.tags, tuple)
    assert isinstance(card.internal_source_refs, tuple)
    assert isinstance(card.internal_source_refs[0], SourceRef)
    assert card.in_2024_catalog is True


def test_catalog_field_evidence_is_deeply_immutable():
    evidence = CompetitionFieldEvidence(
        field="schedule",
        values=["2026 年 9 月"],
        status=CatalogEvidenceStatus.GROUNDED,
        source_refs=[_ref()],
    )
    assert evidence.values == ("2026 年 9 月",)
    assert evidence.source_refs == (_ref(),)


def test_recommended_competition_has_evidence_status_enum_values():
    # evidence_status is a closed enum string (overview §3)
    r = RecommendedCompetition(
        competition_id="icpc", display_name="ICPC", category="计算机",
        summary="s", fit_level="medium", score=0.5,
        score_components={}, eligibility_notes="", schedule_notes="",
        team_notes="", preparation_effort="high", short_reasons=("r",),
        risk_flags=("time",), official_links=("http://icpc",),
        evidence_status="grounded", freshness_notice=None,
        internal_source_refs=(_ref(),),
    )
    assert r.evidence_status == "grounded"
    assert isinstance(r.short_reasons, tuple)
    assert r.available_actions == ("detail", "create_plan", "ask_rules", "compare")


def test_competition_query_understanding_is_deeply_immutable():
    q = CompetitionQueryUnderstanding(
        interests=["算法", "建模"],
        missing_information=["weekly_hours"],
        needs_clarification=False,
        confidence=0.7,
    )
    assert q.interests == ("算法", "建模")
    assert q.missing_information == ("weekly_hours",)


def test_plan_change_card_three_status_axes():
    # overview §6: validation_status / approval_status / application_status
    card = PlanChangeCard(
        id="card-1",
        type="move_task",
        summary="移动任务",
        target_task_id="t1",
        new_date=__import__("datetime").date(2026, 7, 10),
        rationale="r",
        internal_source_refs=(_ref(),),
        validation_status="passed",
        approval_status="pending",
        application_status="not_applied",
    )
    assert card.validation_status == "passed"
    assert card.approval_status == "pending"
    assert card.application_status == "not_applied"
    assert card.status == "pending"
    assert card.action == "move_task"
