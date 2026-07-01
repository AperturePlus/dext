"""Unit tests for the trimmer hook (grounded-generation spec §5.1).

trim(fact_bundle, token_budget, query_terms) -> FactBundle — pure function.
Behavior:
- KEEP query-hit items always (regardless of budget).
- DROP non-hit items when over budget.
- REJECT contact-bearing items (value or source_ref quote matches a contact regex).
- NEVER drop source_refs from a kept FactItem.
- Keep only canonical source_refs used by surviving facts.
- token_budget<=0 keeps ONLY query-hit items.
"""
from __future__ import annotations

import pytest

from dext_grounded import (
    ContentClass, FactBundle, FactItem, SourceRef, trim,
)


def _ref(quote: str = "q", chunk: str = "c1") -> SourceRef:
    return SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash=chunk, quote_or_summary=quote,
    )


def _item(field: str, value: str, *, refs=None, cls=ContentClass.FACT) -> FactItem:
    return FactItem(
        field=field, value=value, content_class=cls,
        source_refs=refs if refs is not None else [],
    )


# (a) keeps query-hit items ------------------------------------------------

def test_trim_keeps_query_hits():
    hit = _item("research_statement", "works on RAG", refs=[_ref()])
    miss = _item("bio", "lorem ipsum", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit, miss], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=128, query_terms={"RAG"})
    fields = {f.field for f in trimmed.facts}
    assert "research_statement" in fields


# (b) drops non-hit items when over budget ---------------------------------

def test_trim_drops_non_hit_when_over_budget():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    long_miss = _item("bio", "x" * 10000, refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit, long_miss], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=128, query_terms={"RAG"})
    assert any(f.field == "research_statement" for f in trimmed.facts)
    assert not any(f.field == "bio" for f in trimmed.facts)


# (c) never drops source_refs from kept items ------------------------------

def test_trim_preserves_source_refs_on_kept_items():
    real = _ref("works on RAG")
    hit = _item("research_statement", "RAG", refs=[real])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit], source_refs=[real])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    kept = [f for f in trimmed.facts if f.field == "research_statement"]
    assert len(kept) == 1
    assert kept[0].source_refs == (real,)


# (d) rejects contact-bearing items ----------------------------------------

def test_trim_rejects_contact_in_value():
    real = _ref("RAG info")
    bad = _item("research_statement", "email me foo@bar.com about RAG", refs=[real])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[bad], source_refs=[real])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    assert not any(f.field == "research_statement" for f in trimmed.facts)


def test_trim_rejects_contact_in_source_ref_quote():
    real = _ref("contact foo@bar.com")  # quote carries a contact
    bad = _item("research_statement", "RAG works", refs=[real])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[bad], source_refs=[real])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    assert not any(f.field == "research_statement" for f in trimmed.facts)
    assert not trimmed.source_refs


def test_trim_rejects_phone_contact():
    bad = _item("bio", "call me 13800000000 about RAG", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[bad], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    assert not any(f.field == "bio" for f in trimmed.facts)


# (e) preserves bundle source_refs canonical set ---------------------------

def test_trim_preserves_only_used_bundle_source_refs():
    real = _ref("RAG")
    unused = _ref("unused", chunk="c2")
    hit = _item("research_statement", "RAG", refs=[real])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit], source_refs=[real, unused])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    assert trimmed.source_refs == (real,)


def test_trim_rejects_contact_in_source_ref_url():
    ref = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs", chunk_hash="c1",
        quote_or_summary="RAG", official_url="https://example.test/foo@bar.com",
    )
    item = _item("research_statement", "RAG", refs=[ref])
    trimmed = trim(
        FactBundle(build_id="b", subject_id="p1", facts=[item], source_refs=[ref]),
        token_budget=4096,
        query_terms={"RAG"},
    )
    assert not trimmed.facts
    assert not trimmed.source_refs


# (f) token_budget<=0 keeps only hits --------------------------------------

def test_trim_zero_budget_keeps_only_hits():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    miss = _item("bio", "some text", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit, miss], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=0, query_terms={"RAG"})
    assert any(f.field == "research_statement" for f in trimmed.facts)
    assert not any(f.field == "bio" for f in trimmed.facts)


def test_trim_negative_budget_keeps_only_hits():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    miss = _item("bio", "some text", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit, miss], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=-1, query_terms={"RAG"})
    assert any(f.field == "research_statement" for f in trimmed.facts)
    assert not any(f.field == "bio" for f in trimmed.facts)


# (g) does not mutate input -------------------------------------------------

def test_trim_does_not_mutate_input():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    miss = _item("bio", "x" * 10000, refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit, miss], source_refs=[_ref()])
    original_facts = bundle.facts
    _ = trim(bundle, token_budget=128, query_terms={"RAG"})
    assert bundle.facts == original_facts
    assert len(bundle.facts) == 2


# (h) query_terms as list (iterable) ---------------------------------------

def test_trim_accepts_list_query_terms():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=4096, query_terms=["rag"])
    assert any(f.field == "research_statement" for f in trimmed.facts)


# (i) case-insensitive query match -----------------------------------------

def test_trim_query_match_case_insensitive():
    hit = _item("research_statement", "Works On RAG", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=4096, query_terms={"rag"})
    assert any(f.field == "research_statement" for f in trimmed.facts)


# (j) returns same build_id / subject_id -----------------------------------

def test_trim_preserves_bundle_identity():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    bundle = FactBundle(build_id="b1", subject_id="p1",
                        facts=[hit], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    assert trimmed.build_id == "b1"
    assert trimmed.subject_id == "p1"


# (k) non-hit item within budget survives ----------------------------------

def test_trim_keeps_non_hit_within_budget():
    hit = _item("research_statement", "RAG", refs=[_ref()])
    short_miss = _item("bio", "short bio", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[hit, short_miss], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    fields = {f.field for f in trimmed.facts}
    assert "research_statement" in fields
    assert "bio" in fields


# (l) empty query_terms drops everything except within-budget ---------------

def test_trim_empty_query_terms_budget_allows_items():
    item = _item("research_statement", "short text", refs=[_ref()])
    bundle = FactBundle(build_id="b", subject_id="p1",
                        facts=[item], source_refs=[_ref()])
    trimmed = trim(bundle, token_budget=4096, query_terms=set())
    assert any(f.field == "research_statement" for f in trimmed.facts)
