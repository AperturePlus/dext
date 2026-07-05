from __future__ import annotations

import hashlib
from datetime import date

from dext_competition import (
    PlanChangeCard,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
    RecommendedCompetition,
    SourceRef,
)
from dext_competition.contracts.assistant import collapse_change_card_status
from dext_competition.http.adapters import card_result_to_public, recommended_competition_to_public


def _ref() -> SourceRef:
    return SourceRef("rules.md", "rules > a", hashlib.sha256(b"rules").hexdigest(), "rules")


def test_recommended_competition_mapping_hides_internal_refs_and_clamps_score() -> None:
    item = RecommendedCompetition(
        competition_id="cmp-1",
        display_name="测试竞赛",
        category="计算机",
        summary="summary",
        fit_level="high",
        score=1.5,
        score_components={"topic": 0.8},
        eligibility_notes="本科生可参加",
        schedule_notes="3 月报名，5 月比赛",
        team_notes="3-5 人",
        preparation_effort="每周 6 小时",
        short_reasons=("匹配算法方向",),
        risk_flags=("需复核当届通知",),
        official_links=("https://example.test",),
        internal_source_refs=(_ref(),),
    )

    public = recommended_competition_to_public(item)

    assert public["id"] == "cmp-1"
    assert public["name"] == "测试竞赛"
    assert public["match_score"] == 1.0
    assert public["official_url"] == "https://example.test"
    assert "internal_source_refs" not in public
    assert "score_components" not in public


def test_change_card_mapping_uses_openapi_named_fields_only() -> None:
    card = PlanChangeCard(
        id="card-1",
        type="add_task",
        summary="加任务",
        rationale="训练阶段需要模拟。",
        target_phase_key="practice",
        new_task=PreparationNewTaskDraft("模拟赛", 3, date(2026, 7, 20), "note"),
        phase_schedule=(PreparationPhaseScheduleDraft("practice", date(2026, 7, 15), date(2026, 8, 1)),),
        internal_source_refs=(_ref(),),
        validation_status="passed",
    )

    public = card_result_to_public(card)

    assert public["type"] == "add_task"
    assert public["status"] == "pending"
    assert public["new_task"]["due_date"] == "2026-07-20"
    assert public["phase_schedule"][0]["phase_key"] == "practice"
    assert "internal_source_refs" not in public


def test_change_card_status_collapse_matrix() -> None:
    assert collapse_change_card_status(
        validation_status="pending",
        approval_status="pending",
        application_status="not_applied",
    ) == "pending"
    assert collapse_change_card_status(
        validation_status="rejected",
        approval_status="accepted",
        application_status="applied",
    ) == "rejected"
    assert collapse_change_card_status(
        validation_status="passed",
        approval_status="declined",
        application_status="not_applied",
    ) == "declined"
    assert collapse_change_card_status(
        validation_status="passed",
        approval_status="accepted",
        application_status="applied",
    ) == "applied"
    assert collapse_change_card_status(
        validation_status="passed",
        approval_status="accepted",
        application_status="failed",
    ) == "stale"
