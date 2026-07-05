from __future__ import annotations

from dext_competition import CompetitionCard
from dext_competition.planning import build_phase_templates, diagnose_preparation_level


def _card(category: str = "计算机") -> CompetitionCard:
    return CompetitionCard("cmp-1", "测试竞赛", category, summary="测试")


def test_submission_template_keeps_defense_and_beginner_foundation() -> None:
    phases = build_phase_templates(_card(), time_model="submission_deadline", experience_level="beginner")
    assert [phase.key for phase in phases][-2:] == ["submission", "defense_prep"]
    foundation = phases[0]
    assert all(task.required for task in foundation.tasks)


def test_window_template_has_sprint_and_recovery() -> None:
    phases = build_phase_templates(_card("机器人"), time_model="competition_window", experience_level="experienced")
    assert [phase.key for phase in phases][-2:] == ["event_sprint", "recovery"]


def test_level_diagnosis_is_deterministic_and_never_claims_probability() -> None:
    beginner = diagnose_preparation_level([{"question_key": "experience", "answer": "没有参加过"}])
    experienced = diagnose_preparation_level([
        {"question_key": "experience", "answer": "多次参加并获奖，有论文复现和项目经验"}
    ])
    assert beginner.level == "beginner"
    assert experienced.level == "experienced"
    assert "概率" not in experienced.rationale + experienced.suggestion
