"""Rule-based inspection of constrained-generation inputs and outputs."""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from dext_grounded._output import empty_output, iter_output_strings, map_output_strings
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.codes import GenerationWarningCode
from dext_grounded.content import ContentClass
from dext_grounded.rules import GroundedRules, load_grounded_rules

_CONTENT_POLICY_ORDER = (
    "mentor_attack",
    "political_sensitive",
    "sexual_content",
    "violent_content",
    "personal_attack",
)


class SafetyGuard:
    def __init__(self, rules: GroundedRules | None = None) -> None:
        self.rules = rules or load_grounded_rules()
        self._contact_patterns = tuple(
            re.compile(pattern) for pattern in self.rules.safety.contact_regexes
        )

    def inspect_input(
        self,
        value: Any,
        *,
        domain: str = "generic",
        operation: str | None = None,
        subject_kind: str | None = None,
        output_template: dict | str = "",
    ) -> GenerationResult | None:
        """Return a refusal GenerationResult when user-visible generation is unsafe."""
        if domain not in self.rules.domains:
            raise ValueError(f"unknown safety domain: {domain!r}")
        category = self._find_content_policy_category(
            tuple(iter_output_strings(value)), subject_kind=subject_kind,
        )
        if category is None:
            return None
        return GenerationResult(
            output=self._refusal_output(output_template, operation=operation),
            claims=(),
            warnings=self._content_policy_warnings(category),
        )

    def inspect(
        self,
        result: GenerationResult,
        *,
        domain: str = "generic",
        include_contacts: bool = False,
        operation: str | None = None,
        subject_kind: str | None = None,
    ) -> GenerationResult:
        if domain not in self.rules.domains:
            raise ValueError(f"unknown safety domain: {domain!r}")

        warnings = list(result.warnings)
        warning_messages = self.rules.warning_messages
        output_strings = tuple(iter_output_strings(result.output))
        all_text = tuple(claim.text for claim in result.claims) + output_strings
        mislabel = self.rules.safety.competition_report_dir_whitelist_mislabel
        content_policy_category = self._find_content_policy_category(
            all_text, subject_kind=subject_kind,
        )
        if content_policy_category is not None:
            warnings.extend(self._content_policy_warnings(content_policy_category))
            return replace(
                result,
                claims=(),
                warnings=warnings,
                output=self._refusal_output(result.output, operation=operation),
            )

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

    def _find_content_policy_category(
        self,
        texts: tuple[str, ...],
        *,
        subject_kind: str | None,
    ) -> str | None:
        categories = self.rules.safety.content_policy.categories
        lowered = tuple(text.lower() for text in texts)
        for category in _CONTENT_POLICY_ORDER:
            rule = categories.get(category)
            if rule is None:
                continue
            for text_lower in lowered:
                if any(pattern.lower() in text_lower for pattern in rule.patterns):
                    return category
                if rule.subject_terms and rule.attack_terms:
                    subject_hit = (
                        subject_kind == "mentor"
                        or any(term.lower() in text_lower for term in rule.subject_terms)
                    )
                    attack_hit = any(term.lower() in text_lower for term in rule.attack_terms)
                    if subject_hit and attack_hit:
                        return category
        return None

    def _content_policy_warnings(self, category: str) -> list[GenerationWarning]:
        warning_messages = self.rules.warning_messages
        return [
            GenerationWarning(
                code=GenerationWarningCode.CONTENT_POLICY_REFUSAL.value,
                message=warning_messages[GenerationWarningCode.CONTENT_POLICY_REFUSAL.value],
                claim_text=category,
            ),
            GenerationWarning(
                code=category,
                message=warning_messages.get(category, category),
            ),
        ]

    def _refusal_output(self, output_template: dict | str, *, operation: str | None) -> dict | str:
        policy = self.rules.safety.content_policy
        message = f"{policy.refusal_message}{policy.safe_alternative}"
        if isinstance(output_template, str):
            return message
        if operation == "detail_followup":
            return {"answer": message, "claims": []}
        if operation == "match_analysis":
            return {
                "summary": message,
                "dimension_scores": {},
                "next_steps": [policy.safe_alternative],
                "claims": [],
            }
        if operation == "outreach_email":
            return {"subject": "无法生成该内容", "body": message, "claims": []}
        if operation == "professor_comparison":
            return {
                "summary": message,
                "professor_notes": {},
                "evidence_gaps": {},
                "claims": [],
            }
        return {"message": message, "claims": []}


__all__ = ["SafetyGuard"]
