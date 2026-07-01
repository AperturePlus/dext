"""Rule-based inspection of the final constrained-generation output (spec §6)."""
from __future__ import annotations

import re
from dataclasses import replace

from dext_grounded._output import empty_output, iter_output_strings, map_output_strings
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.codes import GenerationWarningCode
from dext_grounded.content import ContentClass
from dext_grounded.rules import GroundedRules, load_grounded_rules


class SafetyGuard:
    def __init__(self, rules: GroundedRules | None = None) -> None:
        self.rules = rules or load_grounded_rules()
        self._contact_patterns = tuple(
            re.compile(pattern) for pattern in self.rules.safety.contact_regexes
        )

    def inspect(
        self,
        result: GenerationResult,
        *,
        domain: str = "generic",
        include_contacts: bool = False,
    ) -> GenerationResult:
        if domain not in self.rules.domains:
            raise ValueError(f"unknown safety domain: {domain!r}")

        warnings = list(result.warnings)
        warning_messages = self.rules.warning_messages
        output_strings = tuple(iter_output_strings(result.output))
        all_text = tuple(claim.text for claim in result.claims) + output_strings
        mislabel = self.rules.safety.competition_report_dir_whitelist_mislabel

        unsafe_text = next(
            (
                text
                for text in all_text
                if any(pattern in text for pattern in self.rules.safety.unsafe_advice_patterns)
            ),
            None,
        )
        combined_output = "\n".join(output_strings)
        fake_whitelist = (
            domain == "competition"
            and mislabel.report_dir in combined_output
            and mislabel.fake_whitelist_label in combined_output
        )
        if unsafe_text is not None or fake_whitelist:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.UNSAFE_ADVICE.value,
                message=warning_messages[GenerationWarningCode.UNSAFE_ADVICE.value],
                claim_text=mislabel.claim_text if fake_whitelist else unsafe_text,
            ))
            return replace(
                result,
                claims=(),
                warnings=warnings,
                output=empty_output(result.output),
            )

        probability_patterns = tuple(
            pattern
            for pattern in self.rules.safety.probability_patterns
            if any(pattern in text for text in all_text)
        )
        stale_patterns = tuple(
            pattern
            for pattern in self.rules.safety.stale_patterns
            if domain == "competition" and any(pattern in text for text in all_text)
        )
        contact_detected = (
            not include_contacts
            and any(
                pattern.search(text)
                for text in all_text
                for pattern in self._contact_patterns
            )
        )

        for pattern in probability_patterns:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.NO_PROBABILITY_CLAIM.value,
                message=warning_messages[GenerationWarningCode.NO_PROBABILITY_CLAIM.value],
                claim_text=pattern,
            ))
        for pattern in stale_patterns:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.STALE_FACT.value,
                message=warning_messages[GenerationWarningCode.STALE_FACT.value],
                claim_text=pattern,
            ))
        if contact_detected:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.UNAUTHORIZED_CONTACT.value,
                message=warning_messages[GenerationWarningCode.UNAUTHORIZED_CONTACT.value],
            ))

        kept_claims: list[Claim] = []
        for claim in result.claims:
            if any(pattern in claim.text for pattern in probability_patterns):
                continue
            next_claim = claim
            if (
                claim.content_class == ContentClass.FACT
                and any(pattern in claim.text for pattern in stale_patterns)
            ):
                next_claim = replace(claim, content_class=ContentClass.UNCERTAIN)
            if contact_detected and not include_contacts:
                next_claim = replace(next_claim, text=self._sanitize_contacts(next_claim.text))
            kept_claims.append(next_claim)

        def sanitize_leaf(text: str) -> str:
            for pattern in probability_patterns:
                text = text.replace(pattern, "")
            for pattern in stale_patterns:
                text = text.replace(pattern, f"{pattern}[uncertain: 往届信息]")
            if not include_contacts:
                text = self._sanitize_contacts(text)
            return text

        sanitized = map_output_strings(result.output, sanitize_leaf)
        if contact_detected and any(
            pattern.search(text)
            for text in iter_output_strings(sanitized)
            for pattern in self._contact_patterns
        ):
            sanitized = empty_output(result.output)

        return replace(
            result,
            claims=kept_claims,
            warnings=warnings,
            output=sanitized,
        )

    def _sanitize_contacts(self, text: str) -> str:
        for pattern in self._contact_patterns:
            text = pattern.sub("", text)
        return text


__all__ = ["SafetyGuard"]
