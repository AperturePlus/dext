from __future__ import annotations

import dataclasses

import pytest

from dext_grounded import (
    Claim, ContentClass, GenerationResult, GenerationWarning, SourceRef, UserContextRef,
)


def _ref(doc: str = "catalog://e/p1") -> SourceRef:
    return SourceRef(doc_path=doc, heading_path="h", chunk_hash="c", quote_or_summary="q")


def test_claim_fact_requires_refs_or_flags_warning():
    claim = Claim(text="professor works on RAG", content_class=ContentClass.FACT, fact_refs=[])
    warnings = claim.validate()
    assert any(w.code == "fact_ref_missing" for w in warnings)


def test_claim_advice_with_user_context_ref_ok():
    claim = Claim(
        text="consider highlighting your NLP project",
        content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    assert not claim.validate()


def test_claim_uncertain_must_not_carry_fact_refs():
    claim = Claim(
        text="maybe accepts PhD students",
        content_class=ContentClass.UNCERTAIN,
        fact_refs=[_ref()],
    )
    warnings = claim.validate()
    assert any(w.code == "uncertain_claim_with_refs" for w in warnings)


def test_claim_is_frozen():
    claim = Claim(text="t", content_class=ContentClass.ADVICE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.text = "other"


def test_generation_result_defaults():
    res = GenerationResult(output="hello")
    assert not res.claims
    assert not res.cited_refs
    assert not res.warnings
    assert res.output == "hello"


def test_generation_warning_minimum():
    w = GenerationWarning(code="fabricated_ref", message="made up")
    assert w.claim_text is None
