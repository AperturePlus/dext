# tests/test_grounded_student_context.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import StudentContext
from dext_grounded.rules import load_grounded_rules


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


def test_student_context_accepts_legal_gpa_bucket():
    rules = load_grounded_rules()
    legal = next(iter(rules.gpa_buckets))
    ctx = StudentContext(gpa_bucket=legal)
    assert ctx.gpa_bucket == legal


@pytest.mark.parametrize(
    "raw",
    [
        "3.97",
        "3.9/4.0",
        "3.9",
        "rank 12",
        "12",
        "top10%",
        "GPA 3.97",
        "3.97/4.0",
    ],
)
def test_student_context_rejects_raw_gpa_value(raw):
    with pytest.raises(ValueError):
        StudentContext(gpa_bucket=raw)


@pytest.mark.parametrize(
    "raw",
    [
        "3.97",
        "rank 12",
        "12",
        "top5%",
        "rank #12",
    ],
)
def test_student_context_rejects_raw_rank_value(raw):
    with pytest.raises(ValueError):
        StudentContext(rank_bucket=raw)


def test_student_context_rejects_gpa_bucket_not_in_enum():
    with pytest.raises(ValueError):
        StudentContext(gpa_bucket="not-a-bucket")


def test_student_context_rejects_rank_bucket_not_in_enum():
    with pytest.raises(ValueError):
        StudentContext(rank_bucket="not-a-bucket")


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
    assert summary["completeness_bucket"] == "high"
    assert set(summary) == {"uses_profile", "completeness_bucket"}
    for forbidden in ("school", "major", "gpa_bucket", "rank_bucket",
                       "research_interests", "achievements_summary",
                       "competition_experience_summary", "profile_completeness"):
        assert forbidden not in summary, f"safe_log_summary must not expose {forbidden}"


def test_student_context_safe_log_summary_empty_context():
    ctx = StudentContext()
    summary = ctx.safe_log_summary()
    assert summary["uses_profile"] is False
    assert summary["completeness_bucket"] == "none"
    assert set(summary) == {"uses_profile", "completeness_bucket"}


@pytest.mark.parametrize(
    "completeness, expected",
    [
        (0.8, "high"),
        (0.66, "high"),
        (0.5, "medium"),
        (0.33, "medium"),
        (0.1, "low"),
        (0.01, "low"),
        (0.0, "none"),
        (None, "none"),
    ],
)
def test_student_context_safe_log_summary_buckets_completeness(completeness, expected):
    ctx = StudentContext(profile_completeness=completeness)
    summary = ctx.safe_log_summary()
    assert summary["completeness_bucket"] == expected
    # raw float must NEVER leak through any serialization path
    assert "profile_completeness" not in summary
    if completeness is not None:
        assert str(completeness) not in str(summary)
