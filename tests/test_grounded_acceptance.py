"""Acceptance for grounded-generation spec §9 (single-module slice).

Verifies the contract surface a consumer (dext_recommend phase 6) will rely on:
FactBundle trimming hook, LLMGenerationPort, CitationValidator, SafetyGuard
compose into one grounded pipeline that never returns un-cited output.
"""
from __future__ import annotations

from dext_grounded import (
    Claim, CitationValidator, ContentClass, FactBundle, FactItem,
    FakeLLMGenerationPort, GenerationResult, LLMGenerationPort, SafetyGuard,
    SourceRef, StudentContext, UserContextRef,
)


def test_grounded_pipeline_drops_fabricated_and_blocks_probability():
    # 1. assemble a fact bundle with one real source ref
    real_ref = SourceRef(
        doc_path="catalog://entity/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="works on RAG",
    )
    bundle = FactBundle(
        build_id="b1", subject_id="p1",
        facts=[FactItem(
            field="research_statement", value="works on RAG",
            content_class=ContentClass.FACT, source_refs=[real_ref],
        )],
        source_refs=[real_ref],
    )

    # 2. LLM returns a mix: a grounded fact, a fabricated ref, and a probability claim
    fabricated = SourceRef(
        doc_path="catalog://entity/p1", heading_path="rs",
        chunk_hash="FAKE", quote_or_summary="made up",
    )
    llm_result = GenerationResult(
        output="professor works on RAG; admission probability 90%",
        claims=[
            Claim(text="professor works on RAG", content_class=ContentClass.FACT,
                  fact_refs=[real_ref]),
            Claim(text="made up fact", content_class=ContentClass.FACT,
                  fact_refs=[fabricated]),
            Claim(text="录取概率 90%", content_class=ContentClass.ADVICE),
        ],
    )
    port: LLMGenerationPort = FakeLLMGenerationPort(llm_result)

    # 3. run generate → validate → safety
    raw = port.generate(
        system_prompt_id="match-analysis-v1", user_inputs={},
        fact_bundle=bundle, student_context=StudentContext(),
        json_schema=None, generation_profile_version="gen-v1.0",
    )
    validated = CitationValidator().validate(raw, bundle, StudentContext())
    final = SafetyGuard().inspect(validated, domain="recommend", include_contacts=False)

    # 4. only the grounded fact survives; fabricated + probability dropped
    assert len(final.claims) == 1
    assert final.claims[0].text == "professor works on RAG"
    codes = {w.code for w in final.warnings}
    assert "fabricated_ref" in codes
    assert "no_probability_claim" in codes


def test_advice_with_user_context_refs_validated_against_student_context():
    real_ref = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="q",
    )
    bundle = FactBundle(
        build_id="b", subject_id="p1", facts=[], source_refs=[real_ref],
    )
    # student context HAS research_interests → ref kept
    ctx = StudentContext(research_interests=["NLP"])
    claim = Claim(
        text="highlight your NLP background", content_class=ContentClass.ADVICE,
        user_context_ref=UserContextRef(
            field="research_interests", value_bucket="advanced", quote_or_summary="NLP",
        ),
    )
    raw = GenerationResult(output="advice", claims=[claim])
    validated = CitationValidator().validate(raw, bundle, ctx)
    assert validated.claims[0].user_context_ref is not None
    assert validated.warnings == []


def test_all_dropped_yields_no_grounded_output_marker():
    real_ref = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="c1", quote_or_summary="q",
    )
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[], source_refs=[real_ref])
    fabricated = SourceRef(
        doc_path="catalog://e/p1", heading_path="rs",
        chunk_hash="FAKE", quote_or_summary="made up",
    )
    raw = GenerationResult(
        output="x",
        claims=[Claim(text="m", content_class=ContentClass.FACT, fact_refs=[fabricated])],
    )
    validated = CitationValidator().validate(raw, bundle, StudentContext())
    assert validated.claims == []
    assert any(w.code == "no_grounded_output" for w in validated.warnings)
