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

