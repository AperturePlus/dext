from __future__ import annotations

import pytest

from dext_grounded import (
    Claim, ContentClass, FactBundle, FactItem, FakeLLMGenerationPort,
    GenerationResult, SourceRef,
)
from dext_recommend import ConversationContext, ProfessorDetail, RecommendRequest
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
