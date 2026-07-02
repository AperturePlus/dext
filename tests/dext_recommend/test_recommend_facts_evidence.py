from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids


def test_dedupe_entity_ids_preserves_order():
    assert dedupe_entity_ids(["e2", "e1", "e2", "e3", "e1"]) == ("e2", "e1", "e3")


def test_dedupe_entity_ids_empty():
    assert dedupe_entity_ids([]) == ()
    assert dedupe_entity_ids(None) == ()


def test_chunk_entity_ids_below_limit():
    assert chunk_entity_ids(["e1", "e2"], 999) == (("e1", "e2"),)


def test_chunk_entity_ids_splits_at_limit():
    ids = [f"e{i}" for i in range(5)]
    chunks = chunk_entity_ids(ids, 2)
    assert chunks == (("e0", "e1"), ("e2", "e3"), ("e4",))


def test_chunk_entity_ids_empty():
    assert chunk_entity_ids([], 999) == ()


def test_chunk_entity_ids_limit_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        chunk_entity_ids(["e1"], 0)


from dext_grounded import FactItem, SourceRef
from dext_grounded.content import ContentClass

from dext_recommend.facts.evidence import (
    WEAK_EXPLANATION_TEXT, build_fact_items, select_publication_snippets,
    select_statement_snippets,
)


def _sref(key: str) -> SourceRef:
    return SourceRef(
        doc_path="catalog", heading_path=key, chunk_hash="", quote_or_summary=key,
    )


def test_select_statement_snippets_truncates_and_caps():
    statements = [
        {"id": f"s{i}", "normalized_text": f"statement {i}."} for i in range(10)
    ]
    out = select_statement_snippets(statements, max_chars=20, max_count=3)
    assert len(out) <= 3
    assert sum(len(s) for s in out) <= 20 + 3  # allowance for per-item joins


def test_select_statement_snippets_empty():
    assert select_statement_snippets([]) == ()


def test_select_publication_snippets_excludes_needs_review():
    mentions = [
        {"id": "m1", "observation_id": "o1", "normalized_text": "ok", "needs_review": 0},
        {"id": "m2", "observation_id": "o1", "normalized_text": "review", "needs_review": 1},
    ]
    out = select_publication_snippets(mentions)
    assert out == ("ok",)


def test_build_fact_items_fact_with_source_ref():
    sref = _sref("catalog:entity:e1:build:b1")
    items = build_fact_items(
        identity={"display_name": "A", "entity_id": "e1"},
        eligibility={"master_eligibility": "confirmed", "phd_eligibility": "unknown"},
        research_statements=("works on NLP",),
        approved_topics=("NLP",),
        publications=("Paper A",),
        source_refs_by_field={"display_name": (sref,)},
    )
    by_field = {i.field: i for i in items}
    assert by_field["display_name"].content_class is ContentClass.FACT
    assert by_field["display_name"].source_refs == (sref,)


def test_build_fact_items_uncertain_without_source_ref():
    items = build_fact_items(
        identity={"display_name": "A", "entity_id": "e1"},
        eligibility={"master_eligibility": "confirmed", "phd_eligibility": "unknown"},
        research_statements=("works on NLP",),
        approved_topics=("NLP",),
        publications=("Paper A",),
        source_refs_by_field={},
    )
    for item in items:
        # no source ref => must be uncertain (enforced by FactItem.__post_init__)
        assert item.content_class is ContentClass.UNCERTAIN


def test_weak_explanation_text_is_nonempty():
    assert isinstance(WEAK_EXPLANATION_TEXT, str) and WEAK_EXPLANATION_TEXT.strip()
