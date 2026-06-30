from __future__ import annotations

from dext_grounded import (
    Claim, CitationValidator, ContentClass, FactBundle, FactItem,
    GenerationResult, SourceRef, StudentContext, UserContextRef,
)


def _ref(doc: str, chunk: str = "c") -> SourceRef:
    return SourceRef(doc_path=doc, heading_path="h", chunk_hash=chunk, quote_or_summary="q")


def _bundle(refs: list[SourceRef]) -> FactBundle:
    return FactBundle(build_id="b", subject_id="s", facts=[], source_refs=refs)


def test_fact_claim_with_ref_in_bundle_kept():
    r = _ref("catalog://e/p1")
    bundle = _bundle([r])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[r])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert result.claims == [claim]
    assert result.warnings == []


def test_fact_claim_with_fabricated_ref_dropped():
    r_real = _ref("catalog://e/p1")
    r_fake = _ref("catalog://e/p2", chunk="nope")
    bundle = _bundle([r_real])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[r_fake])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert result.claims == []
    assert any(w.code == "fabricated_ref" for w in result.warnings)


def test_advice_claim_referencing_missing_student_field_drops_user_context():
    r = _ref("catalog://e/p1")
    bundle = _bundle([r])
    claim = Claim(
        text="t",
        content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    # StudentContext has NO research_interests set
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    # claim kept but user_context_ref stripped, warning emitted
    assert len(result.claims) == 1
    assert result.claims[0].user_context_ref is None
    assert any(w.code == "fabricated_user_context" for w in result.warnings)


def test_advice_claim_referencing_present_student_field_kept():
    r = _ref("catalog://e/p1")
    bundle = _bundle([r])
    claim = Claim(
        text="t",
        content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    ctx = StudentContext(research_interests=["NLP"])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, ctx,
    )
    assert result.claims[0].user_context_ref is not None
    assert result.warnings == []


def test_fact_claim_missing_refs_downgraded_to_uncertain():
    # fact claim with no refs at all → Claim.validate flags it, CitationValidator downgrades
    bundle = _bundle([_ref("catalog://e/p1")])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert any(w.code == "fact_ref_missing" for w in result.warnings)
    # downgraded to uncertain
    assert result.claims[0].content_class == ContentClass.UNCERTAIN


def test_all_claims_dropped_returns_no_grounded_output_marker():
    r_real = _ref("catalog://e/p1")
    bundle = _bundle([r_real])
    fake_claim = Claim(
        text="t", content_class=ContentClass.FACT, fact_refs=[_ref("catalog://e/p2", "x")],
    )
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[fake_claim]), bundle, StudentContext(),
    )
    assert result.claims == []
    assert any(w.code == "no_grounded_output" for w in result.warnings)
