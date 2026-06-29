"""Versioned deterministic title, role, and eligibility rules."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml

from dext_graph.catalog.ids import canonical_json_hash
from dext_graph.catalog.normalization import normalize_text


@dataclass(frozen=True)
class CurationRules:
    version: str
    normalization_version: str
    empty_values: tuple[str, ...]
    title_families: dict[str, tuple[str, ...]]
    excluded_context_terms: tuple[str, ...]
    master_positive: tuple[str, ...]
    master_negative: tuple[str, ...]
    phd_positive: tuple[str, ...]
    phd_negative: tuple[str, ...]
    manifest_hash: str


@dataclass(frozen=True)
class RoleDecision:
    title_family: str
    role_status: str
    reason_codes: tuple[str, ...]
    master_eligibility: str
    phd_eligibility: str


def load_curation_rules() -> CurationRules:
    path = files("dext_graph.catalog").joinpath("rules", "curation_v1.yaml")
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = {
        "version", "normalization_version", "empty_values", "title_families",
        "excluded_context_terms", "master_positive", "master_negative",
        "phd_positive", "phd_negative",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise ValueError("curation rules missing keys: " + ", ".join(missing))
    families = {
        str(key): tuple(str(item) for item in value)
        for key, value in dict(raw["title_families"]).items()
    }
    return CurationRules(
        version=str(raw["version"]),
        normalization_version=str(raw["normalization_version"]),
        empty_values=tuple(str(v) for v in raw["empty_values"]),
        title_families=families,
        excluded_context_terms=tuple(str(v) for v in raw["excluded_context_terms"]),
        master_positive=tuple(str(v) for v in raw["master_positive"]),
        master_negative=tuple(str(v) for v in raw["master_negative"]),
        phd_positive=tuple(str(v) for v in raw["phd_positive"]),
        phd_negative=tuple(str(v) for v in raw["phd_negative"]),
        manifest_hash=canonical_json_hash(raw),
    )


def _contains_any(values: list[str], terms: tuple[str, ...]) -> bool:
    return any(term.casefold() in value.casefold() for value in values for term in terms)


def _eligibility(values: list[str], positive: tuple[str, ...], negative: tuple[str, ...]) -> str:
    has_negative = _contains_any(values, negative)
    has_positive = any(
        positive_term.casefold() in value.casefold()
        and not any(negative_term.casefold() in value.casefold() for negative_term in negative)
        for value in values
        for positive_term in positive
    )
    if has_positive and has_negative:
        return "conflict"
    if has_positive:
        return "confirmed"
    return "unknown"


def classify_role(
    title: object,
    enrollment_values: list[str],
    context_values: list[str],
    rules: CurationRules,
) -> RoleDecision:
    title_text = normalize_text(title, empty_values=rules.empty_values) or ""
    title_family = "unknown"
    for family in ("clinical", "researcher", "associate", "professor", "lecturer", "technical"):
        if _contains_any([title_text], rules.title_families.get(family, ())):
            title_family = family
            break
    values = [value for value in enrollment_values if value]
    master = _eligibility(values, rules.master_positive, rules.master_negative)
    phd = _eligibility(values, rules.phd_positive, rules.phd_negative)
    if _contains_any(context_values + [title_text], rules.excluded_context_terms):
        return RoleDecision(title_family, "excluded", ("explicit_non_teacher_context",), master, phd)
    if master in {"confirmed", "conflict"} or phd in {"confirmed", "conflict"}:
        return RoleDecision(title_family, "included", ("supervisor_evidence",), master, phd)
    if title_family in {"professor", "associate", "researcher", "clinical"}:
        return RoleDecision(title_family, "included", ("teaching_research_title",), master, phd)
    if title_family == "lecturer":
        return RoleDecision(title_family, "review", ("lecturer_without_supervisor_evidence",), master, phd)
    if title_family == "technical":
        return RoleDecision(title_family, "review", ("technical_title_without_exclusion_context",), master, phd)
    return RoleDecision(title_family, "review", ("missing_or_unclassified_title",), master, phd)


__all__ = ["CurationRules", "RoleDecision", "classify_role", "load_curation_rules"]
