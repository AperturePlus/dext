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
    # output downgraded to refusal template ...
    assert res.output == "[output rejected: unsafe advice]"
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
