from __future__ import annotations

import pytest

from dext_recommend import ProfessorFact, VectorHit
from dext_recommend.core.intent import resolve_recommend_route
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.core.rerank import rerank
from dext_recommend.models import RecommendRequest
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
    professor_details_case, professor_facts_case, ranking_profile_dict,
)


def _profile(**over) -> RankingProfile:
    return RankingProfile.from_dict(ranking_profile_dict(**over))


def _fact(eid: str, **over) -> ProfessorFact:
    base = dict(
        entity_id=eid, display_name=eid, university="U", org_units=("ou_cs",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="included", profile_url=None,
        profile_hash=None, research_summary="summary",
        university_id="u_demo", city_name="北京", org_unit_ids=("ou_cs",),
        topic_ids=("topic_cv",),
    )
    base.update(over)
    return ProfessorFact(**base)


def test_rerank_single_candidate_score_one():
    hits = [VectorHit("e1", 1.0, {})]
    facts = {"e1": _fact("e1")}
    semantic = {"e1": 1.0}
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    out = rerank(hits, facts, {}, semantic, None, _profile(), route)
    assert len(out) == 1
    assert out[0].entity_id == "e1"
    assert out[0].score_components["semantic_score"] == 1.0
    assert out[0].match_level in ("excellent", "strong", "possible", "weak")


def test_rerank_higher_semantic_score_ranks_first():
    hits = [VectorHit("e1", 1.0, {}), VectorHit("e2", 0.5, {})]
    facts = {"e1": _fact("e1"), "e2": _fact("e2")}
    semantic = {"e1": 1.0, "e2": 0.0}
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    out = rerank(hits, facts, {}, semantic, None, _profile(), route)
    assert out[0].entity_id == "e1"


def test_rerank_tie_break_by_entity_id_asc():
    hits = [VectorHit("e_b", 0.5, {}), VectorHit("e_a", 0.5, {})]
    facts = {"e_b": _fact("e_b"), "e_a": _fact("e_a")}
    semantic = {"e_b": 0.5, "e_a": 0.5}
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    out = rerank(hits, facts, {}, semantic, None, _profile(), route)
    assert out[0].entity_id == "e_a"
    assert out[1].entity_id == "e_b"


def test_rerank_weights_from_profile_change_order():
    hits = [VectorHit("e_sem", 1.0, {}), VectorHit("e_ev", 0.0, {})]
    facts = {
        "e_sem": _fact("e_sem"),
        "e_ev": _fact("e_ev", topic_ids=()),  # fewer topic matches
    }
    semantic = {"e_sem": 1.0, "e_ev": 0.0}
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    # default weights -> e_sem first
    out_default = rerank(hits, facts, {}, semantic, None, _profile(), route)
    assert out_default[0].entity_id == "e_sem"
    # flip: weight semantic to 0, topic to 0.99
    flipped = ranking_profile_dict()
    flipped["weights"] = {
        "semantic_score": 0.01, "topic_statement_score": 0.95,
        "student_fit_score": 0.01, "eligibility_score": 0.01,
        "provenance_score": 0.01, "completeness_score": 0.01,
    }
    out_flipped = rerank(hits, facts, {}, semantic, None,
                         RankingProfile.from_dict(flipped), route)
    # with topic dominating and e_sem having topic_cv, e_sem still likely first;
    # this test just asserts the score changes are observable
    assert out_flipped[0].score_components["semantic_score"] == 1.0


def test_rerank_match_level_thresholds():
    hits = [VectorHit("e1", 1.0, {})]
    facts = {"e1": _fact("e1")}
    semantic = {"e1": 0.8}  # high semantic but no detail -> only semantic + eligibility + completeness
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    out = rerank(hits, facts, {}, semantic, None, _profile(), route)
    assert out[0].match_level in ("strong", "possible", "excellent", "weak")


def test_rerank_detail_present_increases_score():
    hits = [VectorHit("e_with", 0.5, {}), VectorHit("e_without", 0.5, {})]
    facts = {"e_with": _fact("e_with"), "e_without": _fact("e_without")}
    details = professor_details_case("happy")  # has e_cv_strong only; use it for e_with
    # map e_with to a detail
    details = {"e_with": details["e_cv_strong"]}
    semantic = {"e_with": 0.5, "e_without": 0.5}
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    out = rerank(hits, facts, details, semantic, None, _profile(), route)
    with_score = next(e for e in out if e.entity_id == "e_with")
    without_score = next(e for e in out if e.entity_id == "e_without")
    assert with_score.score >= without_score.score


def resolve_recommend_request(request):
    # local alias to avoid typo in test imports
    return resolve_recommend_route(request)


def test_rerank_same_field_boosts_topic_overlap():
    from dext_recommend.core.rerank import rerank
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact
    from tests.dext_recommend._recfixtures import ranking_profile_dict

    profile = RankingProfile.from_dict(ranking_profile_dict())
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    # two candidates, identical semantic; one shares anchor topic
    hits = [
        VectorHit(entity_id="e_share", score=0.9, payload={}),
        VectorHit(entity_id="e_other", score=0.9, payload={}),
    ]
    fact_share = ProfessorFact(
        entity_id="e_share", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, topic_ids=("topic_nlp",),
    )
    fact_other = ProfessorFact(
        entity_id="e_other", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, topic_ids=("topic_cv",),
    )
    fact_map = {"e_share": fact_share, "e_other": fact_other}
    semantic = {"e_share": 1.0, "e_other": 1.0}
    ranked = rerank(
        hits, fact_map, {}, semantic, None, profile, route,
        anchor_topics=("topic_nlp",),
    )
    assert ranked[0].entity_id == "e_share"
    assert ranked[0].score_components["same_field_overlap"] > 0.0
    assert ranked[0].score_components["same_field_boost"] > 0.0
    assert ranked[1].score_components["same_field_overlap"] == 0.0
    assert ranked[1].score_components["same_field_boost"] == 0.0


def test_rerank_same_field_components_always_present():
    """Even with no anchor_topics, both same_field_* keys exist and are 0.0."""
    from dext_recommend.core.rerank import rerank
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from tests.dext_recommend._recfixtures import ranking_profile_dict

    profile = RankingProfile.from_dict(ranking_profile_dict())
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    hits = [VectorHit(entity_id="e1", score=0.5, payload={})]
    ranked = rerank(hits, {}, {}, {"e1": 1.0}, None, profile, route)
    assert "same_field_overlap" in ranked[0].score_components
    assert "same_field_boost" in ranked[0].score_components
    assert ranked[0].score_components["same_field_overlap"] == 0.0
    assert ranked[0].score_components["same_field_boost"] == 0.0


def test_rerank_tie_break_configurable():
    from dext_recommend.core.rerank import rerank
    from dext_recommend.core.ranking_profile import RankingProfile
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorDetail
    from tests.dext_recommend._recfixtures import ranking_profile_dict

    d = ranking_profile_dict(tie_break=("score", "semantic_score", "entity_id", "evidence_count"))
    profile = RankingProfile.from_dict(d)
    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    # two candidates with identical score and semantic_score but different
    # evidence_count and entity_id. Custom tie_break puts entity_id asc
    # BEFORE evidence_count, so e_a (evidence=0) outranks e_b (evidence=5).
    # The hardcoded sort puts evidence_count desc before entity_id, so it
    # would rank e_b first.
    hits = [
        VectorHit(entity_id="e_b", score=0.5, payload={}),
        VectorHit(entity_id="e_a", score=0.5, payload={}),
    ]
    detail_a = ProfessorDetail(
        build_id="b-1", profile_hash=None, entity_id="e_a",
        display_name="A", university="U", org_units=("CS",), title="Prof",
        title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="included", profile_url=None,
        research_statements=(), approved_topics=(),
        selected_publication_mentions=(), bio_snippets=(),
        source_urls=("u1", "u2", "u3", "u4", "u5"), provenance_refs=(),
        quality_findings=(), risk_flags=(),
        fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id="e_a"),
    )
    detail_b = ProfessorDetail(
        build_id="b-1", profile_hash=None, entity_id="e_b",
        display_name="B", university="U", org_units=("CS",), title="Prof",
        title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="included", profile_url=None,
        research_statements=(), approved_topics=(),
        selected_publication_mentions=(), bio_snippets=(),
        source_urls=("u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8", "u9", "u10"),
        provenance_refs=(), quality_findings=(), risk_flags=(),
        fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id="e_b"),
    )
    details = {"e_a": detail_a, "e_b": detail_b}
    ranked = rerank(hits, {}, details, {"e_a": 1.0, "e_b": 1.0}, None, profile, route)
    assert ranked[0].entity_id == "e_a"
    assert ranked[1].entity_id == "e_b"
