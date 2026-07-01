# tests/test_grounded_source_ref.py
from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import SourceRef, UserContextRef


def test_source_ref_minimum_fields():
    ref = SourceRef(
        doc_path="catalog://entity/prof-001",
        heading_path="research_statements",
        chunk_hash="sha256:abc",
        quote_or_summary="works on retrieval-augmented generation",
    )
    assert ref.official_url is None
    assert ref.last_verified is None


def test_source_ref_is_frozen():
    ref = SourceRef(
        doc_path="p", heading_path="h", chunk_hash="c", quote_or_summary="q",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.doc_path = "other"


def test_source_ref_quote_length_capped():
    long_quote = "x" * 600
    ref = SourceRef(
        doc_path="p", heading_path="h", chunk_hash="c", quote_or_summary=long_quote,
    )
    assert len(ref.quote_or_summary) <= 500


def test_user_context_ref_fields():
    ref = UserContextRef(
        field="research_interests",
        value_bucket="advanced",
        quote_or_summary="interested in NLP and IR",
    )
    assert ref.field == "research_interests"
    assert ref.value_bucket == "advanced"


def test_user_context_ref_value_bucket_never_raw():
    # value_bucket is a redacted bucket; raw GPA/rank forbidden
    ref = UserContextRef(
        field="gpa_bucket", value_bucket="top10", quote_or_summary="high GPA",
    )
    assert ref.value_bucket == "top10"
