"""Versioned grounded-generation rules loaded from package data."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ContentPolicyCategoryRule:
    patterns: tuple[str, ...]
    subject_terms: tuple[str, ...] = ()
    attack_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentPolicyRules:
    refusal_message: str
    safe_alternative: str
    categories: dict[str, ContentPolicyCategoryRule]


@dataclass(frozen=True, slots=True)
class CompetitionWhitelistMislabelRule:
    report_dir: str
    fake_whitelist_label: str
    claim_text: str


@dataclass(frozen=True, slots=True)
class SafetyRules:
    probability_patterns: tuple[str, ...]
    unsafe_advice_patterns: tuple[str, ...]
    contact_regexes: tuple[str, ...]
    stale_patterns: tuple[str, ...]
    content_policy: ContentPolicyRules
    competition_report_dir_whitelist_mislabel: CompetitionWhitelistMislabelRule


@dataclass(frozen=True, slots=True)
class CompletenessBuckets:
    """Coarse profile-completeness bucket thresholds (spec §2.1).

    ``thresholds`` maps bucket name -> lower-bound float; the highest bucket
    whose threshold the raw float meets is selected by ``bucket_for()``.
    Ordering is enforced at parse time so ``bucket_for`` need not sort.
    """

    thresholds: tuple[tuple[str, float], ...]

    def bucket_for(self, completeness: float | None) -> str:
        if completeness is None:
            return "none"
        # thresholds are sorted descending; the first bucket whose threshold
        # the raw float meets is the coarse bucket (highest match wins).
        for name, threshold in self.thresholds:
            if completeness >= threshold:
                return name
        return "none"


@dataclass(frozen=True, slots=True)
class GroundedRules:
    version: str
    domains: tuple[str, ...]
    warning_messages: dict[str, str]
    student_context_fields: tuple[str, ...]
    gpa_buckets: frozenset[str]
    rank_buckets: frozenset[str]
    completeness_buckets: CompletenessBuckets
    safety: SafetyRules
    trim_token_budget: int
    quote_max_len: int
    manifest_hash: str

    def warning_message(self, code: str, fallback: str | None = None) -> str:
        if code in self.warning_messages:
            return self.warning_messages[code]
        if fallback is not None:
            return fallback
        raise KeyError(f"unknown grounded warning code: {code!r}")


def _canonical_json_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_tuple(raw: object, key: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ValueError(f"grounded rules {key} must be a list")
    return tuple(str(item) for item in raw)


def _as_frozenset(raw: object, key: str) -> frozenset[str]:
    if not isinstance(raw, list):
        raise ValueError(f"grounded rules {key} must be a list")
    return frozenset(str(item) for item in raw)


def _as_optional_tuple(raw: object, key: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    return _as_tuple(raw, key)


def _parse_completeness_buckets(raw: object) -> CompletenessBuckets:
    if not isinstance(raw, dict):
        raise ValueError("grounded rules completeness_buckets must be a mapping")
    pairs: list[tuple[str, float]] = []
    for name, value in raw.items():
        try:
            threshold = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"grounded rules completeness_buckets {name!r} threshold must be numeric"
            ) from exc
        if threshold < 0.0 or threshold > 1.0:
            raise ValueError(
                f"grounded rules completeness_buckets {name!r} threshold must be in [0,1]"
            )
        pairs.append((str(name), threshold))
    # sort by threshold descending so bucket_for picks the highest matching bucket
    pairs.sort(key=lambda item: item[1], reverse=True)
    if not any(name == "none" for name, _ in pairs):
        pairs.append(("none", 0.0))
    return CompletenessBuckets(thresholds=tuple(pairs))


def _parse_content_policy(raw: object) -> ContentPolicyRules:
    if not isinstance(raw, dict):
        raise ValueError("grounded safety content_policy must be a mapping")
    required = {"refusal_message", "safe_alternative", "categories"}
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError("grounded safety content_policy missing keys: " + ", ".join(missing))
    categories_raw = raw["categories"]
    if not isinstance(categories_raw, dict):
        raise ValueError("grounded safety content_policy.categories must be a mapping")
    required_categories = {
        "political_sensitive", "personal_attack", "sexual_content",
        "violent_content", "mentor_attack",
    }
    missing_categories = sorted(required_categories - categories_raw.keys())
    if missing_categories:
        raise ValueError(
            "grounded safety content_policy.categories missing keys: "
            + ", ".join(missing_categories)
        )
    categories: dict[str, ContentPolicyCategoryRule] = {}
    for name, cfg in categories_raw.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"grounded safety content_policy category {name!r} must be a mapping")
        if "patterns" not in cfg:
            raise ValueError(f"grounded safety content_policy category {name!r} missing patterns")
        categories[str(name)] = ContentPolicyCategoryRule(
            patterns=_as_tuple(cfg["patterns"], f"content_policy.{name}.patterns"),
            subject_terms=_as_optional_tuple(
                cfg.get("subject_terms"), f"content_policy.{name}.subject_terms"
            ),
            attack_terms=_as_optional_tuple(
                cfg.get("attack_terms"), f"content_policy.{name}.attack_terms"
            ),
        )
    return ContentPolicyRules(
        refusal_message=str(raw["refusal_message"]),
        safe_alternative=str(raw["safe_alternative"]),
        categories=categories,
    )


def parse_grounded_rules(raw: dict[str, Any]) -> GroundedRules:
    required = {
        "version", "defaults", "domains", "student_context_fields",
        "gpa_buckets", "rank_buckets", "completeness_buckets",
        "warning_messages", "safety",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError("grounded rules missing keys: " + ", ".join(missing))

    defaults = raw["defaults"]
    if not isinstance(defaults, dict):
        raise ValueError("grounded rules defaults must be a mapping")
    safety = raw["safety"]
    if not isinstance(safety, dict):
        raise ValueError("grounded rules safety must be a mapping")
    warning_messages = raw["warning_messages"]
    if not isinstance(warning_messages, dict):
        raise ValueError("grounded rules warning_messages must be a mapping")

    safety_required = {
        "probability_patterns", "unsafe_advice_patterns", "contact_regexes",
        "stale_patterns", "content_policy", "competition_report_dir_whitelist_mislabel",
    }
    missing_safety = sorted(safety_required - safety.keys())
    if missing_safety:
        raise ValueError("grounded safety rules missing keys: " + ", ".join(missing_safety))

    competition_rule = safety["competition_report_dir_whitelist_mislabel"]
    if not isinstance(competition_rule, dict):
        raise ValueError(
            "grounded safety competition_report_dir_whitelist_mislabel must be a mapping"
        )
    competition_required = {"report_dir", "fake_whitelist_label", "claim_text"}
    missing_competition = sorted(competition_required - competition_rule.keys())
    if missing_competition:
        raise ValueError(
            "grounded competition mislabel rule missing keys: "
            + ", ".join(missing_competition)
        )

    return GroundedRules(
        version=str(raw["version"]),
        domains=_as_tuple(raw["domains"], "domains"),
        warning_messages={str(key): str(value) for key, value in warning_messages.items()},
        student_context_fields=_as_tuple(
            raw["student_context_fields"], "student_context_fields"
        ),
        gpa_buckets=_as_frozenset(raw["gpa_buckets"], "gpa_buckets"),
        rank_buckets=_as_frozenset(raw["rank_buckets"], "rank_buckets"),
        completeness_buckets=_parse_completeness_buckets(raw["completeness_buckets"]),
        safety=SafetyRules(
            probability_patterns=_as_tuple(
                safety["probability_patterns"], "safety.probability_patterns"
            ),
            unsafe_advice_patterns=_as_tuple(
                safety["unsafe_advice_patterns"], "safety.unsafe_advice_patterns"
            ),
            contact_regexes=_as_tuple(safety["contact_regexes"], "safety.contact_regexes"),
            stale_patterns=_as_tuple(safety["stale_patterns"], "safety.stale_patterns"),
            content_policy=_parse_content_policy(safety["content_policy"]),
            competition_report_dir_whitelist_mislabel=CompetitionWhitelistMislabelRule(
                report_dir=str(competition_rule["report_dir"]),
                fake_whitelist_label=str(competition_rule["fake_whitelist_label"]),
                claim_text=str(competition_rule["claim_text"]),
            ),
        ),
        trim_token_budget=int(defaults["trim_token_budget"]),
        quote_max_len=int(defaults["quote_max_len"]),
        manifest_hash=_canonical_json_hash(raw),
    )


@lru_cache(maxsize=1)
def load_grounded_rules() -> GroundedRules:
    path = files("dext_grounded").joinpath("rules", "grounded_v1.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("grounded rules must be a mapping")
    return parse_grounded_rules(raw)


__all__ = [
    "CompletenessBuckets",
    "CompetitionWhitelistMislabelRule",
    "ContentPolicyCategoryRule",
    "ContentPolicyRules",
    "GroundedRules",
    "SafetyRules",
    "load_grounded_rules",
    "parse_grounded_rules",
]
