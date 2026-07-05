from __future__ import annotations

from dext_recommend import ProfessorFact, RecommendedProfessor
from dext_recommend.core.cards import assemble_card
from dext_recommend.core.explanation import build_explanation
from dext_recommend.core.rerank import RerankEntry
from dext_recommend.models import QueryUnderstanding

from tests.dext_recommend._recfixtures import professor_details_case, professor_facts_case


def _entry(eid: str = "e_cv_strong", score: float = 0.8) -> RerankEntry:
    return RerankEntry(
        entity_id=eid, score=score,
        score_components={"semantic_score": score, "topic_statement_score": 0.5,
                          "student_fit_score": 0.0, "eligibility_score": 1.0,
                          "provenance_score": 0.4, "completeness_score": 0.6},
        match_level="strong", evidence_count=2,
    )


def _qu() -> QueryUnderstanding:
    return QueryUnderstanding(
        research_interests=("NLP",), preferred_universities=(),
        preferred_cities=(), preferred_org_units=(), degree_goal=None,
        mentor_eligibility_requirement=None, missing_information=(),
        needs_clarification=False, confidence=0.8,
    )


def test_assemble_card_builds_recommended_professor_with_all_fields():
    fact = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry()
    expl = build_explanation(entry, fact, detail, query_terms=("NLP",))
    card = assemble_card(entry, fact, detail, expl, _qu(), include_contacts=False)
    assert isinstance(card, RecommendedProfessor)
    assert card.entity_id == "e_cv_strong"
    assert card.match_level == "strong"
    assert card.score == 0.8
    assert "semantic_score" in card.score_components
    assert card.role_status == "included"
    assert len(card.short_reasons) >= 1
    assert "detail" in card.available_actions


def test_assemble_card_contacts_hidden_by_default():
    fact = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry()
    expl = build_explanation(entry, fact, detail, query_terms=("NLP",))
    card = assemble_card(entry, fact, detail, expl, _qu(), include_contacts=False)
    # contacts never populated on the card (ProfessorFact has no contacts; detail gated)
    assert card.role_status == "included"


def test_assemble_card_review_role_adds_risk_flag():
    fact = professor_facts_case("review")["e_cv_review"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry("e_cv_review")
    expl = build_explanation(entry, fact, detail, query_terms=("NLP",))
    card = assemble_card(entry, fact, detail, expl, _qu(), include_contacts=False)
    assert "role_status_review" in card.risk_flags


def test_assemble_card_research_fields_fall_back_to_detail_topics():
    fact = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry()
    expl = build_explanation(entry, fact, detail, query_terms=("医学影像",))
    assert expl.matched_topics == ()
    assert expl.matched_statements == ()
    card = assemble_card(entry, fact, detail, expl, _qu(), include_contacts=False)
    assert card.matched_topics == ("topic_cv",)
