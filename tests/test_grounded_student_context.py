# tests/test_grounded_student_context.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import StudentContext


def test_student_context_defaults():
    ctx = StudentContext()
    assert ctx.education_stage is None
    assert ctx.school is None
    assert ctx.research_interests == []
    assert ctx.profile_completeness is None


def test_student_context_is_frozen():
    ctx = StudentContext(school="X University")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.school = "Y University"


def test_student_context_safe_log_summary_omits_raw_values():
    ctx = StudentContext(
        education_stage="本科高年级",
        school="某大学",
        major="CS",
        gpa_bucket="top10",
        rank_bucket="top5",
        research_interests=["NLP"],
        achievements_summary="won a national prize",
        competition_experience_summary="ICPC regional",
        profile_completeness=0.8,
    )
    summary = ctx.safe_log_summary()
    # only completeness bucket + whether used, never raw text
    assert summary["uses_profile"] is True
    assert summary["profile_completeness"] == 0.8
    assert summary["education_stage"] == "本科高年级"
    for forbidden in ("school", "major", "gpa_bucket", "rank_bucket",
                       "research_interests", "achievements_summary",
                       "competition_experience_summary"):
        assert forbidden not in summary, f"safe_log_summary must not expose {forbidden}"


def test_student_context_safe_log_summary_empty_context():
    ctx = StudentContext()
    summary = ctx.safe_log_summary()
    assert summary["uses_profile"] is False
    assert summary["profile_completeness"] is None
