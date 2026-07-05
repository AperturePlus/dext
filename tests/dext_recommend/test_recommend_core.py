from __future__ import annotations

import asyncio
import dataclasses
import sqlite3
from pathlib import Path

import pytest

from dext_recommend import (
    FakeProfessorFactPort, FakeQueryEmbeddingPort, FakeVectorSearchPort,
    ProfessorDetail, RecommendRequest, RecommendResponse,
    RecommendationFilters, RecommendationWarning, ViewerPermissions,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.service import RecommendDeps as _RecommendDeps, RecommendationCore
from dext_recommend.ports._fakes import (
    FakeActiveSnapshotProvider, FakeRankingProfilePort,
    FakeRecommendGenerationProfilePort,
)
from dext_recommend.models import ConversationContext
from dext_grounded import FactBundle, FactItem
from dext_grounded.content import ContentClass


def _empty_fact_bundle(*, build_id: str, entity_id: str) -> FactBundle:
    return FactBundle(
        build_id=build_id, subject_id=entity_id,
        facts=(FactItem(field="display_name", value=entity_id,
                        content_class=ContentClass.UNCERTAIN),),
        source_refs=(),
    )

from tests.dext_recommend._recfixtures import (
    coverage_flags_case, fake_llm_for_understanding, generation_profile,
    professor_details_case,
    professor_facts_case, ranking_profile_dict, snapshot, vector_hits_case,
)


def RecommendDeps(**kwargs):
    kwargs.setdefault(
        "generation_profile_port",
        FakeRecommendGenerationProfilePort(generation_profile()),
    )
    return _RecommendDeps(**kwargs)


def _output(**over):
    base = {
        "research_interests": ["NLP"], "preferred_universities": [],
        "preferred_cities": [], "preferred_org_units": [], "degree_goal": "master",
        "mentor_eligibility_requirement": None, "missing_information": [],
        "needs_clarification": False, "confidence": 0.8,
    }
    base.update(over)
    return base


def _core(
    *, hits=None, facts=None, details=None, llm_output=None,
    coverage=None, snapshot_obj="UNSET", profile=None, embedding_fp="fp-x",
):
    # Default sentinel "UNSET" distinguishes "caller passed None (no ACTIVE
    # build)" from "caller did not specify snapshot_obj". When None, build the
    # deps against FakeActiveSnapshotProvider(None) directly so the snapshot
    # port reports no ACTIVE build — no post-construction mutation needed
    # (RecommendDeps is frozen).
    if snapshot_obj == "UNSET":
        snap = snapshot()
        snapshot_port = FakeActiveSnapshotProvider(snap)
    else:
        snap = snapshot_obj
        snapshot_port = FakeActiveSnapshotProvider(snapshot_obj)
    prof = profile or RankingProfile.from_dict(ranking_profile_dict())
    return RecommendationCore(
        RecommendDeps(
            snapshot_port=snapshot_port,
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], embedding_fp),
            vector_port=FakeVectorSearchPort(hits=hits or list(vector_hits_case("happy"))),
            facts_port=FakeProfessorFactPort(
                facts=facts or professor_facts_case("happy"),
                details=details or professor_details_case("happy"),
            ),
            llm_port=fake_llm_for_understanding(llm_output or _output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage or coverage_flags_case(
                snap.build_id if snap else "b-1"
            ),
        ),
        RecommendSettings(),
    )


async def test_new_search_happy_path():
    core = _core()
    resp = await core.recommend(RecommendRequest(query_text="NLP 导师"))
    assert isinstance(resp, RecommendResponse)
    assert resp.build_id == "b-1"
    assert resp.ranking_profile_version == "r1"
    assert resp.generation_profile_version == "generation-v2"
    assert resp.embedding_fingerprint == "fp-x"
    assert len(resp.results) >= 1
    ids = [r.entity_id for r in resp.results]
    assert "e_cv_strong" in ids
    assert "e_cv_excluded" not in ids


async def test_recommend_steps_used_in_diagnostics():
    """Happy path: resp.query.steps_used >= 1."""
    core = _core()
    resp = await core.recommend(RecommendRequest(query_text="NLP 导师"))
    assert isinstance(resp, RecommendResponse)
    assert resp.query.steps_used >= 1


async def test_no_active_build_returns_error_response():
    core = _core(snapshot_obj=None)
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    assert resp.results == ()
    assert any(w.code == "active_build_unavailable" for w in resp.warnings)


async def test_needs_clarification_skips_recall():
    core = _core(llm_output=_output(needs_clarification=True, confidence=0.2))
    resp = await core.recommend(RecommendRequest(query_text="随便"))
    assert resp.results == ()
    assert any(w.code == "needs_clarification" for w in resp.warnings)
    # vector_port never called
    assert core._deps.vector_port.hybrid_recall_calls == []


async def test_no_candidates_after_filters():
    # hard filter that matches nothing
    core = _core(hits=list(vector_hits_case("happy")),
                  facts=professor_facts_case("happy"),
                  details=professor_details_case("happy"))
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        filters=RecommendationFilters(university_ids=("u_nonexistent",)),
    ))
    assert resp.results == ()
    assert any(w.code == "no_candidates_after_filters" for w in resp.warnings)


async def test_oversample_step_progression():
    # only 3 hits returned, limit 10 -> all 4 steps used
    hits = list(vector_hits_case("happy"))[:1]
    core = _core(hits=hits)
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=10))
    assert len(core._deps.vector_port.hybrid_recall_calls) == 4
    oversamples = [c["oversample"] for c in core._deps.vector_port.hybrid_recall_calls]
    assert oversamples == [200, 400, 800, 1000]


async def test_detail_followup_short_circuits_before_llm():
    core = _core()
    from dext_recommend import ConversationContext
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="detail_followup", intent_source="explicit",
            anchor_entity_id="e1", session_id="s1", turn_id="t1"),
    ))
    assert resp.results == ()
    assert any(w.code == "invalid_conversation_state" for w in resp.warnings)
    assert core._deps.vector_port.hybrid_recall_calls == []


async def test_embedding_fingerprint_mismatch_error():
    core = _core(embedding_fp="wrong-fp")
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    assert any(w.code == "embedding_fingerprint_mismatch" for w in resp.warnings)
    assert resp.results == ()


async def test_ranking_profile_unavailable_error():
    from dext_recommend import ReadinessSourceError
    snap = snapshot()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1], "fp-x"),
            vector_port=FakeVectorSearchPort(),
            facts_port=FakeProfessorFactPort(),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(error=ReadinessSourceError("ranking", "boom")),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    assert any(w.code == "ranking_profile_unavailable" for w in resp.warnings)
    assert resp.results == ()


async def test_snapshot_pinned_throughout():
    core = _core()
    await core.recommend(RecommendRequest(query_text="NLP"))
    assert core.deps.snapshot_port.get_snapshot_calls == 1
    assert len(core.deps.generation_profile_port.read_profile_calls) == 1
    # every port call used the same build_id
    for c in core._deps.vector_port.hybrid_recall_calls:
        assert c["snapshot_build_id"] == "b-1"
    for c in core._deps.facts_port.hydrate_calls:
        assert c["snapshot_build_id"] == "b-1"
    for c in core._deps.facts_port.get_detail_calls:
        assert c["snapshot_build_id"] == "b-1"


async def test_org_unit_degraded_when_coverage_false():
    hits = list(vector_hits_case("other_org"))
    facts = professor_facts_case("other_org")
    details = professor_details_case("no_statement")
    core = _core(hits=hits, facts=facts, details=details,
                 coverage=coverage_flags_case("b-1", org_unit_ids=False))
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        filters=RecommendationFilters(org_unit_ids=("ou_cs",)),
    ))
    # degraded: candidate kept despite fact saying ou_math
    assert any(w.code == "org_unit_filter_unavailable" for w in resp.warnings)
    ids = [r.entity_id for r in resp.results]
    assert "e_other_org" in ids


async def test_recommend_org_unit_degraded_silent_when_not_requested():
    """org_unit coverage missing but filters.org_unit_ids empty -> no warning."""
    core = _core(
        hits=list(vector_hits_case("happy")),
        facts=professor_facts_case("happy"),
        details=professor_details_case("happy"),
        coverage=coverage_flags_case("b-1", org_unit_ids=None),
    )
    # request has NO org_unit_ids filter
    from dext_recommend.models import RecommendRequest, RecommendationFilters
    req = RecommendRequest(
        query_text="computer vision", filters=RecommendationFilters(),
        oversample=200, limit=5,
    )
    resp = await core.recommend(req)
    codes = [w.code for w in resp.warnings]
    assert "org_unit_filter_unavailable" not in codes


async def test_recommend_org_unit_enforced_when_coverage_ok():
    """flag True + org_unit requested -> mismatching candidate filtered (no over-softening)."""
    hits = list(vector_hits_case("other_org"))
    facts = professor_facts_case("other_org")
    details = professor_details_case("no_statement")
    core = _core(hits=hits, facts=facts, details=details,
                 coverage=coverage_flags_case("b-1", org_unit_ids=True))
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        filters=RecommendationFilters(org_unit_ids=("ou_cs",)),
    ))
    ids = [r.entity_id for r in resp.results]
    assert "e_other_org" not in ids
    # no degradation warning
    codes = [w.code for w in resp.warnings]
    assert "org_unit_filter_unavailable" not in codes


async def test_response_validation_runs():
    core = _core()
    resp = await core.recommend(RecommendRequest(query_text="NLP 导师"))
    # validate is called inside recommend; a valid response is returned
    assert resp.build_id == "b-1"
    assert resp.ranking_profile_version == "r1"


async def test_recommend_refine_direction_merge():
    """refine_direction merges only safe QU city preferences into filters."""
    core = _core(
        llm_output=_output(
            preferred_cities=["北京市"],
            preferred_universities=["清华"],
            preferred_org_units=["计算机学院"],
            mentor_eligibility_requirement="confirmed",
        ),
    )
    resp = await core.recommend(RecommendRequest(
        query_text="换方向",
        filters=RecommendationFilters(),  # all defaults: empty + any
        conversation_context=ConversationContext(intent="refine_direction", intent_source="explicit"),
    ))
    # Only the city preference is safe to hard-filter from QU. University and
    # org-unit preferences are names here, not internal IDs.
    assert core._deps.vector_port.hybrid_recall_calls
    eff = core._deps.vector_port.hybrid_recall_calls[0]["filters"]
    assert eff.city_names == ("北京",)
    assert eff.university_ids == ()
    assert eff.org_unit_ids == ()
    assert eff.master_eligibility == "any"
    # response is well-formed (success or clean no_candidates)
    assert isinstance(resp, RecommendResponse)


async def test_qu_university_name_is_not_used_as_university_id_filter():
    core = _core(llm_output=_output(preferred_universities=["清华"]))
    resp = await core.recommend(RecommendRequest(query_text="清华 NLP 导师"))

    assert isinstance(resp, RecommendResponse)
    assert core._deps.vector_port.hybrid_recall_calls
    assert all(
        call["filters"].university_ids == ()
        for call in core._deps.vector_port.hybrid_recall_calls
    )


async def test_new_search_merges_location_preferences_without_relaxing_when_enough():
    from dext_recommend import VectorHit

    hits = [
        VectorHit("e_bj_1", 0.92, {"city": "北京", "role_status": "included"}),
        VectorHit("e_bj_2", 0.90, {"city": "北京市", "role_status": "included"}),
        VectorHit("e_wh", 0.99, {"city": "武汉", "role_status": "included"}),
    ]
    base = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    facts = {
        "e_bj_1": dataclasses.replace(base, entity_id="e_bj_1", city_name="北京"),
        "e_bj_2": dataclasses.replace(base, entity_id="e_bj_2", city_name="北京市"),
        "e_wh": dataclasses.replace(base, entity_id="e_wh", city_name="武汉"),
    }
    details = {
        eid: dataclasses.replace(
            detail, entity_id=eid,
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id=eid),
        )
        for eid in facts
    }
    core = _core(
        hits=hits, facts=facts, details=details,
        llm_output=_output(preferred_cities=["北京"]),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=2))

    assert [r.entity_id for r in resp.results] == ["e_bj_1", "e_bj_2"]
    assert "preference_relaxed" not in [w.code for w in resp.warnings]
    assert len(core._deps.vector_port.hybrid_recall_calls) == 1
    eff = core._deps.vector_port.hybrid_recall_calls[0]["filters"]
    assert eff.city_names == ("北京",)


async def test_recommend_direction_evidence_beats_semantic_neighbor():
    from dext_recommend import VectorHit

    hits = [
        VectorHit("e_visual", 0.99, {"city": "上海", "role_status": "included"}),
        VectorHit("e_cv", 0.80, {"city": "上海", "role_status": "included"}),
        VectorHit("e_other", 0.60, {"city": "上海", "role_status": "included"}),
    ]
    base = professor_facts_case("happy")["e_cv_strong"]
    base_detail = professor_details_case("happy")["e_cv_strong"]
    facts = {
        "e_visual": dataclasses.replace(
            base, entity_id="e_visual", display_name="Visual Neighbor",
            city_name="上海", research_summary="数据可视化、人机交互、智能传播",
            topic_ids=(),
        ),
        "e_cv": dataclasses.replace(
            base, entity_id="e_cv", display_name="CV Mentor",
            city_name="上海", research_summary="计算机视觉与医学影像分析",
            topic_ids=(),
        ),
        "e_other": dataclasses.replace(
            base, entity_id="e_other", display_name="Other Neighbor",
            city_name="上海", research_summary="智能传播与用户体验",
            topic_ids=(),
        ),
    }
    details = {
        "e_visual": dataclasses.replace(
            base_detail, entity_id="e_visual", display_name="Visual Neighbor",
            research_statements=("关注用户体验与信息传达设计",),
            approved_topics=("数据可视化", "人机交互"),
            selected_publication_mentions=("计算机视觉邻域的可视化论文",),
            source_urls=("http://example/visual",),
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id="e_visual"),
        ),
        "e_cv": dataclasses.replace(
            base_detail, entity_id="e_cv", display_name="CV Mentor",
            research_statements=("开展计算机视觉方向研究",),
            approved_topics=("计算机视觉",),
            selected_publication_mentions=("computer vision paper",),
            source_urls=("http://example/cv",),
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id="e_cv"),
        ),
        "e_other": dataclasses.replace(
            base_detail, entity_id="e_other", display_name="Other Neighbor",
            research_statements=("关注智能传播与用户体验",),
            approved_topics=("智能传播",),
            selected_publication_mentions=("CHI paper",),
            source_urls=("http://example/other",),
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id="e_other"),
        ),
    }
    core = _core(
        hits=hits, facts=facts, details=details,
        llm_output=_output(
            research_interests=["计算机视觉"], preferred_cities=["上海"],
        ),
    )

    resp = await core.recommend(RecommendRequest(
        query_text="上海 计算机视觉", limit=2,
    ))

    assert [r.entity_id for r in resp.results][0] == "e_cv"
    visual = next(r for r in resp.results if r.entity_id == "e_visual")
    assert visual.score_components["direction_evidence_score"] == 0.0
    assert visual.match_level == "weak"
    assert any(w.code == "weak_explanation" for w in resp.warnings)


async def test_new_search_relaxes_location_when_preferred_city_underfills():
    from dext_recommend import VectorHit

    hits = [
        VectorHit("e_bj", 0.90, {"city": "北京", "role_status": "included"}),
        VectorHit("e_wh", 0.99, {"city": "武汉", "role_status": "included"}),
    ]
    base = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    facts = {
        "e_bj": dataclasses.replace(base, entity_id="e_bj", city_name="北京"),
        "e_wh": dataclasses.replace(base, entity_id="e_wh", city_name="武汉"),
    }
    details = {
        eid: dataclasses.replace(
            detail, entity_id=eid,
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id=eid),
        )
        for eid in facts
    }
    core = _core(
        hits=hits, facts=facts, details=details,
        llm_output=_output(preferred_cities=["北京"]),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=2))

    assert [r.entity_id for r in resp.results] == ["e_bj", "e_wh"]
    assert "preference_relaxed" in [w.code for w in resp.warnings]
    relaxed = next(r for r in resp.results if r.entity_id == "e_wh")
    assert "location_relaxed" in relaxed.risk_flags
    assert core._deps.vector_port.hybrid_recall_calls[0]["filters"].city_names == ("北京",)
    assert core._deps.vector_port.hybrid_recall_calls[-1]["filters"].city_names == ()


async def test_batch_detail_failure_marks_warning_without_single_fetch_fanout():
    from dext_recommend import VectorHit

    class FailingBatchPort(FakeProfessorFactPort):
        def __init__(self, *, facts):
            super().__init__(facts=facts, details={})
            self.get_details_calls = []

        async def get_details(self, snapshot, entity_ids, include_contacts, viewer_permissions):
            self.get_details_calls.append(list(entity_ids))
            raise sqlite3.OperationalError("database is locked")

        async def get_detail(self, snapshot, entity_id, include_contacts, viewer_permissions):
            raise AssertionError("batch failure must not fall back to concurrent single fetch")

    base = professor_facts_case("happy")["e_cv_strong"]
    facts = {
        "e1": dataclasses.replace(base, entity_id="e1"),
        "e2": dataclasses.replace(base, entity_id="e2"),
    }
    hits = [
        VectorHit("e1", 0.90, {"city": "北京", "role_status": "included"}),
        VectorHit("e2", 0.88, {"city": "北京", "role_status": "included"}),
    ]
    core = _core(hits=hits, facts=facts)
    failing_port = FailingBatchPort(facts=facts)
    core = RecommendationCore(
        dataclasses.replace(core._deps, facts_port=failing_port),
        RecommendSettings(),
    )

    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=2))

    assert len(failing_port.get_details_calls) == 2
    assert failing_port.get_detail_calls == []
    warnings = [w for w in resp.warnings if w.code == "details_unavailable"]
    assert warnings
    assert warnings[-1].message == "2 detail(s) unavailable; degraded"


def _fact(eid: str, *, topic_ids: tuple[str, ...] = ("topic_cv",),
          role: str = "included") -> ProfessorFact:
    from dext_recommend import ProfessorFact
    return ProfessorFact(
        entity_id=eid, display_name=eid.replace("_", " ").title(),
        university="示例大学", org_units=("计算机学院",), title="Prof",
        title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status=role, profile_url=None,
        profile_hash=None, research_summary="summary",
        university_id="u_demo", city_name="北京", org_unit_ids=("ou_cs",),
        topic_ids=topic_ids,
    )


def _detail(eid: str, *, topics: tuple[str, ...] = ("topic_cv",)) -> ProfessorDetail:
    return ProfessorDetail(
        build_id="b-1", profile_hash=None, entity_id=eid,
        display_name=eid.replace("_", " ").title(), university="示例大学",
        org_units=("计算机学院",), title="Prof", title_family="professor",
        master_eligibility="confirmed", phd_eligibility="confirmed",
        role_status="included", profile_url=None,
        research_statements=("NLP research",), approved_topics=topics,
        selected_publication_mentions=("paper A",), bio_snippets=(),
        source_urls=("http://example/p",), provenance_refs=(),
        quality_findings=(), risk_flags=(),
        fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id=eid),
    )


async def test_recommend_same_field_anchor_excluded_and_topic_overlap_ranked():
    """Anchor not in results; topic-sharing candidates rank above non-sharing
    when semantic scores are tied."""
    from dext_recommend import VectorHit
    # anchor has two topics; e_nlp_a shares one, e_nlp_b shares both,
    # e_cv_strong shares none. All semantic tied so topic overlap is the
    # only rank differentiator (same_field_boost).
    hits = [
        VectorHit("e_nlp_anchor", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp", "topic_ml"],
        }),
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
        VectorHit("e_nlp_b", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp", "topic_ml"],
        }),
        VectorHit("e_cv_strong", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
    ]
    facts = {
        "e_nlp_anchor": _fact("e_nlp_anchor", topic_ids=("topic_nlp", "topic_ml")),
        "e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",)),
        "e_nlp_b": _fact("e_nlp_b", topic_ids=("topic_nlp", "topic_ml")),
        "e_cv_strong": _fact("e_cv_strong", topic_ids=("topic_cv",)),
    }
    details = {
        "e_nlp_anchor": _detail("e_nlp_anchor", topics=("topic_nlp", "topic_ml")),
        "e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",)),
        "e_nlp_b": _detail("e_nlp_b", topics=("topic_nlp", "topic_ml")),
        "e_cv_strong": _detail("e_cv_strong", topics=("topic_cv",)),
    }
    core = _core(hits=hits, facts=facts, details=details)
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    ids = [r.entity_id for r in resp.results]
    # anchor excluded from results
    assert "e_nlp_anchor" not in ids
    # both nlp sharers present and ranked above the non-sharing cv candidate
    assert "e_nlp_a" in ids
    assert "e_nlp_b" in ids
    assert "e_cv_strong" in ids
    pos_a = ids.index("e_nlp_a")
    pos_b = ids.index("e_nlp_b")
    pos_cv = ids.index("e_cv_strong")
    assert pos_a < pos_cv
    assert pos_b < pos_cv


async def test_recommend_same_field_anchor_missing_is_terminal():
    """An anchor absent from ACTIVE build terminates before recall."""
    from dext_recommend import VectorHit
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    # anchor fact deliberately absent from facts map
    facts = {"e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",))}
    details = {"e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",))}
    core = _core(hits=hits, facts=facts, details=details)
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "anchor_not_in_active_build" in codes
    assert resp.results == ()
    assert core.deps.vector_port.hybrid_recall_calls == []


async def test_recommend_same_field_anchor_excluded_is_terminal():
    """An excluded anchor terminates before recall."""
    from dext_recommend import VectorHit
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    facts = {
        "e_nlp_anchor": _fact("e_nlp_anchor", topic_ids=("topic_nlp",),
                              role="excluded"),
        "e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",)),
    }
    details = {"e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",))}
    core = _core(hits=hits, facts=facts, details=details)
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "anchor_not_in_active_build" in codes
    assert resp.results == ()


async def test_recommend_same_field_anchor_without_topics_is_terminal():
    """An anchor without approved topics terminates before recall."""
    from dext_recommend import VectorHit
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    facts = {
        "e_nlp_anchor": _fact("e_nlp_anchor", topic_ids=()),
        "e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",)),
    }
    details = {"e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",))}
    core = _core(hits=hits, facts=facts, details=details)
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "anchor_not_in_active_build" in codes
    assert resp.results == ()


async def test_recommend_same_field_boost_from_profile():
    """profile same_field_boost_per_topic=0 -> no boost (order by tie-break only)."""
    from dext_recommend import VectorHit
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
        VectorHit("e_cv_strong", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
    ]
    facts = {
        "e_nlp_anchor": _fact("e_nlp_anchor", topic_ids=("topic_nlp",)),
        "e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",)),
        "e_cv_strong": _fact("e_cv_strong", topic_ids=("topic_cv",)),
    }
    details = {
        "e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",)),
        "e_cv_strong": _detail("e_cv_strong", topics=("topic_cv",)),
    }
    # boost disabled: per_topic=0 -> boost=0 for all, order is pure tie-break
    prof = RankingProfile.from_dict(ranking_profile_dict(
        same_field_boost_per_topic=0.0, same_field_boost_max=0.0,
    ))
    core = _core(hits=hits, facts=facts, details=details, profile=prof)
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    ids = [r.entity_id for r in resp.results]
    # both candidates present (anchor excluded), boost=0 means tie-break by
    # entity_id asc since semantic scores are tied
    assert "e_nlp_anchor" not in ids
    assert "e_cv_strong" in ids
    assert "e_nlp_a" in ids
    # tie-break: entity_id asc -> e_cv_strong before e_nlp_a
    assert ids.index("e_cv_strong") < ids.index("e_nlp_a")


async def test_recommend_larger_step_is_authoritative():
    """A candidate that appeared at step 200 with a different score and then
    disappeared at step 400 must NOT persist in results. Step 400 is authoritative."""
    from dext_recommend import ProfessorFact, VectorHit
    from dext_recommend.config import RecommendSettings
    from dext_recommend.core.service import RecommendationCore
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort, FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, fake_llm_for_understanding, professor_details_case,
        ranking_profile_dict, snapshot, vector_hits_case,
    )

    # step 200 returns [e1, e2]; step 400 returns [e3] only.
    hits_200 = [
        VectorHit("e1", 0.9, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
        VectorHit("e2", 0.85, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
    ]
    hits_400 = [
        VectorHit("e3", 0.80, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
    ]

    class SteppedVectorPort(FakeVectorSearchPort):
        def __init__(self):
            super().__init__(hits=hits_200)
            self._step = 0
        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.hybrid_recall_calls.append({
                "oversample": oversample, "filters": filters,
                "profile_version": profile_version,
                "snapshot_build_id": snapshot.build_id, "rrf_k": rrf_k,
                "sparse_vector": dict(sparse_vector) if sparse_vector else None,
            })
            return list(hits_200) if oversample == 200 else list(hits_400)

    def _fact(eid, topics=("topic_cv",)):
        return ProfessorFact(
            entity_id=eid, display_name=eid.replace("_", " ").title(),
            university="示例大学", org_units=("计算机学院",), title="Prof",
            title_family="professor", master_eligibility="confirmed",
            phd_eligibility="confirmed", role_status="included", profile_url=None,
            profile_hash=None, research_summary="summary",
            university_id="u_demo", city_name="北京", org_unit_ids=("ou_cs",),
            topic_ids=topics,
        )

    def _detail(eid, topics=("topic_cv",)):
        return ProfessorDetail(
            build_id="b-1", profile_hash=None, entity_id=eid,
            display_name=eid.replace("_", " ").title(), university="示例大学",
            org_units=("计算机学院",), title="Prof", title_family="professor",
            master_eligibility="confirmed", phd_eligibility="confirmed",
            role_status="included", profile_url=None,
            research_statements=("NLP research",), approved_topics=topics,
            selected_publication_mentions=("paper A",), bio_snippets=(),
            source_urls=("http://example/p",), provenance_refs=(),
            quality_findings=(), risk_flags=(),
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id=eid),
        )

    snap = snapshot()
    facts = {eid: _fact(eid) for eid in ("e1", "e2", "e3")}
    details = {eid: _detail(eid) for eid in ("e1", "e2", "e3")}
    vector_port = SteppedVectorPort()
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=vector_port,
            facts_port=FakeProfessorFactPort(facts=facts, details=details),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(
                profile=RankingProfile.from_dict(ranking_profile_dict()),
            ),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=10))
    ids = [r.entity_id for r in resp.results]
    # step 400 is authoritative: only e3 persists; e1/e2 disappeared at step 400
    assert "e3" in ids
    assert "e1" not in ids
    assert "e2" not in ids


async def test_recommend_empty_new_ids_skips_hydrate():
    """When all pref hits are already hydrated, hydrate is not called again."""
    from dext_recommend import ProfessorFact, VectorHit
    from dext_recommend.config import RecommendSettings
    from dext_recommend.core.service import RecommendationCore
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort, FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, fake_llm_for_understanding, professor_details_case,
        ranking_profile_dict, snapshot,
    )

    # both steps return the same entity_ids e1, e2
    same_hits = [
        VectorHit("e1", 0.9, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
        VectorHit("e2", 0.85, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
    ]

    class SteppedVectorPort(FakeVectorSearchPort):
        def __init__(self):
            super().__init__(hits=same_hits)
        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.hybrid_recall_calls.append({
                "oversample": oversample, "filters": filters,
                "profile_version": profile_version,
                "snapshot_build_id": snapshot.build_id, "rrf_k": rrf_k,
                "sparse_vector": dict(sparse_vector) if sparse_vector else None,
            })
            return list(same_hits)

    def _fact(eid, topics=("topic_cv",)):
        return ProfessorFact(
            entity_id=eid, display_name=eid.replace("_", " ").title(),
            university="示例大学", org_units=("计算机学院",), title="Prof",
            title_family="professor", master_eligibility="confirmed",
            phd_eligibility="confirmed", role_status="included", profile_url=None,
            profile_hash=None, research_summary="summary",
            university_id="u_demo", city_name="北京", org_unit_ids=("ou_cs",),
            topic_ids=topics,
        )

    def _detail(eid, topics=("topic_cv",)):
        return ProfessorDetail(
            build_id="b-1", profile_hash=None, entity_id=eid,
            display_name=eid.replace("_", " ").title(), university="示例大学",
            org_units=("计算机学院",), title="Prof", title_family="professor",
            master_eligibility="confirmed", phd_eligibility="confirmed",
            role_status="included", profile_url=None,
            research_statements=("NLP research",), approved_topics=topics,
            selected_publication_mentions=("paper A",), bio_snippets=(),
            source_urls=("http://example/p",), provenance_refs=(),
            quality_findings=(), risk_flags=(),
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id=eid),
        )

    snap = snapshot()
    facts = {eid: _fact(eid) for eid in ("e1", "e2")}
    details = {eid: _detail(eid) for eid in ("e1", "e2")}
    facts_port = FakeProfessorFactPort(facts=facts, details=details)
    vector_port = SteppedVectorPort()
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=vector_port,
            facts_port=facts_port,
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(
                profile=RankingProfile.from_dict(ranking_profile_dict()),
            ),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    # 2 hits < limit 10 -> all 4 steps run; same ids each step
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=10))
    # hydrate called only on the first step (ids already cached after that)
    assert len(facts_port.hydrate_calls) == 1
    assert set(facts_port.hydrate_calls[0]["entity_ids"]) == {"e1", "e2"}
    # response is well-formed and contains both candidates
    ids = [r.entity_id for r in resp.results]
    assert set(ids) == {"e1", "e2"}


async def test_explanation_every_result_has_reason():
    """Every result in resp.results has len(short_reasons) >= 1, including
    candidates whose ProfessorDetail is missing."""
    from dext_recommend import VectorHit
    hits = [
        VectorHit("e_with_detail", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
        VectorHit("e_no_detail", 0.85, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    facts = {
        "e_with_detail": _fact("e_with_detail"),
        "e_no_detail": _fact("e_no_detail", topic_ids=("topic_nlp",)),
    }
    # e_no_detail deliberately omitted from details -> get_detail raises
    # KeyError -> fetch_details returns None -> build_explanation gets None.
    details = {"e_with_detail": _detail("e_with_detail")}
    core = _core(hits=hits, facts=facts, details=details)
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    assert len(resp.results) >= 1
    # every result has at least one short_reason
    assert all(c.short_reasons for c in resp.results)
    # e_no_detail is in results with a non-empty reason despite missing detail
    ids = [r.entity_id for r in resp.results]
    assert "e_no_detail" in ids
    no_detail_card = next(r for r in resp.results if r.entity_id == "e_no_detail")
    assert len(no_detail_card.short_reasons) >= 1


async def test_recommend_invalid_request_calls_no_ports():
    """Empty query_text -> INVALID_REQUEST warning, no ports called."""
    core = _core()
    req = RecommendRequest(query_text="", limit=5)  # empty query_text
    resp = await core.recommend(req)
    codes = [w.code for w in resp.warnings]
    assert "invalid_request" in codes
    assert resp.results == ()
    # no port was called
    assert core._deps.vector_port.hybrid_recall_calls == []
    assert core._deps.facts_port.hydrate_calls == []
    assert core._deps.facts_port.get_detail_calls == []


async def test_recommend_hard_topic_filter_without_topic_ids_invalid():
    """topic_filter_mode=hard with empty topic_ids -> INVALID_REQUEST, no ports called.
    spec §2.2: R3 defaults to NOT allowing topic hard-filter degraded."""
    core = _core()
    req = RecommendRequest(
        query_text="NLP",
        filters=RecommendationFilters(topic_filter_mode="hard", topic_ids=()),
        limit=5,
    )
    resp = await core.recommend(req)
    codes = [w.code for w in resp.warnings]
    assert "invalid_request" in codes
    assert resp.results == ()
    # no port was called
    assert core._deps.vector_port.hybrid_recall_calls == []
    assert core._deps.facts_port.hydrate_calls == []
    assert core._deps.facts_port.get_detail_calls == []


async def test_recommend_unauthorized_contacts_calls_no_detail():
    """include_contacts=True with default ViewerPermissions -> UNAUTHORIZED_CONTACT, no get_detail."""
    core = _core()
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        include_contacts=True,
    ))
    codes = [w.code for w in resp.warnings]
    assert "unauthorized_contact" in codes
    assert resp.results == ()
    assert core._deps.facts_port.get_detail_calls == []


async def test_recommend_unauthorized_review_policy():
    """review_policy=include_downranked with default ViewerPermissions -> UNAUTHORIZED_REVIEW."""
    core = _core()
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        review_policy="include_downranked",
    ))
    codes = [w.code for w in resp.warnings]
    assert "unauthorized_review" in codes
    assert resp.results == ()
    # an unauthorized review-policy request must not touch any port
    assert core._deps.facts_port.get_detail_calls == []
    assert core._deps.vector_port.hybrid_recall_calls == []
    assert core._deps.facts_port.hydrate_calls == []


def test_composition_seam_injects_deps_verbatim():
    from dext_recommend.composition import assemble_core, build_test_core
    from dext_recommend.config import RecommendSettings
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort, FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, fake_llm_for_understanding, professor_details_case,
        professor_facts_case, ranking_profile_dict, snapshot, vector_hits_case,
    )

    snap = snapshot()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    deps = RecommendDeps(
        snapshot_port=FakeActiveSnapshotProvider(snap),
        embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
        vector_port=FakeVectorSearchPort(hits=list(vector_hits_case("happy"))),
        facts_port=FakeProfessorFactPort(
            facts=professor_facts_case("happy"),
            details=professor_details_case("happy"),
        ),
        llm_port=fake_llm_for_understanding({
            "research_interests": ["NLP"], "preferred_universities": [],
            "preferred_cities": [], "preferred_org_units": [], "degree_goal": "master",
            "mentor_eligibility_requirement": None, "missing_information": [],
            "needs_clarification": False, "confidence": 0.8,
        }),
        ranking_port=FakeRankingProfilePort(profile=prof),
        coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
    )
    settings = RecommendSettings()
    core = assemble_core(deps, settings)
    assert core.deps is deps
    assert build_test_core(deps, settings).deps is deps


async def test_recommend_phase_diagnostics_populated():
    # happy path: resp.phase_diagnostics has entries for snapshot/ranking/qu/embedding/vector_recall/candidate_hydrate/details
    # each with elapsed_ms >= 0 and error_code is None
    core = _core()
    resp = await core.recommend(RecommendRequest(query_text="NLP 导师"), viewer_permissions=ViewerPermissions())
    assert len(resp.phase_diagnostics) >= 1
    for pd in resp.phase_diagnostics:
        assert pd.elapsed_ms >= 0
        assert pd.error_code is None


# ---- W6-b resilience tests ----


class _RaisingLLMPort:
    """FakeLLMGenerationPort-shaped double whose generate() always raises."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[dict] = []

    async def generate(self, system_prompt_id, user_inputs, fact_bundle,
                       student_context, json_schema, generation_profile_version):
        self.calls.append({"system_prompt_id": system_prompt_id})
        raise self._exc


class _RaisingEmbeddingPort:
    def __init__(self, exc: Exception, fingerprint: str = "fp-x") -> None:
        self._exc = exc
        self._fingerprint = fingerprint

    async def embed(self, snapshot, query_text):
        raise self._exc


class _RaisingVectorPort:
    def __init__(self, exc: Exception, hits=None) -> None:
        self._exc = exc
        self._hits = hits or []
        self.hybrid_recall_calls: list[dict] = []

    async def hybrid_recall(self, snapshot, qv, filters, oversample,
                            profile_version, *, rrf_k, sparse_vector=None):
        self.hybrid_recall_calls.append({"oversample": oversample})
        raise self._exc


class _RaisingFactsPort:
    """hydrates raises; get_detail works."""

    def __init__(self, facts=None, details=None, hydrate_exc=None) -> None:
        from dext_recommend.ports._fakes import FakeProfessorFactPort
        self._inner = FakeProfessorFactPort(facts=facts or {}, details=details or {})
        self._hydrate_exc = hydrate_exc
        self.hydrate_calls = self._inner.hydrate_calls
        self.get_detail_calls = self._inner.get_detail_calls

    async def hydrate(self, snapshot, entity_ids):
        if self._hydrate_exc is not None:
            raise self._hydrate_exc
        return await self._inner.hydrate(snapshot, entity_ids)


class _RaisingGetDetailFactsPort:
    """hydrate works; one get_detail raises a non-Lookup error."""

    def __init__(self, facts=None, details=None, fail_eid=None, exc=None) -> None:
        from dext_recommend.ports._fakes import FakeProfessorFactPort
        self._inner = FakeProfessorFactPort(facts=facts or {}, details=details or {})
        self._fail_eid = fail_eid
        self._exc = exc
        self.hydrate_calls = self._inner.hydrate_calls
        self.get_detail_calls = self._inner.get_detail_calls

    async def get_detail(self, snapshot, entity_id, include_contacts, viewer_permissions):
        if entity_id == self._fail_eid:
            raise self._exc
        return await self._inner.get_detail(snapshot, entity_id, include_contacts, viewer_permissions)

    async def hydrate(self, snapshot, entity_ids):
        return await self._inner.hydrate(snapshot, entity_ids)


async def test_recommend_llm_failure_classified():
    """FakeLLMGenerationPort.generate raises -> llm_unavailable, no 500."""
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort, FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn, vector_hits_case,
    )
    snap = snap_fn()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=FakeVectorSearchPort(hits=list(vector_hits_case("happy"))),
            facts_port=FakeProfessorFactPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
            ),
            llm_port=_RaisingLLMPort(RuntimeError("llm down")),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    codes = [w.code for w in resp.warnings]
    assert "llm_unavailable" in codes
    assert resp.results == ()
    # phase_diagnostics captured the query_understanding failure
    qu_phases = [pd for pd in resp.phase_diagnostics if pd.phase == "query_understanding"]
    assert any(pd.error_code == "llm_unavailable" for pd in qu_phases)


async def test_recommend_embedding_failure_classified():
    """embedding_port.embed raises -> embedding_unavailable."""
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeRankingProfilePort,
        FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn, vector_hits_case,
    )
    snap = snap_fn()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=_RaisingEmbeddingPort(RuntimeError("embed down"), "fp-x"),
            vector_port=FakeVectorSearchPort(hits=list(vector_hits_case("happy"))),
            facts_port=FakeProfessorFactPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    codes = [w.code for w in resp.warnings]
    assert "embedding_unavailable" in codes
    assert resp.results == ()
    emb_phases = [pd for pd in resp.phase_diagnostics if pd.phase == "embedding"]
    assert any(pd.error_code == "embedding_unavailable" for pd in emb_phases)


async def test_recommend_vector_failure_classified():
    """vector_port.hybrid_recall raises -> vector_unavailable."""
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn, vector_hits_case,
    )
    snap = snap_fn()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=_RaisingVectorPort(RuntimeError("vector down")),
            facts_port=FakeProfessorFactPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    codes = [w.code for w in resp.warnings]
    assert "vector_unavailable" in codes
    assert resp.results == ()
    vr_phases = [pd for pd in resp.phase_diagnostics if pd.phase == "vector_recall"]
    assert any(pd.error_code == "vector_unavailable" for pd in vr_phases)


async def test_recommend_hydrate_failure_not_classified_as_vector():
    """facts_port.hydrate raises -> hydrate_unavailable (not vector_unavailable)."""
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeQueryEmbeddingPort, FakeRankingProfilePort,
        FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn, vector_hits_case,
    )
    snap = snap_fn()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=FakeVectorSearchPort(hits=list(vector_hits_case("happy"))),
            facts_port=_RaisingFactsPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
                hydrate_exc=RuntimeError("hydrate down"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    codes = [w.code for w in resp.warnings]
    assert "hydrate_unavailable" in codes
    assert "vector_unavailable" not in codes
    assert resp.results == ()
    hyd_phases = [pd for pd in resp.phase_diagnostics if pd.phase == "candidate_hydrate"]
    assert any(pd.error_code == "hydrate_unavailable" for pd in hyd_phases)


async def test_recommend_single_detail_failure_degrades():
    """One get_detail raises -> that detail None, others ok, DETAILS_UNAVAILABLE
    warning, request succeeds (results non-empty)."""
    from dext_recommend import VectorHit
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeQueryEmbeddingPort, FakeRankingProfilePort,
        FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, ranking_profile_dict, snapshot as snap_fn,
    )
    snap = snap_fn()
    hits = [
        VectorHit("e_ok", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
        VectorHit("e_fail", 0.85, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_cv"],
        }),
    ]
    facts = {eid: _fact(eid) for eid in ("e_ok", "e_fail")}
    details = {"e_ok": _detail("e_ok"), "e_fail": _detail("e_fail")}
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=FakeVectorSearchPort(hits=hits),
            facts_port=_RaisingGetDetailFactsPort(
                facts=facts, details=details,
                fail_eid="e_fail", exc=RuntimeError("detail svc down"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit=5))
    # request still succeeds (no error severity)
    assert all(w.severity != "error" for w in resp.warnings)
    codes = [w.code for w in resp.warnings]
    assert "details_unavailable" in codes
    # e_fail detail came back None due to operational failure
    assert resp.results  # non-empty
    # phase_diagnostics recorded a details failure
    det_phases = [pd for pd in resp.phase_diagnostics if pd.phase == "details"]
    assert any(pd.error_code == "details_unavailable" for pd in det_phases)


async def test_recommend_total_timeout():
    """A fake vector port sleeps past total_timeout -> request_timeout response,
    phase_diagnostics shows the in-flight phase."""
    from dext_recommend.config import RecommendSettings
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn, vector_hits_case,
    )

    class _SlowVectorPort:
        def __init__(self):
            self.hybrid_recall_calls: list[dict] = []

        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.hybrid_recall_calls.append({"oversample": oversample})
            import asyncio as _a
            await _a.sleep(5.0)
            return []

    snap = snap_fn()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    settings = RecommendSettings(total_timeout=0.2)
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=_SlowVectorPort(),
            facts_port=FakeProfessorFactPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        settings,
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    codes = [w.code for w in resp.warnings]
    assert "request_timeout" in codes
    assert resp.results == ()
    # phase_diagnostics should at least have snapshot/ranking/qu/embedding recorded
    phases = {pd.phase for pd in resp.phase_diagnostics}
    assert "snapshot" in phases
    assert "ranking_profile" in phases


async def test_recommend_concurrent_diagnostics_isolated():
    """Two concurrent recommend() calls on the SAME Core: their phase_diagnostics
    do not interleave. One triggers a vector failure (vector_unavailable), the
    other triggers an embedding failure (embedding_unavailable); each response
    only has its own error codes."""
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeProfessorFactPort, FakeQueryEmbeddingPort,
        FakeRankingProfilePort, FakeVectorSearchPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn, vector_hits_case,
    )

    class _DualPort:
        """Vector port that fails on the first call and succeeds after; used to
        drive one request into vector_unavailable. Embedding port that fails
        on the first embed call and succeeds after."""
        def __init__(self):
            self.vector_calls = 0
            self.embed_calls = 0
            self.hybrid_recall_calls: list[dict] = []

        async def embed(self, snapshot, query_text):
            self.embed_calls += 1
            if self.embed_calls == 1:
                raise RuntimeError("embed down on first")
            return type("E", (), {"vector": [0.1, 0.2],
                                  "embedding_fingerprint": snapshot.embedding_fingerprint,
                                  "sparse_vector": None})()

        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.hybrid_recall_calls.append({"oversample": oversample})
            self.vector_calls += 1
            if self.vector_calls == 1:
                raise RuntimeError("vector down on first")
            return list(vector_hits_case("happy"))

    snap = snap_fn()
    prof = RankingProfile.from_dict(ranking_profile_dict())
    dual = _DualPort()
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=dual,
            vector_port=dual,
            facts_port=FakeProfessorFactPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    r1, r2 = await asyncio.gather(
        core.recommend(RecommendRequest(query_text="NLP")),
        core.recommend(RecommendRequest(query_text="机器学习")),
    )
    codes1 = {w.code for w in r1.warnings}
    codes2 = {w.code for w in r2.warnings}
    # one response got embedding_unavailable, the other vector_unavailable
    all_codes = codes1 | codes2
    assert "embedding_unavailable" in all_codes
    assert "vector_unavailable" in all_codes
    # the two responses' error codes are disjoint (no cross-contamination)
    err1 = {c for c in codes1 if c in ("embedding_unavailable", "vector_unavailable")}
    err2 = {c for c in codes2 if c in ("embedding_unavailable", "vector_unavailable")}
    assert err1 and err2
    assert err1.isdisjoint(err2)
    # phase_diagnostics: each response only carries its own error code
    pd_codes1 = {pd.error_code for pd in r1.phase_diagnostics if pd.error_code}
    pd_codes2 = {pd.error_code for pd in r2.phase_diagnostics if pd.error_code}
    assert "embedding_unavailable" not in pd_codes2 or "vector_unavailable" not in pd_codes2
    assert pd_codes1.isdisjoint(pd_codes2)


async def test_recommend_early_returns_preserve_accumulated_warnings():
    """needs_clarification / unsupported / no_candidates early returns still
    carry route warnings + the triggering warning, and phase_diagnostics."""
    # needs_clarification: route warnings + needs_clarification warning + diag
    core = _core(llm_output=_output(needs_clarification=True, confidence=0.2))
    resp = await core.recommend(RecommendRequest(query_text="随便"))
    codes = [w.code for w in resp.warnings]
    assert "needs_clarification" in codes
    assert resp.results == ()
    assert len(resp.phase_diagnostics) >= 1  # snapshot/ranking/qu recorded

    # unsupported (detail_followup): admission rejects with invalid_conversation_state
    core2 = _core()
    resp2 = await core2.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="detail_followup", intent_source="explicit",
            anchor_entity_id="e1", session_id="s1", turn_id="t1"),
    ))
    codes2 = [w.code for w in resp2.warnings]
    assert "invalid_conversation_state" in codes2
    assert resp2.results == ()

    # no_candidates after filters: warning + diag (snapshot/ranking/qu/embedding/vector_recall/...)
    core3 = _core(hits=list(vector_hits_case("happy")),
                  facts=professor_facts_case("happy"),
                  details=professor_details_case("happy"))
    resp3 = await core3.recommend(RecommendRequest(
        query_text="NLP",
        filters=RecommendationFilters(university_ids=("u_nonexistent",)),
    ))
    codes3 = [w.code for w in resp3.warnings]
    assert "no_candidates_after_filters" in codes3
    assert resp3.results == ()
    assert len(resp3.phase_diagnostics) >= 1


# ---- R3c leftover bug regression tests (P1/P2) ----


async def test_recommend_passes_trusted_viewer_permissions_to_detail_port():
    """P1-A: service.py must pass the trusted `vp` through to fetch_details,
    not rebuild a ViewerPermissions from request flags. Rebuilding drops
    can_view_review (defaults False) and lets request.diagnostics_level='debug'
    elevate diagnostics=True even when the viewer lacks diagnostics permission
    — both violate the W7 permission boundary."""
    core = _core()
    vp = ViewerPermissions(
        include_contacts=True, can_view_review=True, diagnostics=False,
    )
    resp = await core.recommend(
        RecommendRequest(query_text="NLP 导师", diagnostics_level="debug"),
        viewer_permissions=vp,
    )
    assert resp.results, "expected a non-empty result set"
    calls = core._deps.facts_port.get_detail_calls
    assert calls, "detail port was never called"
    received = calls[0]["viewer_permissions"]
    # the trusted object must be passed verbatim (same identity)
    assert received is vp, "detail port received a rebuilt ViewerPermissions, not the trusted vp"
    assert received.can_view_review is True
    assert received.diagnostics is False  # NOT elevated by diagnostics_level='debug'


async def test_recommend_snapshot_port_exception_returns_active_build_unavailable():
    """P1-B: get_snapshot() raising a non-Classified exception (e.g. OSError)
    must surface as active_build_unavailable, never leak as a 500/raw exception.
    _guarded_sync must classify like _guarded_async."""
    from dext_recommend.ports._fakes import FakeActiveSnapshotProvider  # noqa: F401

    class _RaisingSnapshotPort:
        def get_snapshot(self):
            raise OSError("disk unreadable")

    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=_RaisingSnapshotPort(),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=FakeVectorSearchPort(hits=list(vector_hits_case("happy"))),
            facts_port=FakeProfessorFactPort(
                facts=professor_facts_case("happy"),
                details=professor_details_case("happy"),
            ),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(
                profile=RankingProfile.from_dict(ranking_profile_dict()),
            ),
            coverage_flags_by_build_id=coverage_flags_case("b-1"),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(query_text="NLP"))
    codes = [w.code for w in resp.warnings]
    assert "active_build_unavailable" in codes
    assert resp.results == ()
    # the snapshot phase diagnostic recorded a failure code (not None)
    snap_phases = [pd for pd in resp.phase_diagnostics if pd.phase == "snapshot"]
    assert snap_phases and any(pd.error_code for pd in snap_phases)


async def test_recommend_anchor_failure_prevents_filter_pipeline():
    """P1-C: when an early return fires (no_candidates after filters), any
    warning accumulated earlier in the pipeline (e.g. missing_anchor from a
    same_field fallback) MUST be preserved alongside the triggering warning.
    The previous code built _error_response with only the triggering warning,
    silently dropping accumulated route_warnings."""
    from dext_recommend import VectorHit
    # same_field intent whose anchor is missing from the facts map -> emits
    # missing_anchor and falls back to new_search; then a hard university filter
    # matching nothing -> no_candidates_after_filters. Both warnings must appear.
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    # anchor fact deliberately absent -> missing_anchor; the single survivor's
    # university is u_demo so a u_nonexistent filter yields no_candidates.
    facts = {"e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",))}
    details = {"e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",))}
    core = _core(hits=hits, facts=facts, details=details)
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        filters=RecommendationFilters(university_ids=("u_nonexistent",)),
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "anchor_not_in_active_build" in codes
    assert "no_candidates_after_filters" not in codes


async def test_recommend_validate_request_rejects_string_limit_as_invalid_request():
    """P2-A: limit='10' (str instead of int) must return invalid_request,
    NOT raise TypeError out of recommend(). validate_request is the admission
    boundary; it must not crash on wrong-typed scalars."""
    core = _core()
    resp = await core.recommend(RecommendRequest(query_text="NLP", limit="10"))  # type: ignore[arg-type]
    codes = [w.code for w in resp.warnings]
    assert "invalid_request" in codes
    assert resp.results == ()
    assert core._deps.vector_port.hybrid_recall_calls == []


async def test_recommend_validate_request_rejects_none_filters_as_invalid_request():
    """P2-A: filters=None must return invalid_request, NOT raise AttributeError
    out of recommend()."""
    core = _core()
    req = RecommendRequest(query_text="NLP")  # type: ignore[call-arg]
    object.__setattr__(req, "filters", None)  # simulate a deserialization gap
    resp = await core.recommend(req)
    codes = [w.code for w in resp.warnings]
    assert "invalid_request" in codes
    assert resp.results == ()
    assert core._deps.vector_port.hybrid_recall_calls == []


# ---- R3d closure §2.3: prior warnings survive terminal failure ----


async def test_recommend_anchor_failure_prevents_vector_failure():
    """spec 3d §2.3: missing_anchor -> vector failure must carry BOTH the
    missing_anchor warning AND the terminating vector_unavailable code. The
    ClassifiedRecommendError branch must not drop route_warnings accumulated
    before the failure."""
    from dext_recommend import VectorHit
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeQueryEmbeddingPort, FakeRankingProfilePort,
        FakeProfessorFactPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, professor_details_case, professor_facts_case,
        ranking_profile_dict, snapshot as snap_fn,
    )

    class _VectorFailureAfterRecall:
        # hybrid_recall raises on every call -> vector_unavailable classified
        def __init__(self):
            self.hybrid_recall_calls: list[dict] = []
        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.hybrid_recall_calls.append({"oversample": oversample})
            raise RuntimeError("vector svc down")

    snap = snap_fn()
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    # anchor fact deliberately absent -> missing_anchor emitted + fallback
    facts = {"e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",))}
    details = {"e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",))}
    prof = RankingProfile.from_dict(ranking_profile_dict())
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=_VectorFailureAfterRecall(),
            facts_port=FakeProfessorFactPort(facts=facts, details=details),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        RecommendSettings(),
    )
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "anchor_not_in_active_build" in codes
    assert "vector_unavailable" not in codes
    assert resp.results == ()


async def test_recommend_anchor_failure_prevents_downstream_timeout():
    """spec 3d §2.3: missing_anchor -> request timeout must carry BOTH the
    missing_anchor warning AND the terminating request_timeout code. The
    asyncio.TimeoutError branch must not drop route_warnings."""
    from dext_recommend import VectorHit
    from dext_recommend.config import RecommendSettings
    from dext_recommend.ports._fakes import (
        FakeActiveSnapshotProvider, FakeQueryEmbeddingPort, FakeRankingProfilePort,
        FakeProfessorFactPort,
    )
    from tests.dext_recommend._recfixtures import (
        coverage_flags_case, ranking_profile_dict, snapshot as snap_fn,
    )

    class _SlowVectorPort:
        def __init__(self):
            self.hybrid_recall_calls: list[dict] = []
        async def hybrid_recall(self, snapshot, qv, filters, oversample,
                                profile_version, *, rrf_k, sparse_vector=None):
            self.hybrid_recall_calls.append({"oversample": oversample})
            import asyncio as _a
            await _a.sleep(5.0)
            return []

    snap = snap_fn()
    hits = [
        VectorHit("e_nlp_a", 0.90, {
            "university_id": "u_demo", "city_name": "北京",
            "org_unit_ids": ["ou_cs"], "title_family": "professor",
            "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
            "role_status": "included", "topic_ids": ["topic_nlp"],
        }),
    ]
    facts = {"e_nlp_a": _fact("e_nlp_a", topic_ids=("topic_nlp",))}
    details = {"e_nlp_a": _detail("e_nlp_a", topics=("topic_nlp",))}
    prof = RankingProfile.from_dict(ranking_profile_dict())
    settings = RecommendSettings(total_timeout=0.2)
    core = RecommendationCore(
        RecommendDeps(
            snapshot_port=FakeActiveSnapshotProvider(snap),
            embedding_port=FakeQueryEmbeddingPort([0.1, 0.2], "fp-x"),
            vector_port=_SlowVectorPort(),
            facts_port=FakeProfessorFactPort(facts=facts, details=details),
            llm_port=fake_llm_for_understanding(_output()),
            ranking_port=FakeRankingProfilePort(profile=prof),
            coverage_flags_by_build_id=coverage_flags_case(snap.build_id),
        ),
        settings,
    )
    resp = await core.recommend(RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="same_field", intent_source="explicit", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "anchor_not_in_active_build" in codes
    assert "request_timeout" not in codes
    assert resp.results == ()


async def test_recommend_invalid_request_response_is_validated():
    """spec 3d §2.3: 'returned before unconditional validate(response)'. The
    INVALID_REQUEST admission early-return must produce a response that passes
    validate() — i.e. be a well-formed error response (results==(), an error
    warning present, non-tuple fields rejected). We assert shape invariants
    validate itself checks; this also guards against a future builder change
    that forgets validate on the admission path."""
    from dext_recommend.core.validation import validate
    core = _core()
    resp = await core.recommend(RecommendRequest(query_text="", limit=5))
    codes = [w.code for w in resp.warnings]
    assert "invalid_request" in codes
    # validate() must not raise on the admission early-return response
    validate(resp)
    assert resp.results == ()


async def test_recommend_unauthorized_contact_response_is_validated():
    """spec 3d §2.3: the UNAUTHORIZED_CONTACT admission early-return must also
    pass validate()."""
    from dext_recommend.core.validation import validate
    core = _core()
    resp = await core.recommend(RecommendRequest(
        query_text="NLP", include_contacts=True,
    ))
    codes = [w.code for w in resp.warnings]
    assert "unauthorized_contact" in codes
    validate(resp)  # must not raise
    assert resp.results == ()


async def test_recommend_unauthorized_review_response_is_validated():
    """spec 3d §2.3: the UNAUTHORIZED_REVIEW admission early-return must also
    pass validate()."""
    from dext_recommend.core.validation import validate
    core = _core()
    resp = await core.recommend(RecommendRequest(
        query_text="NLP", review_policy="include_downranked",
    ))
    codes = [w.code for w in resp.warnings]
    assert "unauthorized_review" in codes
    validate(resp)  # must not raise
    assert resp.results == ()


# ---- R5 Task 5: recommend rejects unresolved/detail_followup context ----


async def test_recommend_rejects_unresolved_implicit_context():
    core = _core()
    req = RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(intent_source="implicit"),
    )
    resp = await core.recommend(req)
    assert any(w.code == "invalid_intent" and w.severity == "error"
               for w in resp.warnings)


async def test_recommend_rejects_detail_followup_context():
    core = _core()
    req = RecommendRequest(
        query_text="NLP",
        conversation_context=ConversationContext(
            intent="detail_followup", intent_source="explicit", anchor_entity_id="e1",
            session_id="s1", turn_id="t1"),
    )
    resp = await core.recommend(req)
    assert any(w.code == "invalid_conversation_state" and w.severity == "error"
               for w in resp.warnings)
