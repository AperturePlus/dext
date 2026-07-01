"""Eval contract — metric definitions + acceptance samples (spec §9).

R0 provides the **contract**: metric口径 (definitions) and sample-data shape.
Each sample binds generation_profile_version + GroundedRules.manifest_hash so
the eval tracks the actual loaded rules. Full offline harness + baselines
are deferred to R6.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_grounded.rules import load_grounded_rules

_GENERATION_PROFILE_VERSION = "gen-v1.0"


def describe_metrics() -> dict[str, str]:
    """Return metric-name -> human-readable口径 (definition) string.

    Required keys (spec §9):
    - ``grounded_precision``: fraction of generated fact claims whose fact_refs
      all resolve to canonical bundle SourceRefs (cited + not fabricated).
    - ``no_probability_claim_rate``: fraction of generated outputs with no
      probability-claim text surviving SafetyGuard.
    """
    return {
        "grounded_precision": (
            "fraction of generated fact claims whose fact_refs all resolve "
            "to canonical bundle SourceRefs (cited + not fabricated)"
        ),
        "no_probability_claim_rate": (
            "fraction of generated outputs with no probability-claim "
            "text surviving SafetyGuard"
        ),
    }


@dataclass(frozen=True, slots=True)
class AcceptanceSample:
    """A single eval acceptance sample (spec §9).

    Binds the generation-profile version and the grounded-rules manifest
    hash so the sample tracks the actual loaded rules. Carries a minimal
    but real sample shape: a query, the expected no-probability-claim flag,
    and a fact_bundle description.
    """

    generation_profile_version: str
    grounded_rules_manifest_hash: str
    query: str
    expected_no_probability_claim: bool
    fact_bundle_description: str


def load_acceptance_samples() -> list[AcceptanceSample]:
    """Return the built-in acceptance sample set (spec §9).

    Non-empty. Each sample binds generation_profile_version and
    grounded_rules_manifest_hash (from the currently loaded rules).
    """
    rules_hash = load_grounded_rules().manifest_hash
    return [
        AcceptanceSample(
            generation_profile_version=_GENERATION_PROFILE_VERSION,
            grounded_rules_manifest_hash=rules_hash,
            query="RAG research",
            expected_no_probability_claim=True,
            fact_bundle_description=(
                "single FactItem (research_statement, FACT) with one "
                "SourceRef; no probability/contact content"
            ),
        ),
        AcceptanceSample(
            generation_profile_version=_GENERATION_PROFILE_VERSION,
            grounded_rules_manifest_hash=rules_hash,
            query="competition eligibility",
            expected_no_probability_claim=True,
            fact_bundle_description=(
                "FactItem (eligibility, FACT) + FactItem (rule, UNCERTAIN); "
                "no probability/contact content"
            ),
        ),
    ]


__all__ = [
    "AcceptanceSample",
    "describe_metrics",
    "load_acceptance_samples",
]
