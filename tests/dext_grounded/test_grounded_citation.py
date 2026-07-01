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


def test_no_duplicate_fact_ref_missing_warning():
    bundle = _bundle([_ref("catalog://e/p1")])
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    count = sum(1 for w in result.warnings if w.code == "fact_ref_missing")
    assert count == 1, f"expected exactly one fact_ref_missing, got {count}"


def test_no_duplicate_uncertain_claim_with_refs_warning():
    bundle = _bundle([_ref("catalog://e/p1")])
    claim = Claim(text="t", content_class=ContentClass.UNCERTAIN, fact_refs=[_ref("catalog://e/p1")])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    count = sum(1 for w in result.warnings if w.code == "uncertain_claim_with_refs")
    assert count == 1, f"expected exactly one uncertain_claim_with_refs, got {count}"


# ---- §5.2 canonical identity: last_verified is NOT a discriminator ----

def test_bundle_ref_with_last_verified_resolves_canonical_when_llm_omits_it():
    # The bundle carries authoritative last_verified; the LLM has no way to know
    # it, so an otherwise-identical LLM-reported ref must still resolve to the
    # canonical bundle object (spec §5.2 — last_verified is NOT a discriminator).
    canonical = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="works on RAG",
        official_url="https://example.edu/p1",
        last_verified="2026-06-30",
    )
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[], source_refs=[canonical])
    # LLM-reported ref: same quote + url, but OMITS last_verified
    llm_ref = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="works on RAG",
        official_url="https://example.edu/p1",
    )
    claim = Claim(text="professor works on RAG", content_class=ContentClass.FACT,
                  fact_refs=[llm_ref])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    # NOT fabricated — resolves to the canonical bundle object carrying last_verified
    assert not any(w.code == "fabricated_ref" for w in result.warnings)
    assert len(result.cited_refs) == 1
    assert result.cited_refs[0].last_verified == "2026-06-30"


def test_fact_claim_same_triple_different_quote_still_flagged_fabricated():
    # FACT-path 冒充 case: same (doc_path, heading_path, chunk_hash) triple but
    # a different quote_or_summary → impersonating a real ref → fabricated_ref.
    canonical = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="works on RAG",
        official_url="https://example.edu/p1",
        last_verified="2026-06-30",
    )
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[], source_refs=[canonical])
    impersonating = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="FAKED SUMMARY",  # same triple, faked quote
        official_url="https://example.edu/p1",
    )
    claim = Claim(text="fake grounded fact", content_class=ContentClass.FACT,
                  fact_refs=[impersonating])
    result = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert result.claims == []
    assert any(w.code == "fabricated_ref" for w in result.warnings)
