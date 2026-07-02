"""Canonical GenerationWarningCode registry (grounded-generation spec §8).

The enum is the single source of truth for warning/error code strings so that
consumers (dext_recommend, dext_competition) do not invent divergent literals.
"""
from __future__ import annotations

import pytest

from dext_grounded import GenerationWarningCode


EXPECTED_CODES = {
    "FACT_REF_MISSING": "fact_ref_missing",
    "UNCERTAIN_CLAIM_WITH_REFS": "uncertain_claim_with_refs",
    "FABRICATED_REF": "fabricated_ref",
    "FABRICATED_USER_CONTEXT": "fabricated_user_context",
    "NO_GROUNDED_OUTPUT": "no_grounded_output",
    "NO_PROBABILITY_CLAIM": "no_probability_claim",
    "UNSAFE_ADVICE": "unsafe_advice",
    "UNAUTHORIZED_CONTACT": "unauthorized_contact",
    "STALE_FACT": "stale_fact",
    "CONTENT_POLICY_REFUSAL": "content_policy_refusal",
    "POLITICAL_SENSITIVE": "political_sensitive",
    "PERSONAL_ATTACK": "personal_attack",
    "SEXUAL_CONTENT": "sexual_content",
    "VIOLENT_CONTENT": "violent_content",
    "MENTOR_ATTACK": "mentor_attack",
    "GENERATION_UNAVAILABLE": "generation_unavailable",
    "GENERATION_PARSE_ERROR": "generation_parse_error",
    "INSUFFICIENT_FACTS": "insufficient_facts",
}


def test_all_codes_exist_with_correct_string_values():
    for member_name, expected_value in EXPECTED_CODES.items():
        assert hasattr(GenerationWarningCode, member_name), (
            f"GenerationWarningCode missing member {member_name!r}"
        )
        member = getattr(GenerationWarningCode, member_name)
        assert member.value == expected_value, (
            f"{member_name}: expected {expected_value!r}, got {member.value!r}"
        )


def test_three_new_section_8_codes_present():
    values = {c.value for c in GenerationWarningCode}
    assert "generation_unavailable" in values
    assert "generation_parse_error" in values
    assert "insufficient_facts" in values


def test_each_code_value_is_non_empty_string():
    for member in GenerationWarningCode:
        assert isinstance(member.value, str)
        assert member.value != ""
        # no whitespace, lowercase snake_case
        assert member.value == member.value.strip()
        assert " " not in member.value


def test_exactly_expected_codes():
    assert len(list(GenerationWarningCode)) == len(EXPECTED_CODES)


def test_enum_is_str_enum():
    assert issubclass(GenerationWarningCode, str)
    # a member is also a plain str equal to its value
    assert GenerationWarningCode.NO_GROUNDED_OUTPUT == "no_grounded_output"
