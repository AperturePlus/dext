# tests/test_grounded_fact_bundle.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import ContentClass, FactBundle, FactItem, SourceRef


def _ref(doc: str = "catalog://e/p1") -> SourceRef:
    return SourceRef(
        doc_path=doc, heading_path="rs", chunk_hash="h", quote_or_summary="q",
    )


def test_fact_item_with_source_refs_is_fact():
    item = FactItem(
        field="research_statement",
        value="works on RAG",
        content_class=ContentClass.FACT,
        source_refs=[_ref()],
    )
    assert item.content_class == ContentClass.FACT


def test_fact_item_without_source_refs_must_be_uncertain():
    item = FactItem(
        field="research_statement",
        value="works on RAG",
        content_class=ContentClass.FACT,   # wrongly declared
        source_refs=[],
    )
    # post_init must force uncertain
    assert item.content_class == ContentClass.UNCERTAIN


def test_fact_item_is_frozen():
    item = FactItem(field="f", value="v", content_class=ContentClass.ADVICE, source_refs=[])
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.value = "other"


def test_fact_bundle_is_immutable_snapshot():
    item = FactItem(
        field="eligibility", value="confirmed",
        content_class=ContentClass.FACT, source_refs=[_ref()],
    )
    bundle = FactBundle(
        build_id="build-1",
        subject_id="prof-001",
        facts=[item],
        source_refs=[_ref()],
    )
    assert bundle.build_id == "build-1"
    assert bundle.subject_id == "prof-001"
    assert len(bundle.facts) == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        bundle.build_id = "build-2"


def test_fact_bundle_source_refs_lookup():
    r1 = _ref("catalog://e/p1")
    r2 = _ref("catalog://e/p2")
    bundle = FactBundle(
        build_id="b", subject_id="s", facts=[], source_refs=[r1, r2],
    )
    # source_refs is the canonical lookup set
    hashes = {ref.chunk_hash for ref in bundle.source_refs}
    assert hashes == {"h"}
