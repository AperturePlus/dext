from __future__ import annotations

from dext_recommend import ProfessorFact, RecommendationFilters, VectorHit
from dext_recommend.core.filters import (
    FilterDiagnostics, final_filter, payload_prefilter,
)
from dext_recommend.core.intent import resolve_recommend_route
from dext_recommend.models import RecommendRequest

from tests.dext_recommend._recfixtures import vector_hits_case, professor_facts_case


def _route_for(intent: str = "new_search", prior=(), anchor=None):
    return resolve_recommend_route(RecommendRequest(
        query_text="NLP",
        conversation_context=None,
    )) if intent == "new_search" else resolve_recommend_route(RecommendRequest(
        query_text="NLP",
        conversation_context=type("C", (), {"intent": intent,
                                             "prior_result_entity_ids": prior,
                                             "anchor_entity_id": anchor})(),
    ))


def test_payload_prefilter_skips_org_unit_when_degraded():
    from dext_recommend.core.filters import payload_prefilter
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports.vector_search import VectorHit

    hits = [
        VectorHit(entity_id="e1", score=0.9, payload={"org_unit_ids": ["ou_cs"]}),
        VectorHit(entity_id="e2", score=0.8, payload={"org_unit_ids": ["ou_math"]}),
    ]
    filters = RecommendationFilters(org_unit_ids=("ou_cs",))
    out = payload_prefilter(hits, filters, org_unit_degraded=True)
    assert {h.entity_id for h in out} == {"e1", "e2"}


def test_payload_prefilter_keeps_org_unit_when_not_degraded():
    from dext_recommend.core.filters import payload_prefilter
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.ports.vector_search import VectorHit

    hits = [
        VectorHit(entity_id="e1", score=0.9, payload={"org_unit_ids": ["ou_cs"]}),
        VectorHit(entity_id="e2", score=0.8, payload={"org_unit_ids": ["ou_math"]}),
    ]
    filters = RecommendationFilters(org_unit_ids=("ou_cs",))
    out = payload_prefilter(hits, filters, org_unit_degraded=False)
    assert {h.entity_id for h in out} == {"e1"}


def test_payload_prefilter_drops_unmatched_university():
    hits = vector_hits_case("happy")
    filters = RecommendationFilters(university_ids=("u_other",))
    out = payload_prefilter(hits, filters)
    assert out == []


def test_payload_prefilter_passes_when_payload_field_missing():
    hits = (VectorHit("e1", 0.9, {}),)  # no university_id in payload
    filters = RecommendationFilters(university_ids=("u_demo",))
    out = payload_prefilter(hits, filters)
    assert len(out) == 1  # field missing -> skip condition, leave to final filter


def test_final_filter_drops_excluded_role():
    hits = vector_hits_case("happy")
    facts = professor_facts_case("happy")
    filters = RecommendationFilters()
    out, diag = final_filter(hits, facts, filters, _route_for(), {"org_unit_ids": True})
    ids = [h.entity_id for h in out]
    assert "e_cv_excluded" not in ids
    assert "e_cv_strong" in ids
    assert diag.role_excluded == 1


def test_final_filter_org_unit_degraded_when_flag_missing():
    from dext_recommend.core.filters import final_filter, FilterDiagnostics
    from dext_recommend.models import RecommendationFilters, RecommendationWarning
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact

    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    fact = ProfessorFact(
        entity_id="e1", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, org_unit_ids=("ou_math",),
    )
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    # coverage_flags missing the key entirely -> degraded
    survivors, diag = final_filter(
        hits, {"e1": fact}, RecommendationFilters(org_unit_ids=("ou_cs",)),
        route, coverage_flags={}, review_policy="exclude",
    )
    assert diag.org_unit_degraded is True
    assert len(survivors) == 1  # not hard-filtered on org_unit


def test_final_filter_org_unit_not_degraded_when_flag_true():
    from dext_recommend.core.filters import final_filter
    from dext_recommend.models import RecommendationFilters
    from dext_recommend.core.intent import RecommendRoute
    from dext_recommend.ports.vector_search import VectorHit
    from dext_recommend.ports.professor_facts import ProfessorFact

    route = RecommendRoute(intent="new_search", exclude_entity_ids=(),
                          anchor_entity_id=None, refine_merge=False,
                          unsupported=None, warnings=())
    fact = ProfessorFact(
        entity_id="e1", display_name="N", university="U", org_units=("CS",),
        title="Prof", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="active", profile_url=None,
        profile_hash=None, research_summary=None, org_unit_ids=("ou_math",),
    )
    hits = [VectorHit(entity_id="e1", score=0.9, payload={})]
    survivors, diag = final_filter(
        hits, {"e1": fact}, RecommendationFilters(org_unit_ids=("ou_cs",)),
        route, coverage_flags={"org_unit_ids": True}, review_policy="exclude",
    )
    assert diag.org_unit_degraded is False
    assert len(survivors) == 0  # hard-filtered: org_unit mismatch


def test_final_filter_review_excluded_by_default():
    hits = vector_hits_case("review")
    facts = professor_facts_case("review")
    filters = RecommendationFilters()
    out, diag = final_filter(hits, facts, filters, _route_for(), {"org_unit_ids": True})
    assert out == []
    assert diag.role_review == 1


def test_final_filter_review_kept_when_include_downranked():
    hits = vector_hits_case("review")
    facts = professor_facts_case("review")
    filters = RecommendationFilters()  # review_policy comes from request, not filters
    # simulate include_downranked by passing a route whose intent tolerates review
    out, diag = final_filter(
        hits, facts, filters, _route_for(), {"org_unit_ids": True},
        review_policy="include_downranked",
    )
    assert len(out) == 1
    assert out[0].entity_id == "e_cv_review"


def test_final_filter_fact_authority_overrides_payload():
    # e_other_org payload says ou_cs, fact says ou_math
    hits = vector_hits_case("other_org")
    facts = professor_facts_case("other_org")
    filters = RecommendationFilters(org_unit_ids=("ou_cs",))
    out, diag = final_filter(hits, facts, filters, _route_for(), {"org_unit_ids": True})
    assert out == []  # fact authority wins


def test_final_filter_org_unit_degraded_when_coverage_false():
    hits = vector_hits_case("other_org")
    facts = professor_facts_case("other_org")
    filters = RecommendationFilters(org_unit_ids=("ou_cs",))
    out, diag = final_filter(hits, facts, filters, _route_for(), {"org_unit_ids": False})
    # degraded: org_unit hard filter becomes soft; candidate kept even though fact says ou_math
    assert len(out) == 1
    assert diag.org_unit_degraded is True


def test_final_filter_more_mentors_excludes_prior():
    hits = vector_hits_case("happy")
    facts = professor_facts_case("happy")
    filters = RecommendationFilters()
    route = _route_for("more_mentors", prior=("e_cv_strong",))
    out, diag = final_filter(hits, facts, filters, route, {"org_unit_ids": True})
    assert "e_cv_strong" not in [h.entity_id for h in out]


def test_final_filter_hard_topic_uses_fact_topic_ids():
    from tests.dext_recommend._recfixtures import vector_hits_case, professor_facts_case
    # build a custom facts set where e_cv_strong has topic_ids=("topic_cv",)
    hits = vector_hits_case("happy")
    facts = professor_facts_case("happy")
    filters = RecommendationFilters(topic_ids=("topic_missing",), topic_filter_mode="hard")
    out, diag = final_filter(hits, facts, filters, _route_for(), {"org_unit_ids": True})
    assert out == []
