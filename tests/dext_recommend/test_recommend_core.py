from __future__ import annotations

from pathlib import Path

import pytest

from dext_recommend import (
    FakeProfessorFactPort, FakeQueryEmbeddingPort, FakeVectorSearchPort,
    ProfessorDetail, RecommendRequest, RecommendResponse,
    RecommendationFilters, RecommendationWarning, ViewerPermissions,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.service import RecommendDeps, RecommendationCore
from dext_recommend.ports._fakes import (
    FakeActiveSnapshotProvider, FakeRankingProfilePort,
)
from dext_recommend.models import ConversationContext

from tests.dext_recommend._recfixtures import (
    coverage_flags_case, fake_llm_for_understanding, professor_details_case,
    professor_facts_case, ranking_profile_dict, snapshot, vector_hits_case,
)


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
        conversation_context=ConversationContext(intent="detail_followup"),
    ))
    assert resp.results == ()
    assert any(w.code == "unsupported_for_recommend_core" for w in resp.warnings)
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
    """spec §8.1 #11: refine_direction merges QU preferred_* into effective
    filters when request.filters leaves them empty."""
    core = _core(
        llm_output=_output(
            preferred_universities=["u_demo"],
            mentor_eligibility_requirement="confirmed",
        ),
    )
    resp = await core.recommend(RecommendRequest(
        query_text="换方向",
        filters=RecommendationFilters(),  # all defaults: empty + any
        conversation_context=ConversationContext(intent="refine_direction"),
    ))
    # the merge happened: hybrid_recall saw the merged filters
    assert core._deps.vector_port.hybrid_recall_calls
    eff = core._deps.vector_port.hybrid_recall_calls[0]["filters"]
    assert eff.university_ids == ("u_demo",)
    assert eff.master_eligibility == "confirmed"
    # response is well-formed (success or clean no_candidates)
    assert isinstance(resp, RecommendResponse)


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
            intent="same_field", anchor_entity_id="e_nlp_anchor",
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


async def test_recommend_same_field_anchor_missing_falls_back():
    """Anchor not in ACTIVE build (fact None) -> missing_anchor warning + new_search."""
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
            intent="same_field", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "missing_anchor" in codes
    # fell back to new_search: anchor NOT excluded, e_nlp_a returned
    ids = [r.entity_id for r in resp.results]
    assert "e_nlp_a" in ids


async def test_recommend_same_field_anchor_excluded_falls_back():
    """Anchor role_status=excluded -> missing_anchor warning + new_search."""
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
            intent="same_field", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "missing_anchor" in codes


async def test_recommend_same_field_anchor_without_topics_falls_back():
    """Anchor exists but topic_ids=() -> missing_anchor warning + new_search."""
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
            intent="same_field", anchor_entity_id="e_nlp_anchor",
        ),
    ))
    codes = [w.code for w in resp.warnings]
    assert "missing_anchor" in codes


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
            intent="same_field", anchor_entity_id="e_nlp_anchor",
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
    from dext_recommend.core.service import RecommendDeps, RecommendationCore
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
    from dext_recommend.core.service import RecommendDeps, RecommendationCore
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

