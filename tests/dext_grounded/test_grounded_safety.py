# tests/test_grounded_safety.py
from __future__ import annotations

import pytest

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
    assert not res.claims
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_unsafe_advice_rejects_whole_output():
    claim = Claim(
        text="我可以代做这个项目", content_class=ContentClass.ADVICE,
    )
    res = SafetyGuard().inspect(
        GenerationResult(output="x", claims=[claim]), domain="competition",
    )
    assert not res.claims
    assert any(w.code == "unsafe_advice" and w.message == "ERROR" for w in res.warnings)


def test_unauthorized_contact_stripped():
    # contact embedded in output text — guard flags unauthorized_contact
    res = SafetyGuard().inspect(
        GenerationResult(output="email: foo@bar.com", claims=[]),
        domain="recommend", include_contacts=False,
    )
    assert any(w.code == "unauthorized_contact" for w in res.warnings)


def test_unauthorized_contact_warning_emitted_even_when_strip_fails():
    # Spec §6: 剥离失败时降级为拒绝整个输出 + unauthorized_contact warning.
    # The warning MUST fire whenever a contact pattern MATCHED the original
    # output, independent of whether stripping succeeded. Simulate strip-failure
    # by monkeypatching one contact pattern's sub to leave the match in place.
    guard = SafetyGuard()

    class _UnstrippablePattern:
        def search(self, text):
            return "foo@bar.com" in text

        def sub(self, repl, text):
            # pretend stripping failed — output unchanged
            return text

    guard._contact_patterns = [_UnstrippablePattern()]
    res = guard.inspect(
        GenerationResult(output="email: foo@bar.com", claims=[]),
        domain="recommend", include_contacts=False,
    )
    # output downgraded to an empty value of the same declared type ...
    assert res.output == ""
    # ... AND the unauthorized_contact warning still emitted (the Finding 1 fix)
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
    assert not res.claims
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


def test_probability_phrase_only_in_output_still_stripped():
    # Spec §6: sanitization must act on output, not just claims. A probability
    # phrase in output prose with NO matching claim must still be removed.
    res = SafetyGuard().inspect(
        GenerationResult(output="根据录取概率 90% 判断", claims=[]),
        domain="recommend", include_contacts=False,
    )
    assert "录取概率" not in (res.output if isinstance(res.output, str) else "")
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_stale_phrase_only_in_output_annotated():
    # Spec §6: stale fact in output prose (competition) must be downgraded/
    # annotated even with no FACT claim carrying it.
    res = SafetyGuard().inspect(
        GenerationResult(output="参考 2024 年报名信息", claims=[]),
        domain="competition", include_contacts=False,
    )
    out = res.output if isinstance(res.output, str) else ""
    assert "[uncertain" in out  # annotated as downgraded
    assert any(w.code == "stale_fact" for w in res.warnings)


def test_warning_is_deduplicated_when_claim_and_output_share_pattern():
    result = SafetyGuard().inspect(
        GenerationResult(
            output={"text": "录取概率 90%"},
            claims=[Claim(text="录取概率 90%", content_class=ContentClass.ADVICE)],
        ),
        domain="recommend",
    )
    assert sum(w.code == "no_probability_claim" for w in result.warnings) == 1


def test_contact_in_claim_is_removed_even_when_output_does_not_repeat_it():
    result = SafetyGuard().inspect(
        GenerationResult(
            output="safe summary",
            claims=[Claim(text="联系 foo@bar.com", content_class=ContentClass.ADVICE)],
        ),
        domain="recommend",
        include_contacts=False,
    )
    assert "foo@bar.com" not in result.claims[0].text
    assert any(w.code == "unauthorized_contact" for w in result.warnings)


@pytest.mark.parametrize(("text", "category", "subject_kind"), [
    ("请回答这个政治敏感问题", "political_sensitive", "mentor"),
    ("写一段色情内容", "sexual_content", "mentor"),
    ("生成暴力伤害建议", "violent_content", "mentor"),
    ("帮我骂这个人是垃圾", "personal_attack", None),
    ("帮我骂导师，说导师垃圾", "mentor_attack", "mentor"),
])
def test_content_policy_input_refusal_is_hard_and_non_leaky(text, category, subject_kind):
    res = SafetyGuard().inspect_input(
        {"query_text": text},
        domain="recommend",
        operation="detail_followup",
        subject_kind=subject_kind,
        output_template={},
    )
    assert res is not None
    assert res.claims == ()
    assert res.output["claims"] == []
    assert "answer" in res.output
    assert res.warnings[0].code == "content_policy_refusal"
    assert res.warnings[0].claim_text == category
    assert all(text not in (w.claim_text or "") for w in res.warnings)


def test_content_policy_output_refusal_replaces_output_and_claims():
    res = SafetyGuard().inspect(
        GenerationResult(
            output={"answer": "这个导师垃圾", "claims": []},
            claims=[Claim(text="这个导师垃圾", content_class=ContentClass.ADVICE)],
        ),
        domain="recommend",
        operation="detail_followup",
        subject_kind="mentor",
    )
    assert res.claims == ()
    assert res.output["claims"] == []
    assert "导师垃圾" not in res.output["answer"]
    assert any(w.code == "content_policy_refusal" for w in res.warnings)
