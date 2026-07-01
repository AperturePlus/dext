"""Acceptance for grounded-generation spec §9 (single-module slice).

Verifies the contract surface a consumer (dext_recommend phase 6) will rely on:
FactBundle trimming hook, LLMGenerationPort, CitationValidator, SafetyGuard
compose into one grounded pipeline that never returns un-cited output.
"""
from __future__ import annotations

import pytest

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
    assert not validated.warnings


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
    assert not validated.claims
    assert validated.output == ""
    assert any(w.code == "no_grounded_output" for w in validated.warnings)


# ---- §6 attack tests: final output must be sanitized, not just claims ----

def test_safety_probability_claim_removed_from_output():
    claim = Claim(text="录取概率 90%", content_class=ContentClass.ADVICE)
    res = SafetyGuard().inspect(
        GenerationResult(output="录取概率 90%", claims=[claim]),
        domain="recommend", include_contacts=False,
    )
    assert "录取概率" not in (res.output if isinstance(res.output, str) else "")
    assert any(w.code == "no_probability_claim" for w in res.warnings)


def test_safety_unsafe_advice_blanks_output():
    claim = Claim(text="我可以代做这个项目", content_class=ContentClass.ADVICE)
    res = SafetyGuard().inspect(
        GenerationResult(output="我可以代做这个项目", claims=[claim]),
        domain="competition", include_contacts=False,
    )
    # output must NOT still carry the unsafe advice verbatim
    assert "代做" not in (res.output if isinstance(res.output, str) else "")
    assert any(w.code == "unsafe_advice" for w in res.warnings)


def test_safety_unauthorized_contact_stripped_from_output():
    res = SafetyGuard().inspect(
        GenerationResult(output="联系：foo@bar.com 或 13800000000", claims=[]),
        domain="recommend", include_contacts=False,
    )
    assert "foo@bar.com" not in res.output
    assert "13800000000" not in res.output
    assert any(w.code == "unauthorized_contact" for w in res.warnings)


# ---- §5.2 advice fact_refs + canonical identity ----

def test_advice_with_fabricated_fact_ref_dropped():
    real = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="works on RAG")
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[], source_refs=[real])
    fake = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="FAKED SUMMARY")  # same key, faked quote
    claim = Claim(text="advice grounded in RAG", content_class=ContentClass.ADVICE,
                  fact_refs=[fake])
    res = CitationValidator().validate(
        GenerationResult(output="x", claims=[claim]), bundle, StudentContext(),
    )
    assert all(r.quote_or_summary != "FAKED SUMMARY" for r in res.cited_refs)
    assert any(w.code == "fabricated_ref" for w in res.warnings)


# ---- §3 deep immutability ----

def test_fact_bundle_facts_is_readonly():
    bundle = FactBundle(
        build_id="b", subject_id="s", facts=[
            FactItem(field="f", value="v", content_class=ContentClass.UNCERTAIN,
                     source_refs=[]),
        ], source_refs=[],
    )
    with pytest.raises((AttributeError, TypeError)):
        bundle.facts.append(FactItem(field="x", value="y",
                                     content_class=ContentClass.UNCERTAIN, source_refs=[]))


def test_claim_fact_refs_is_readonly():
    ref = SourceRef(doc_path="p", heading_path="h", chunk_hash="c", quote_or_summary="q")
    claim = Claim(text="t", content_class=ContentClass.FACT, fact_refs=[ref])
    with pytest.raises((AttributeError, TypeError)):
        claim.fact_refs.append(ref)


def test_generation_result_claims_is_readonly():
    res = GenerationResult(output="x")
    with pytest.raises((AttributeError, TypeError)):
        res.claims.append(Claim(text="t", content_class=ContentClass.ADVICE))


# ---- §5.1 trimmer hook ----

def test_trim_keeps_query_hits_and_source_refs():
    from dext_grounded import trim  # NEW: R0 must expose the trim hook
    real = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="works on RAG")
    hit = FactItem(field="research_statement", value="RAG",
                   content_class=ContentClass.FACT, source_refs=[real])
    miss = FactItem(field="bio", value="x" * 10000,
                    content_class=ContentClass.UNCERTAIN, source_refs=[])
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[hit, miss],
                        source_refs=[real])
    trimmed = trim(bundle, token_budget=128, query_terms={"RAG"})
    assert any(f.field == "research_statement" for f in trimmed.facts)
    assert all(f.source_refs for f in trimmed.facts if f.content_class == ContentClass.FACT)


# ---- §9 eval contract (precision / no-probability-claim / version binding) ----

def test_eval_contract_samples_bind_profile_version():
    from dext_grounded.eval import describe_metrics, load_acceptance_samples
    metrics = describe_metrics()
    assert "grounded_precision" in metrics and "no_probability_claim_rate" in metrics
    samples = load_acceptance_samples()
    assert samples  # non-empty built-in set
    for s in samples:
        assert s.generation_profile_version
        assert s.grounded_rules_manifest_hash


def test_trim_rejects_contact_bearing_fact_item():
    from dext_grounded import trim
    real = SourceRef(doc_path="catalog://e/p1", heading_path="rs",
                     chunk_hash="c1", quote_or_summary="RAG contact foo@bar.com")
    # FactItem whose value carries a contact (email) — must NOT survive into trimmed bundle
    bad = FactItem(field="research_statement", value="email me foo@bar.com about RAG",
                  content_class=ContentClass.FACT, source_refs=[real])
    bundle = FactBundle(build_id="b", subject_id="p1", facts=[bad], source_refs=[real])
    trimmed = trim(bundle, token_budget=4096, query_terms={"RAG"})
    assert not any(f is bad or f.field == "research_statement" for f in trimmed.facts)
    assert not trimmed.source_refs


def test_nested_structured_output_is_sanitized_recursively():
    result = SafetyGuard().inspect(
        GenerationResult(
            output={
                "summary": "录取概率 90%",
                "details": ["foo@bar.com", {"phone": "13800000000"}],
            },
            claims=[],
        ),
        domain="recommend",
        include_contacts=False,
    )
    rendered = str(result.output)
    assert "录取概率" not in rendered
    assert "foo@bar.com" not in rendered
    assert "13800000000" not in rendered
    assert {warning.code for warning in result.warnings} >= {
        "no_probability_claim",
        "unauthorized_contact",
    }


def test_structured_output_only_unsafe_advice_is_rejected():
    result = SafetyGuard().inspect(
        GenerationResult(output={"plan": ["我可以代做这个项目"]}, claims=[]),
        domain="competition",
    )
    assert result.output == {}
    assert any(warning.code == "unsafe_advice" for warning in result.warnings)
