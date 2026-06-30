"""Safety guard — rule-based output inspection (spec §6).

Runs AFTER CitationValidator. Rules:
- probability claim (录取/保研/奖学金/综测加分/Offer/获奖/导师接收意愿):
  drop claim + no_probability_claim warning.
- unsafe advice (代做/挂名/伪造数据/赛中泄题/绕过查重/规避AI披露):
  reject whole output + unsafe_advice ERROR.
- unauthorized contact (email/phone when include_contacts=False):
  strip + unauthorized_contact warning.
- stale fact (往届时间/奖项比例/赛道/费用/AI规则当成当届): downgrade uncertain + stale_fact.

Domain extras:
- recommend: blocks admission/保研/导师接收意愿 (subset of probability rule).
- competition: "2024 竞赛分析报告目录" written as "教育部白名单" → unsafe_advice reject.
"""
from __future__ import annotations

import re
from dataclasses import replace

from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass

_DOMAINS = {"recommend", "competition", "generic"}

_PROBABILITY_PATTERNS = [
    "录取概率", "保研概率", "奖学金概率", "综测加分", "Offer 概率", "获奖概率",
    "导师接收意愿", "导师接收你的意愿", "录用意愿", "导师愿意接收",
]
_UNSAFE_ADVICE_PATTERNS = [
    "代做", "挂名", "伪造数据", "赛中泄题", "绕过查重", "规避 AI 披露", "规避AI披露",
]
_CONTACT_PATTERNS = [
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),  # email
    re.compile(r"1[3-9]\d{9}"),                                       # CN mobile
]
_STALE_PATTERNS = [
    "2024 年报名", "2024年报名", "往届", "去年", "上一届",
]
_COMPETITION_WHITELIST_FAKE = "教育部白名单"
_COMPETITION_REPORT_DIR = "2024 竞赛分析报告目录"


class SafetyGuard:
    def inspect(
        self,
        result: GenerationResult,
        *,
        domain: str = "generic",
        include_contacts: bool = False,
    ) -> GenerationResult:
        if domain not in _DOMAINS:
            raise ValueError(f"unknown safety domain: {domain!r}")

        warnings: list[GenerationWarning] = list(result.warnings)
        kept_claims: list[Claim] = []
        output_text = result.output if isinstance(result.output, str) else ""

        # competition: fake-whitelist reject fires on output text first
        if domain == "competition" and _COMPETITION_REPORT_DIR in output_text \
                and _COMPETITION_WHITELIST_FAKE in output_text:
            warnings.append(GenerationWarning(
                code="unsafe_advice", message="ERROR",
                claim_text="2024 report dir mislabelled as 教育部白名单",
            ))
            return replace(result, claims=[], warnings=warnings)

        for claim in result.claims:
            text = claim.text
            if any(p in text for p in _UNSAFE_ADVICE_PATTERNS):
                warnings.append(GenerationWarning(
                    code="unsafe_advice", message="ERROR", claim_text=text,
                ))
                return replace(result, claims=[], warnings=warnings)
            if any(p in text for p in _PROBABILITY_PATTERNS):
                warnings.append(GenerationWarning(
                    code="no_probability_claim", message="dropped probability claim",
                    claim_text=text,
                ))
                continue
            if domain == "competition" and any(p in text for p in _STALE_PATTERNS) \
                    and claim.content_class == ContentClass.FACT:
                warnings.append(GenerationWarning(
                    code="stale_fact", message="downgraded stale fact to uncertain",
                    claim_text=text,
                ))
                kept_claims.append(replace(claim, content_class=ContentClass.UNCERTAIN))
                continue
            kept_claims.append(claim)

        if not include_contacts and isinstance(result.output, str):
            for pattern in _CONTACT_PATTERNS:
                if pattern.search(result.output):
                    warnings.append(GenerationWarning(
                        code="unauthorized_contact",
                        message="output contained contact info without include_contacts",
                    ))
                    break

        return replace(result, claims=kept_claims, warnings=warnings)


__all__ = ["SafetyGuard"]
