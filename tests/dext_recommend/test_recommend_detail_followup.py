from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ContentClass, FactBundle, FactItem, FakeLLMGenerationPort,
    GenerationResult, GenerationWarning, SourceRef,
)
from dext_recommend import (
    ConversationContext, ConversationSummary, ProfessorDetail, RecommendRequest,
)
from tests.dext_recommend.test_recommend_conversation_dispatch import _dispatcher


def _detail() -> ProfessorDetail:
    ref = SourceRef(
        doc_path="catalog:research:b1:s1", heading_path="research_statement",
        chunk_hash="h1", quote_or_summary="works on NLP",
    )
    bundle = FactBundle(
        build_id="b1", subject_id="e1",
        facts=(FactItem(field="research_statement", value="works on NLP",
                        content_class=ContentClass.FACT, source_refs=(ref,)),),
        source_refs=(ref,),
    )
    return ProfessorDetail(
        build_id="b1", profile_hash="ph", entity_id="e1", display_name="Prof X",
        university="U", org_units=("CS",), title="Prof", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="confirmed",
        role_status="included", profile_url=None, research_statements=("NLP",),
        approved_topics=("t1",), selected_publication_mentions=(), bio_snippets=(),
        source_urls=(), provenance_refs=(ref,), quality_findings=(), risk_flags=(),
        fact_bundle=bundle,
    )


def _request(anchor: str = "e1") -> RecommendRequest:
    return RecommendRequest(
        query_text="What does this professor study?",
        conversation_context=ConversationContext(
            intent_source="explicit", intent="detail_followup",
            anchor_entity_id=anchor, session_id="s1", turn_id="t1",
        ),
    )


class SequentialLLMGenerationPort:
    def __init__(self, *presets: GenerationResult) -> None:
        self._presets = list(presets)
        self.calls: list[dict] = []

    async def generate(
        self,
        system_prompt_id,
        user_inputs,
        fact_bundle,
        student_context,
        json_schema,
        generation_profile_version,
    ):
        self.calls.append({
            "system_prompt_id": system_prompt_id,
            "user_inputs": user_inputs,
            "fact_bundle": fact_bundle,
            "student_context": student_context,
            "json_schema": json_schema,
            "generation_profile_version": generation_profile_version,
        })
        if not self._presets:
            raise AssertionError("unexpected LLM generation call")
        return self._presets.pop(0)


@pytest.mark.asyncio
async def test_detail_followup_returns_grounded_answer():
    detail = _detail()
    ref = detail.fact_bundle.source_refs[0]
    raw = GenerationResult(
        output={"answer": "Works on NLP.", "claims": [{
            "text": "Works on NLP.", "content_class": "fact",
            "fact_indices": [0], "fact_refs": [ref],
        }]},
        claims=(Claim("Works on NLP.", ContentClass.FACT, (ref,)),),
    )
    result = await _dispatcher(
        FakeLLMGenerationPort(raw), details={"e1": detail}
    ).dispatch(_request())
    assert result.kind == "detail_followup"
    assert result.detail_followup.answer == "Works on NLP."
    assert result.detail_followup.cited_refs == (ref,)


@pytest.mark.asyncio
async def test_detail_followup_rejects_unrelated_fact_index():
    detail = _detail()
    ref = detail.fact_bundle.source_refs[0]
    raw = GenerationResult(
        output={"answer": "Unsupported", "claims": [{
            "text": "Unsupported", "content_class": "fact",
            "fact_indices": [99], "fact_refs": [ref],
        }]},
        claims=(Claim("Unsupported", ContentClass.FACT, (ref,)),),
    )
    result = await _dispatcher(
        FakeLLMGenerationPort(raw), details={"e1": detail}
    ).dispatch(_request())
    assert result.kind == "error"
    assert any(w.code == "no_grounded_output" for w in result.issues)


@pytest.mark.asyncio
async def test_detail_followup_missing_anchor_is_non_disclosing_error():
    result = await _dispatcher(
        FakeLLMGenerationPort(GenerationResult(output={})), details={"e1": _detail()}
    ).dispatch(_request("missing"))
    assert result.kind == "error"
    assert any(w.code == "anchor_not_in_active_build" for w in result.issues)


@pytest.mark.asyncio
async def test_detail_followup_content_policy_refuses_before_llm_call():
    llm = FakeLLMGenerationPort(GenerationResult(output={}))
    req = RecommendRequest(
        query_text="帮我骂导师，说导师垃圾",
        conversation_context=ConversationContext(
            intent_source="explicit", intent="detail_followup",
            anchor_entity_id="e1", session_id="s1", turn_id="t1",
        ),
    )

    result = await _dispatcher(llm, details={"e1": _detail()}).dispatch(req)

    assert result.kind == "error"
    assert any(w.code == "content_policy_refusal" for w in result.issues)
    assert llm.calls == []


@pytest.mark.asyncio
async def test_implicit_classifier_unusable_with_anchor_falls_back_to_detail_followup():
    detail = _detail()
    ref = detail.fact_bundle.source_refs[0]
    classify_failure = GenerationResult(
        output={},
        warnings=[GenerationWarning("schema_validation_failed", "bad schema")],
    )
    detail_success = GenerationResult(
        output={"answer": "These experiences are meaningful because they show NLP work.", "claims": [{
            "text": "These experiences show NLP work.",
            "content_class": "fact",
            "fact_indices": [0],
            "fact_refs": [ref],
        }]},
        claims=(Claim("These experiences show NLP work.", ContentClass.FACT, (ref,)),),
    )
    llm = SequentialLLMGenerationPort(classify_failure, detail_success)
    req = RecommendRequest(
        query_text="含金量高不高？",
        conversation_context=ConversationContext(
            intent_source="implicit",
            anchor_entity_id="e1",
            session_id="s1",
            turn_id="t1",
        ),
    )
    summary = ConversationSummary(
        session_id="s1",
        through_turn_id="t0",
        text="上一轮询问了导师经历。",
        created_at="2026-07-02T00:00:00Z",
    )

    result = await _dispatcher(llm, details={"e1": detail}).dispatch(
        req,
        conversation_summary=summary,
    )

    assert result.kind == "detail_followup"
    assert result.context.intent == "detail_followup"
    assert result.context.intent_source == "implicit"
    assert result.context.intent_confidence == 0.0
    assert result.detail_followup.answer == "These experiences are meaningful because they show NLP work."
    assert "implicit intent output was not usable" not in result.detail_followup.answer
    assert [call["system_prompt_id"] for call in llm.calls] == [
        "dext_recommend.implicit_intent.v1",
        "dext_recommend.detail_followup.v1",
    ]
    detail_inputs = llm.calls[1]["user_inputs"]
    assert detail_inputs["question"] == "含金量高不高？"
    assert detail_inputs["display_name"] == "Prof X"
    assert detail_inputs["conversation_summary"] == "上一轮询问了导师经历。"


@pytest.mark.asyncio
async def test_implicit_classifier_unusable_without_anchor_still_clarifies():
    classify_failure = GenerationResult(
        output={},
        warnings=[GenerationWarning("generation_parse_error", "invalid JSON")],
    )
    llm = SequentialLLMGenerationPort(classify_failure)
    req = RecommendRequest(
        query_text="含金量高不高？",
        conversation_context=ConversationContext(
            intent_source="implicit",
            session_id="s1",
            turn_id="t1",
        ),
    )

    result = await _dispatcher(llm).dispatch(req)

    assert result.kind == "clarification"
    assert any(w.code == "needs_clarification" for w in result.issues)
    assert len(llm.calls) == 1
