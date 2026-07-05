from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

import dext_recommend.generation.auxiliary as auxiliary_module
from dext_grounded import (
    Claim, ConstrainedGenerationPipeline, ContentClass, FactBundle, FactItem,
    FakeLLMGenerationPort, GenerationResult, GenerationWarning, SourceRef,
    StudentContext,
)
from dext_recommend import (
    AuxiliaryGenerationService, MatchAnalysis, OutreachDraft, ProfessorComparison,
    ViewerPermissions,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.core.generation_support import map_generation_warnings
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.service import RecommendDeps, RecommendationCore
from dext_recommend.ports import (
    FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeRankingProfilePort, FakeRecommendGenerationProfilePort, FakeVectorSearchPort,
    ProfessorDetail,
)
from tests.dext_recommend._recfixtures import (
    generation_profile, ranking_profile_dict, snapshot,
)


def _ref(entity_id: str, quote: str, *, chunk: str = "h1") -> SourceRef:
    return SourceRef(
        doc_path=f"catalog:{entity_id}:research_statement",
        heading_path="research_statement",
        chunk_hash=f"{entity_id}-{chunk}",
        quote_or_summary=quote,
    )


def _bundle(entity_id: str, *, build_id: str = "b-1", empty: bool = False) -> FactBundle:
    if empty:
        return FactBundle(build_id=build_id, subject_id=entity_id, facts=(), source_refs=())
    ref = _ref(entity_id, f"{entity_id} works on NLP and trustworthy retrieval")
    fact = FactItem(
        field="research_statement",
        value=f"{entity_id} works on NLP and trustworthy retrieval",
        content_class=ContentClass.FACT,
        source_refs=(ref,),
    )
    return FactBundle(build_id=build_id, subject_id=entity_id, facts=(fact,), source_refs=(ref,))


def _match_scores(**overrides) -> dict[str, float]:
    scores = {
        "research_fit": 82.0,
        "method_match": 70.0,
        "location_fit": 60.0,
        "degree_goal": 68.0,
        "publication_activity": 75.0,
    }
    scores.update(overrides)
    return scores


def _detail(entity_id: str, *, build_id: str = "b-1", contacts=None,
            empty_bundle: bool = False) -> ProfessorDetail:
    bundle = _bundle(entity_id, build_id=build_id, empty=empty_bundle)
    return ProfessorDetail(
        build_id=build_id,
        profile_hash=f"ph-{entity_id}",
        entity_id=entity_id,
        display_name=f"Prof {entity_id}",
        university="U",
        org_units=("CS",),
        title="Professor",
        title_family="professor",
        master_eligibility="confirmed",
        phd_eligibility="confirmed",
        role_status="included",
        profile_url=None,
        research_statements=("NLP",),
        approved_topics=("NLP",),
        selected_publication_mentions=(),
        bio_snippets=(),
        source_urls=(),
        provenance_refs=bundle.source_refs,
        quality_findings=(),
        risk_flags=(),
        fact_bundle=bundle,
        contacts=contacts or {},
    )


def _service(llm: FakeLLMGenerationPort, *, details=None, snapshot_port=None,
             pipeline=None):
    snap = snapshot()
    snap_port = snapshot_port or FakeActiveSnapshotProvider(snap)
    deps = RecommendDeps(
        snapshot_port=snap_port,
        embedding_port=FakeQueryEmbeddingPort([0.1], snap.embedding_fingerprint),
        vector_port=FakeVectorSearchPort(hits=[]),
        facts_port=FakeProfessorFactPort(details=details or {}),
        llm_port=llm,
        ranking_port=FakeRankingProfilePort(profile=RankingProfile.from_dict(ranking_profile_dict())),
        generation_profile_port=FakeRecommendGenerationProfilePort(generation_profile()),
    )
    core = RecommendationCore(deps, RecommendSettings())
    return AuxiliaryGenerationService(
        core=core,
        pipeline=pipeline or ConstrainedGenerationPipeline(llm_port=llm),
        settings=RecommendSettings(),
    ), snap_port


def _result(output: dict, claim: Claim) -> GenerationResult:
    return GenerationResult(output=output, claims=(claim,), warnings=[])


class _ExplodingPipeline:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def generate(self, **kwargs):
        raise self.exc


class _ProviderError(RuntimeError):
    pass


_ProviderError.__module__ = "openai"


class _HttpCoreError(RuntimeError):
    pass


_HttpCoreError.__module__ = "httpcore"


@pytest.mark.asyncio
async def test_analyze_match_returns_grounded_payload_and_pins_snapshot_once():
    detail = _detail("e1")
    ref = detail.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Strong NLP fit.",
            "dimension_scores": _match_scores(),
            "next_steps": ["Read two recent papers"],
            "claims": [{
                "text": "Strong NLP fit.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="Strong NLP fit.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    service, snap_port = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "match_analysis"
    assert isinstance(result.match_analysis, MatchAnalysis)
    assert result.issues == ()
    assert result.match_analysis.summary == "Strong NLP fit."
    assert result.match_analysis.dimension_scores["research_fit"] == 82.0
    assert snap_port.get_snapshot_calls == 1
    assert len(llm.calls) == 1
    assert llm.calls[0]["fact_bundle"].subject_id == "e1"
    assert llm.calls[0]["fact_bundle"].facts[0].field == "research_statement"


@pytest.mark.asyncio
async def test_outreach_contacts_require_permission_and_stay_out_of_llm_bundle():
    detail = _detail("e1", contacts={"email": "prof@example.test"})
    ref = detail.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "subject": "Prospective student interested in NLP",
            "body": "I read your NLP work.",
            "claims": [{
                "text": "I read your NLP work.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="I read your NLP work.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    service, _ = _service(llm, details={"e1": detail})

    denied = await service.draft_outreach_email(
        "e1", StudentContext(research_interests=["NLP"]),
        tone="formal", language="zh", include_contacts=True,
    )
    assert denied.kind == "error"
    assert denied.issues[0].code == "unauthorized_contact"
    assert llm.calls == []

    allowed = await service.draft_outreach_email(
        "e1", StudentContext(research_interests=["NLP"]),
        tone="formal", language="zh", include_contacts=True,
        viewer_permissions=ViewerPermissions(include_contacts=True),
    )
    assert isinstance(allowed.outreach_draft, OutreachDraft)
    assert allowed.outreach_draft.authorized_contacts["email"] == "prof@example.test"
    bundle = llm.calls[0]["fact_bundle"]
    assert all(item.field not in {"email", "phone", "contacts"} for item in bundle.facts)
    assert "prof@example.test" not in "\n".join(item.value for item in bundle.facts)


@pytest.mark.asyncio
async def test_outreach_email_applies_profile_fact_limit_and_rebuilds_refs():
    detail = _detail("e1")
    refs = tuple(_ref("e1", f"e1 NLP fact {idx}", chunk=f"h{idx}") for idx in range(12))
    facts = tuple(
        FactItem(
            field=f"research_{idx}",
            value=f"e1 NLP fact {idx}",
            content_class=ContentClass.FACT,
            source_refs=(ref,),
        )
        for idx, ref in enumerate(refs)
    )
    detail = replace(
        detail,
        fact_bundle=FactBundle(
            build_id=detail.build_id,
            subject_id=detail.entity_id,
            facts=facts,
            source_refs=refs,
        ),
        provenance_refs=refs,
    )
    ref = refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "subject": "Prospective student interested in NLP",
            "body": "I read your NLP work.",
            "claims": [{
                "text": "I read your NLP work.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="I read your NLP work.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.draft_outreach_email(
        "e1",
        StudentContext(research_interests=["NLP"]),
        tone="formal",
        language="zh",
    )

    assert result.kind == "outreach_email"
    bundle = llm.calls[0]["fact_bundle"]
    assert len(bundle.facts) == 8
    assert bundle.facts == facts[:8]
    assert bundle.source_refs == refs[:8]


@pytest.mark.asyncio
async def test_compare_professors_merges_two_or_three_fact_bundles_only():
    d1 = _detail("e1")
    d2 = _detail("e2")
    ref = d1.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Both work on NLP; e1 has stronger retrieval evidence.",
            "professor_notes": {"e1": ["retrieval"], "e2": ["NLP"]},
            "evidence_gaps": {"e1": [], "e2": ["few publication mentions"]},
            "claims": [{
                "text": "e1 has retrieval evidence.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="e1 has retrieval evidence.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    service, _ = _service(llm, details={"e1": d1, "e2": d2})

    invalid = await service.compare_professors(
        ["e1"], StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )
    assert invalid.kind == "error"
    assert invalid.issues[0].code == "invalid_request"

    result = await service.compare_professors(
        ["e1", "e2"], StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )
    assert isinstance(result.professor_comparison, ProfessorComparison)
    assert result.professor_comparison.entity_ids == ("e1", "e2")
    bundle = llm.calls[0]["fact_bundle"]
    assert bundle.subject_id == "compare:e1,e2"
    assert {item.field for item in bundle.facts} == {"e1:research_statement", "e2:research_statement"}


@pytest.mark.asyncio
async def test_auxiliary_support_map_derives_refs_when_fact_refs_empty():
    detail = _detail("e1")
    ref = detail.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Indexed support.",
            "dimension_scores": _match_scores(),
            "next_steps": [],
            "claims": [{
                "text": "Indexed support.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [],
            }],
        },
        Claim(text="Indexed support.", content_class=ContentClass.FACT, fact_refs=()),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "match_analysis"
    assert result.match_analysis is not None
    assert result.match_analysis.claims[0].fact_refs == (ref,)
    assert result.match_analysis.cited_refs == (ref,)


@pytest.mark.asyncio
async def test_auxiliary_support_map_uses_indices_over_unrelated_fact_refs():
    detail = _detail("e1")
    ref0 = detail.fact_bundle.source_refs[0]
    ref1 = _ref("e1", "unrelated robotics", chunk="h2")
    other = FactItem(
        field="bio", value="unrelated robotics",
        content_class=ContentClass.FACT, source_refs=(ref1,),
    )
    detail = replace(
        detail,
        fact_bundle=FactBundle(
            build_id=detail.build_id, subject_id=detail.entity_id,
            facts=detail.fact_bundle.facts + (other,),
            source_refs=(ref0, ref1),
        ),
        provenance_refs=(ref0, ref1),
    )
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Bad support.",
            "dimension_scores": _match_scores(),
            "next_steps": [],
            "claims": [{
                "text": "Bad support.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref1.doc_path,
                    "heading_path": ref1.heading_path,
                    "chunk_hash": ref1.chunk_hash,
                    "quote_or_summary": ref1.quote_or_summary,
                }],
            }],
        },
        Claim(text="Bad support.", content_class=ContentClass.FACT, fact_refs=(ref1,)),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "match_analysis"
    assert result.match_analysis is not None
    assert result.match_analysis.claims[0].fact_refs == (ref0,)
    assert result.match_analysis.cited_refs == (ref0,)


@pytest.mark.asyncio
@pytest.mark.parametrize("fact_indices", ([], [99], ["0"]))
async def test_auxiliary_support_map_blocks_invalid_fact_indices(fact_indices):
    detail = _detail("e1")
    ref = detail.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Bad support.",
            "dimension_scores": _match_scores(),
            "next_steps": [],
            "claims": [{
                "text": "Bad support.",
                "content_class": "fact",
                "fact_indices": fact_indices,
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="Bad support.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "no_grounded_output"


@pytest.mark.asyncio
async def test_auxiliary_support_map_blocks_fact_indices_without_source_refs():
    detail = _detail("e1")
    uncited_fact = FactItem(
        field="research_statement",
        value="e1 works on NLP",
        content_class=ContentClass.UNCERTAIN,
        source_refs=(),
    )
    detail = replace(
        detail,
        fact_bundle=FactBundle(
            build_id=detail.build_id,
            subject_id=detail.entity_id,
            facts=(uncited_fact,),
            source_refs=(),
        ),
        provenance_refs=(),
    )
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Bad support.",
            "dimension_scores": _match_scores(),
            "next_steps": [],
            "claims": [{
                "text": "Bad support.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [],
            }],
        },
        Claim(text="Bad support.", content_class=ContentClass.FACT, fact_refs=()),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "no_grounded_output"


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension_scores", [
    {key: value for key, value in _match_scores().items() if key != "method_match"},
    _match_scores(method_match="high"),
    {},
])
async def test_analyze_match_rejects_unusable_dimension_scores(dimension_scores):
    detail = _detail("e1")
    ref = detail.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Strong NLP fit.",
            "dimension_scores": dimension_scores,
            "next_steps": ["Read recent papers"],
            "claims": [{
                "text": "Strong NLP fit.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="Strong NLP fit.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "generation_parse_error"
    assert result.issues[0].message == "match analysis dimension_scores are not usable"


@pytest.mark.asyncio
async def test_auxiliary_returns_insufficient_facts_without_llm_call():
    detail = _detail("e1", empty_bundle=True)
    llm = FakeLLMGenerationPort(preset=GenerationResult(output={}))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "insufficient_facts"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_auxiliary_output_policy_refuses_mentor_attack():
    detail = _detail("e1")
    llm = FakeLLMGenerationPort(preset=GenerationResult(
        output={
            "summary": "这个导师垃圾",
            "dimension_scores": {},
            "next_steps": [],
            "claims": [],
        },
        claims=(Claim(text="这个导师垃圾", content_class=ContentClass.ADVICE),),
    ))
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1", StudentContext(research_interests=["NLP"]), evidence_policy="default",
    )

    assert result.kind == "error"
    assert any(issue.code == "content_policy_refusal" for issue in result.issues)


@pytest.mark.asyncio
async def test_auxiliary_outer_timeout_adds_provider_grace(monkeypatch):
    detail = _detail("e1")
    ref = detail.fact_bundle.source_refs[0]
    llm = FakeLLMGenerationPort(preset=_result(
        {
            "summary": "Strong NLP fit.",
            "dimension_scores": _match_scores(),
            "next_steps": ["Read recent papers"],
            "claims": [{
                "text": "Strong NLP fit.",
                "content_class": "fact",
                "fact_indices": [0],
                "fact_refs": [{
                    "doc_path": ref.doc_path,
                    "heading_path": ref.heading_path,
                    "chunk_hash": ref.chunk_hash,
                    "quote_or_summary": ref.quote_or_summary,
                }],
            }],
        },
        Claim(text="Strong NLP fit.", content_class=ContentClass.FACT, fact_refs=(ref,)),
    ))
    captured: dict[str, float] = {}

    async def fake_wait_for(coro, timeout):
        captured["timeout"] = timeout
        return await coro

    monkeypatch.setattr(auxiliary_module.asyncio, "wait_for", fake_wait_for)
    service, _ = _service(llm, details={"e1": detail})

    result = await service.analyze_match(
        "e1",
        StudentContext(research_interests=["NLP"]),
        evidence_policy="default",
    )

    assert result.kind == "match_analysis"
    op_timeout = generation_profile().operations["match_analysis"].timeout
    assert captured["timeout"] == pytest.approx(op_timeout + 5.0)


@pytest.mark.asyncio
async def test_auxiliary_generation_timeout_is_request_timeout(caplog):
    detail = _detail("e1")
    llm = FakeLLMGenerationPort(preset=GenerationResult(output={}))
    service, _ = _service(
        llm,
        details={"e1": detail},
        pipeline=_ExplodingPipeline(asyncio.TimeoutError()),
    )

    with caplog.at_level("ERROR", logger="dext_recommend.generation.auxiliary"):
        result = await service.analyze_match(
            "e1",
            StudentContext(research_interests=["NLP"]),
            evidence_policy="default",
            request_id="req-timeout",
        )

    assert result.kind == "error"
    assert result.issues[0].code == "request_timeout"
    assert result.issues[0].message == "match_analysis generation timed out"
    assert "request_id=req-timeout" in caplog.text
    assert "operation_id=match_analysis" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "timeout=" in caplog.text
    assert "outer_timeout=" in caplog.text
    assert "error_type=TimeoutError" in caplog.text


@pytest.mark.asyncio
async def test_auxiliary_provider_exception_is_llm_unavailable():
    detail = _detail("e1")
    llm = FakeLLMGenerationPort(preset=GenerationResult(output={}))
    service, _ = _service(
        llm,
        details={"e1": detail},
        pipeline=_ExplodingPipeline(_ProviderError("provider down")),
    )

    result = await service.analyze_match(
        "e1",
        StudentContext(research_interests=["NLP"]),
        evidence_policy="default",
        request_id="req-provider",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "llm_unavailable"
    assert result.issues[0].message == "match_analysis provider unavailable"


@pytest.mark.asyncio
async def test_auxiliary_wrapped_provider_exception_is_llm_unavailable():
    detail = _detail("e1")
    wrapped = RuntimeError("wrapped provider error")
    wrapped.__cause__ = _ProviderError("provider down")
    llm = FakeLLMGenerationPort(preset=GenerationResult(output={}))
    service, _ = _service(
        llm,
        details={"e1": detail},
        pipeline=_ExplodingPipeline(wrapped),
    )

    result = await service.draft_outreach_email(
        "e1",
        StudentContext(research_interests=["NLP"]),
        tone="formal",
        language="zh",
        request_id="req-provider-wrapped",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "llm_unavailable"
    assert result.issues[0].message == "outreach_email provider unavailable"


@pytest.mark.asyncio
async def test_auxiliary_httpcore_exception_is_llm_unavailable():
    detail = _detail("e1")
    llm = FakeLLMGenerationPort(preset=GenerationResult(output={}))
    service, _ = _service(
        llm,
        details={"e1": detail},
        pipeline=_ExplodingPipeline(_HttpCoreError("transport down")),
    )

    result = await service.analyze_match(
        "e1",
        StudentContext(research_interests=["NLP"]),
        evidence_policy="default",
        request_id="req-httpcore",
    )

    assert result.kind == "error"
    assert result.issues[0].code == "llm_unavailable"
    assert result.issues[0].message == "match_analysis provider unavailable"


@pytest.mark.asyncio
async def test_auxiliary_unknown_generation_exception_stays_generation_unavailable(caplog):
    detail = _detail("e1")
    llm = FakeLLMGenerationPort(preset=GenerationResult(output={}))
    service, _ = _service(
        llm,
        details={"e1": detail},
        pipeline=_ExplodingPipeline(RuntimeError("boom")),
    )

    with caplog.at_level("ERROR", logger="dext_recommend.generation.auxiliary"):
        result = await service.analyze_match(
            "e1",
            StudentContext(research_interests=["NLP"]),
            evidence_policy="default",
            request_id="req-unknown",
        )

    assert result.kind == "error"
    assert result.issues[0].code == "generation_unavailable"
    assert result.issues[0].message == "match_analysis generation failed"
    assert "request_id=req-unknown" in caplog.text
    assert "operation_id=match_analysis" in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_llm_unavailable_warning_maps_to_error():
    mapped = map_generation_warnings((
        GenerationWarning(code="llm_unavailable", message="provider down"),
    ))

    assert mapped[0].code == "llm_unavailable"
    assert mapped[0].message == "provider down"
    assert mapped[0].severity == "error"


def test_auxiliary_dtos_are_deeply_immutable():
    warning = ()
    match = MatchAnalysis(
        build_id="b", ranking_profile_version="rv", generation_profile_version="gv",
        grounded_rules_manifest_hash="grh", embedding_fingerprint="ef",
        taxonomy_version=None, entity_id="e1", display_name="Prof e1",
        summary="s", dimension_scores={"research_fit": 0.8},
        next_steps=["read"], claims=[], cited_refs=[], warnings=warning,
    )
    with pytest.raises(TypeError):
        match.dimension_scores["research_fit"] = 0.9
    with pytest.raises(AttributeError):
        match.next_steps.append("mutate")

    comparison = ProfessorComparison(
        build_id="b", ranking_profile_version="rv", generation_profile_version="gv",
        grounded_rules_manifest_hash="grh", embedding_fingerprint="ef",
        taxonomy_version=None, entity_ids=["e1", "e2"],
        display_names={"e1": "A", "e2": "B"}, summary="s",
        professor_notes={"e1": ["note"]}, evidence_gaps={"e2": ["gap"]},
        claims=[], cited_refs=[], warnings=[],
    )
    with pytest.raises(TypeError):
        comparison.professor_notes["e1"] = ("x",)
    with pytest.raises(AttributeError):
        comparison.professor_notes["e1"].append("x")
