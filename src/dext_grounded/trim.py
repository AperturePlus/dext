"""Trimmer hook — token-budget trimming of FactBundle (spec §5.1).

Pure function ``trim(fact_bundle, token_budget, query_terms) -> FactBundle``.

Behavior:
1. REJECT any FactItem whose value or any source_ref quote_or_summary matches
   a contact regex from GroundedRules.safety.contact_regexes (spec §5.1 forbids
   contact-bearing items in the trimmed bundle).
2. KEEP query-hit items (value or source_ref quote contains a query term,
   case-insensitive) — always, regardless of budget.
3. Include non-hit items only while within the token budget. R0 uses a
   char-approximation (len(value) // 4 tokens); the real tokenizer is R6.
4. NEVER drop source_refs from a kept FactItem — R0 trim only SELECTS items,
   never shortens value, so source_refs naturally survive.
5. Rebuild the canonical source_refs tuple from references used by kept facts,
   preserving the original canonical order.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.rules import load_grounded_rules

# Chars-per-token approximation (R0; real tokenizer deferred to R6).
_CHARS_PER_TOKEN = 4


@lru_cache(maxsize=1)
def _compiled_contact_patterns() -> tuple[re.Pattern[str], ...]:
    """Compile and cache the contact regexes from the loaded GroundedRules."""
    rules = load_grounded_rules()
    return tuple(re.compile(pattern) for pattern in rules.safety.contact_regexes)


def _has_contact(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _item_has_contact(item: FactItem, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Check whether a FactItem carries contact info in its value or source_refs."""
    if _has_contact(item.value, patterns):
        return True
    for ref in item.source_refs:
        if any(
            _has_contact(value, patterns)
            for value in (
                ref.doc_path,
                ref.heading_path,
                ref.chunk_hash,
                ref.quote_or_summary,
                ref.official_url or "",
                ref.last_verified or "",
            )
        ):
            return True
    return False


def _item_matches_query(item: FactItem, terms_lower: set[str]) -> bool:
    """Check whether a FactItem's value or any source_ref quote contains a query term."""
    value_lower = item.value.lower()
    if any(term in value_lower for term in terms_lower):
        return True
    for ref in item.source_refs:
        quote_lower = ref.quote_or_summary.lower()
        if any(term in quote_lower for term in terms_lower):
            return True
    return False


def _estimate_tokens(text: str) -> int:
    """R0 token approximation: len(value) // 4 (real tokenizer deferred to R6)."""
    return len(text) // _CHARS_PER_TOKEN


def trim(
    fact_bundle: FactBundle,
    token_budget: int,
    query_terms: Iterable[str],
) -> FactBundle:
    """Trim a FactBundle to fit a token budget (spec §5.1).

    Pure function: never mutates the input. Returns a new FactBundle.

    - Query-hit items ALWAYS survive (regardless of budget).
    - Contact-bearing items are ALWAYS rejected (even if query-hit).
    - Non-hit items are kept only while the remaining budget allows.
    - source_refs on kept items are never dropped.
    - The canonical source_refs tuple contains only refs used by kept items.
    """
    patterns = _compiled_contact_patterns()
    terms_lower = {str(term).lower() for term in query_terms}

    kept: list[FactItem] = []
    remaining_budget = token_budget if token_budget > 0 else 0

    for item in fact_bundle.facts:
        # 1. REJECT contact-bearing items (spec §5.1)
        if _item_has_contact(item, patterns):
            continue

        # 2. KEEP query-hit items always
        if _item_matches_query(item, terms_lower):
            kept.append(item)
            continue

        # 3. Non-hit items: include only if within remaining budget
        if token_budget <= 0:
            continue
        item_tokens = _estimate_tokens(item.value)
        if item_tokens <= remaining_budget:
            kept.append(item)
            remaining_budget -= item_tokens
        # else: drop — over budget

    used_ref_identities = {
        (
            ref.doc_path,
            ref.heading_path,
            ref.chunk_hash,
            ref.quote_or_summary,
            ref.official_url,
        )
        for item in kept
        for ref in item.source_refs
    }
    canonical_refs = tuple(
        ref
        for ref in fact_bundle.source_refs
        if (
            ref.doc_path,
            ref.heading_path,
            ref.chunk_hash,
            ref.quote_or_summary,
            ref.official_url,
        ) in used_ref_identities
        and not any(
            _has_contact(value, patterns)
            for value in (
                ref.doc_path,
                ref.heading_path,
                ref.chunk_hash,
                ref.quote_or_summary,
                ref.official_url or "",
                ref.last_verified or "",
            )
        )
    )

    return FactBundle(
        build_id=fact_bundle.build_id,
        subject_id=fact_bundle.subject_id,
        facts=kept,
        source_refs=canonical_refs,
    )


__all__ = ["trim"]
