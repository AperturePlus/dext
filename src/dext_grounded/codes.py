"""Canonical GenerationWarningCode registry (grounded-generation spec §8).

Single source of truth for warning/error code strings emitted by the grounded
generation layer and consumed by ``dext_recommend`` / ``dext_competition``.

Existing emitters (``claims.py``, ``citation.py``, ``safety.py``) use the matching
string literals verbatim; they are NOT refactored to reference the enum so the
existing brief-matched tests stay unchanged. New emitters should reference
``GenerationWarningCode.X.value`` instead of inventing a fresh literal, so that
the set of codes stays closed and consumers can branch on a stable contract.
"""
from __future__ import annotations

from enum import Enum


class GenerationWarningCode(str, Enum):
    # --- already-emitted failure modes ---
    FACT_REF_MISSING = "fact_ref_missing"
    UNCERTAIN_CLAIM_WITH_REFS = "uncertain_claim_with_refs"
    FABRICATED_REF = "fabricated_ref"
    FABRICATED_USER_CONTEXT = "fabricated_user_context"
    NO_GROUNDED_OUTPUT = "no_grounded_output"
    NO_PROBABILITY_CLAIM = "no_probability_claim"
    UNSAFE_ADVICE = "unsafe_advice"
    UNAUTHORIZED_CONTACT = "unauthorized_contact"
    STALE_FACT = "stale_fact"
    CONTENT_POLICY_REFUSAL = "content_policy_refusal"
    POLITICAL_SENSITIVE = "political_sensitive"
    PERSONAL_ATTACK = "personal_attack"
    SEXUAL_CONTENT = "sexual_content"
    VIOLENT_CONTENT = "violent_content"
    MENTOR_ATTACK = "mentor_attack"

    # --- spec §8 failure modes previously without canonical representation ---
    GENERATION_UNAVAILABLE = "generation_unavailable"
    GENERATION_PARSE_ERROR = "generation_parse_error"
    INSUFFICIENT_FACTS = "insufficient_facts"


__all__ = ["GenerationWarningCode"]
