# tests/test_grounded_safety.py
from __future__ import annotations

from dext_grounded import (
    Claim, ContentClass, GenerationResult, SafetyGuard,
)


def test_probability_claim_dropped():
    claim = Claim(
        text="录取概率 80%", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="recommend",
    )
    assert res.claims == []
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_unsafe_advice_rejects_whole_output():
    claim = Claim(
        text="我可以代做这个项目", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="competition",
    )
    assert res.claims == []
    assert any(w.code == "unsafe_advice" and w.message == "ERROR" for w in res.warnings)


def test_unauthorized_contact_stripped():
    # contact embedded in output text — guard flags unauthorized_contact
    res = SafetyGuard().inspect(
        GenerationResult(output="email: foo@bar.com", claims=[]),
        domain="recommend", include_contacts=False,
    )
    assert any(w.code == "unauthorized_contact" for w in res.warnings)


def test_stale_fact_downgraded_to_uncertain():
    claim = Claim(
        text="2024 年报名时间是 3 月", content_class=ContentClass.FACT,
        # fact_refs omitted → already uncertain at Claim level, but guard should
        # still detect "stale" wording when presented as current.
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="competition",
    )
    assert any(w.code == "stale_fact" for w in res.warnings)
    assert res.claims[0].content_class == ContentClass.UNCERTAIN


def test_recommend_blocks_admission_probability():
    claim = Claim(
        text="导师接收你的意愿较高", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="recommend",
    )
    assert res.claims == []
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_competition_2024_dir_as_whitelist_rejected():
    claim = Claim(
        text="这是教育部白名单赛事", content_class=ContentClass.FACT,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="2024 竞赛分析报告目录 教育部白名单", claims=[claim]),
        domain="competition",
    )
    assert any(w.code == "unsafe_advice" and w.message == "ERROR" for w in res.warnings)
