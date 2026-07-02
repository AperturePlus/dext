from __future__ import annotations

from dataclasses import fields

import pytest

from dext_grounded import GenerationProfile, GenerationWarningCode, GroundedRules
from dext_grounded.rules import load_grounded_rules, parse_grounded_rules
from dext_grounded.student_context import StudentContext


def test_grounded_rules_loads_versioned_manifest():
    rules = load_grounded_rules()
    assert isinstance(rules, GroundedRules)
    assert rules.version == "grounded-v1"
    assert "recommend" in rules.domains
    assert "competition" in rules.domains
    assert "generic" in rules.domains
    assert len(rules.manifest_hash) == 64


def test_grounded_rules_missing_required_key_is_clear():
    with pytest.raises(ValueError, match="grounded rules missing keys: domains"):
        parse_grounded_rules(
            {
                "version": "grounded-v1",
                "defaults": {"trim_token_budget": 4096, "quote_max_len": 500},
                "student_context_fields": [],
                "gpa_buckets": [],
                "rank_buckets": [],
                "completeness_buckets": {"high": 0.66},
                "warning_messages": {},
                "safety": {},
            }
        )


def test_grounded_rule_hash_is_stable():
    raw = {
        "version": "grounded-test",
        "defaults": {"trim_token_budget": 1, "quote_max_len": 2},
        "domains": ["generic"],
        "student_context_fields": ["school"],
        "gpa_buckets": ["top10"],
        "rank_buckets": ["top5"],
        "completeness_buckets": {"high": 0.5, "medium": 0.2, "low": 0.01, "none": 0.0},
        "warning_messages": {"fact_ref_missing": "message"},
        "safety": {
            "content_policy": {
                "refusal_message": "refuse",
                "safe_alternative": "safe alternative",
                "categories": {
                    "political_sensitive": {"patterns": []},
                    "personal_attack": {"patterns": []},
                    "sexual_content": {"patterns": []},
                    "violent_content": {"patterns": []},
                    "mentor_attack": {
                        "patterns": [],
                        "subject_terms": ["导师"],
                        "attack_terms": ["垃圾"],
                    },
                },
            },
            "probability_patterns": [],
            "unsafe_advice_patterns": [],
            "contact_regexes": [],
            "stale_patterns": [],
            "content_policy": {
                "refusal_message": "refuse",
                "safe_alternative": "safe",
                "categories": {
                    "political_sensitive": {"patterns": []},
                    "personal_attack": {"patterns": []},
                    "sexual_content": {"patterns": []},
                    "violent_content": {"patterns": []},
                    "mentor_attack": {"patterns": []},
                },
            },
            "competition_report_dir_whitelist_mislabel": {
                "report_dir": "report",
                "fake_whitelist_label": "label",
                "claim_text": "claim",
            },
        },
    }
    assert parse_grounded_rules(raw).manifest_hash == parse_grounded_rules(raw).manifest_hash


def test_content_policy_rules_are_required_and_loaded():
    rules = load_grounded_rules()
    categories = rules.safety.content_policy.categories
    assert set(categories) >= {
        "political_sensitive", "personal_attack", "sexual_content",
        "violent_content", "mentor_attack",
    }
    assert "导师" in categories["mentor_attack"].subject_terms
    assert rules.safety.content_policy.refusal_message


def test_warning_message_codes_are_canonical():
    canonical = {code.value for code in GenerationWarningCode}
    assert set(load_grounded_rules().warning_messages) == canonical


def test_configured_student_context_fields_exist():
    student_fields = {field.name for field in fields(StudentContext)}
    assert set(load_grounded_rules().student_context_fields) <= student_fields


def test_profile_defaults_come_from_grounded_rules():
    rules = load_grounded_rules()
    profile = GenerationProfile(version="gen-v1.0")
    assert profile.trim_token_budget == rules.trim_token_budget
    assert profile.quote_max_len == rules.quote_max_len
