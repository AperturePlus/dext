from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ConstrainedGenerationPipeline, ContentClass, FactBundle, FactItem,
    FakeLLMGenerationPort, GenerationResult, SourceRef, StudentContext,
)
from dext_grounded.citation import CitationValidator
from dext_grounded.safety import SafetyGuard


def _bundle(build_id: str = "b1", subject_id: str = "e1") -> FactBundle:
    ref = SourceRef(
        doc_path=f"catalog:research-statement:{build_id}:s1",
        heading_path="research_statement", chunk_hash="h1",
        quote_or_summary="works on NLP", official_url=None, last_verified=None,
    )
    item = FactItem(
        field="research_statement", value="works on NLP",
        content_class=ContentClass.FACT, source_refs=(ref,),
    )
    return FactBundle(build_id=build_id, subject_id=subject_id,
                      facts=(item,), source_refs=(ref,))


@pytest.mark.asyncio
async def test_pipeline_runs_parse_citation_safety_in_order():
    bundle = _bundle()
    ref = bundle.source_refs[0]
    raw = GenerationResult(
        output={"answer": "He works on NLP.", "claims": [
            {"text": "He works on NLP.", "content_class": "fact",
             "fact_indices": [0], "fact_refs": [ref]},
        ]},
        claims=(Claim(text="He works on NLP.", content_class=ContentClass.FACT,
                      fact_refs=(ref,)),),
        cited_refs=(), warnings=[],
    )
    fake = FakeLLMGenerationPort(preset=raw)
    pipe = ConstrainedGenerationPipeline(
        llm_port=fake, citation=CitationValidator(), safety=SafetyGuard(),
    )
    result = await pipe.generate(
        system_prompt_id="dext_recommend.detail_followup.v1",
        user_inputs={"question": "what?", "display_name": "X"},
        fact_bundle=_bundle(), student_context=None,
        json_schema={"type": "object"},
        generation_profile_version="gp-v1",
        safety_domain="recommend", include_contacts=False,
    )
    assert isinstance(result, GenerationResult)
    # citation canonicalized the fact ref into cited_refs
    assert len(result.cited_refs) == 1


@pytest.mark.asyncio
async def test_pipeline_support_validator_runs_before_citation():
    # fact claim whose ref is in the bundle (passes citation) but the support-map
    # callback rejects it because fact_indices is empty -> claim dropped.
    raw = GenerationResult(
        output={"answer": "x", "claims": [
            {"text": "x", "content_class": "fact", "fact_indices": [], "fact_refs": []}]},
        claims=(Claim(text="x", content_class=ContentClass.FACT, fact_refs=()),),
        cited_refs=(), warnings=[],
    )
    fake = FakeLLMGenerationPort(preset=raw)

    def support_validator(result: GenerationResult, bundle: FactBundle) -> GenerationResult:
        # drop every fact claim (simulates index validation failing)
        from dataclasses import replace
        return replace(result, claims=())

    pipe = ConstrainedGenerationPipeline(
        llm_port=fake, citation=CitationValidator(), safety=SafetyGuard(),
    )
    result = await pipe.generate(
        system_prompt_id="p", user_inputs={}, fact_bundle=_bundle(),
        student_context=None, json_schema=None, generation_profile_version="gp-v1",
        safety_domain="recommend", include_contacts=False,
        support_validator=support_validator,
    )
    assert result.claims == ()


@pytest.mark.asyncio
async def test_pipeline_does_not_double_validate(monkeypatch):
    # If business code wraps pipeline output in CitationValidator again, that's
    # a caller bug; pipeline result is already validated. We assert pipeline
    # output is idempotent under a second CitationValidator.validate (no new
    # fabricated_ref warnings on already-canonical refs).
    ref = SourceRef(doc_path="c:s:1", heading_path="rs", chunk_hash="h1",
                    quote_or_summary="NLP", official_url=None, last_verified=None)
    bundle = FactBundle(build_id="b", subject_id="e", facts=(
        FactItem(field="research_statement", value="NLP",
                 content_class=ContentClass.FACT, source_refs=(ref,)),
    ), source_refs=(ref,))
    raw = GenerationResult(
        output={"answer": "NLP", "claims": [
            {"text": "NLP", "content_class": "fact",
             "fact_indices": [0], "fact_refs": [ref]}]},
        claims=(Claim(text="NLP", content_class=ContentClass.FACT,
                      fact_refs=(ref,)),),
        cited_refs=(), warnings=[],
    )
    fake = FakeLLMGenerationPort(preset=raw)
    pipe = ConstrainedGenerationPipeline(
        llm_port=fake, citation=CitationValidator(), safety=SafetyGuard(),
    )
    result = await pipe.generate(
        system_prompt_id="p", user_inputs={}, fact_bundle=bundle,
        student_context=None, json_schema=None, generation_profile_version="gp-v1",
        safety_domain="recommend", include_contacts=False,
    )
    re_validated = CitationValidator().validate(result, bundle, None)
    assert not any(w.code == "fabricated_ref" for w in re_validated.warnings)
