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
from dext_grounded.codes import GenerationWarningCode
from dext_grounded.content import ContentClass
from dext_grounded.rules import GroundedRules, load_grounded_rules


class SafetyGuard:
    def __init__(self, rules: GroundedRules | None = None) -> None:
        self.rules = rules or load_grounded_rules()
        self._contact_patterns = [
            re.compile(pattern) for pattern in self.rules.safety.contact_regexes
        ]

    def inspect(
        self,
        result: GenerationResult,
        *,
        domain: str = "generic",
        include_contacts: bool = False,
    ) -> GenerationResult:
        if domain not in self.rules.domains:
            raise ValueError(f"unknown safety domain: {domain!r}")

        warnings: list[GenerationWarning] = list(result.warnings)
        kept_claims: list[Claim] = []
        output_text = result.output if isinstance(result.output, str) else ""
        warning_messages = self.rules.warning_messages
        mislabel = self.rules.safety.competition_report_dir_whitelist_mislabel

        # competition: fake-whitelist reject fires on output text first
        if domain == "competition" and mislabel.report_dir in output_text \
                and mislabel.fake_whitelist_label in output_text:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.UNSAFE_ADVICE.value,
                message=warning_messages[GenerationWarningCode.UNSAFE_ADVICE.value],
                claim_text=mislabel.claim_text,
            ))
            # spec §6: reject the WHOLE output (blank), not just claims
            return replace(
                result, claims=[], warnings=warnings,
                output=self._refusal_template(),
            )

        # Track fragments that must be sanitized from the str output.
        # probability → remove the matched pattern plaintext from output
        # stale_fact (competition) → annotate the matched fragment as uncertain
        # Contacts are stripped in a separate pass below.
        fragments_to_remove: list[str] = []
        stale_fragments: list[str] = []

        for claim in result.claims:
            text = claim.text
            if any(p in text for p in self.rules.safety.unsafe_advice_patterns):
                warnings.append(GenerationWarning(
                    code=GenerationWarningCode.UNSAFE_ADVICE.value,
                    message=warning_messages[GenerationWarningCode.UNSAFE_ADVICE.value],
                    claim_text=text,
                ))
                # spec §6: reject the WHOLE output regardless of type
                return replace(
                    result, claims=[], warnings=warnings,
                    output=self._refusal_template(),
                )
            if any(p in text for p in self.rules.safety.probability_patterns):
                warnings.append(GenerationWarning(
                    code=GenerationWarningCode.NO_PROBABILITY_CLAIM.value,
                    message=warning_messages[
                        GenerationWarningCode.NO_PROBABILITY_CLAIM.value
                    ],
                    claim_text=text,
                ))
                # record every probability pattern appearing in this claim's
                # text for removal from the str output
                for p in self.rules.safety.probability_patterns:
                    if p in text:
                        fragments_to_remove.append(p)
                continue
            if domain == "competition" and any(
                p in text for p in self.rules.safety.stale_patterns
            ) \
                    and claim.content_class == ContentClass.FACT:
                warnings.append(GenerationWarning(
                    code=GenerationWarningCode.STALE_FACT.value,
                    message=warning_messages[GenerationWarningCode.STALE_FACT.value],
                    claim_text=text,
                ))
                for p in self.rules.safety.stale_patterns:
                    if p in text:
                        stale_fragments.append(p)
                kept_claims.append(replace(claim, content_class=ContentClass.UNCERTAIN))
                continue
            kept_claims.append(claim)

        # Build the sanitized str output (only when output is a str).
        sanitized = result.output
        if isinstance(sanitized, str):
            # 1. remove probability-claim plaintext fragments
            for frag in fragments_to_remove:
                sanitized = sanitized.replace(frag, "")
            # 2. annotate stale plaintext fragments as uncertain
            for frag in stale_fragments:
                if frag in sanitized:
                    sanitized = sanitized.replace(
                        frag, f"{frag}[uncertain: 往届]",
                    )
            # 3. strip unauthorized contact matches from the output
            if not include_contacts:
                # Detect whether ANY contact pattern matched the ORIGINAL output
                # (pre-strip). The unauthorized_contact warning MUST fire on
                # detection, independent of whether stripping succeeded — spec §6
                # requires the warning even on the strip-failure downgrade path.
                contact_detected = any(
                    pattern.search(result.output) for pattern in self._contact_patterns
                )
                if contact_detected:
                    for pattern in self._contact_patterns:
                        if pattern.search(sanitized):
                            stripped = pattern.sub("", sanitized)
                            if pattern.search(stripped):
                                # stripping failed to remove — downgrade to refusal
                                sanitized = self._refusal_template()
                                break
                            sanitized = stripped
                    # emit the unauthorized_contact warning once — fires whether
                    # stripping succeeded (contacts elided) or failed (output
                    # downgraded to the refusal template)
                    warnings.append(GenerationWarning(
                        code=GenerationWarningCode.UNAUTHORIZED_CONTACT.value,
                        message=warning_messages[
                            GenerationWarningCode.UNAUTHORIZED_CONTACT.value
                        ],
                    ))

        return replace(
            result, claims=kept_claims, warnings=warnings, output=sanitized,
        )

    @staticmethod
    def _refusal_template() -> str:
        return "[output rejected: unsafe advice]"


__all__ = ["SafetyGuard"]
