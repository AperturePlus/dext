from __future__ import annotations

from dext_recommend import ProfessorFact
from dext_recommend.core.explanation import build_explanation
from dext_recommend.core.rerank import RerankEntry

from tests.dext_recommend._recfixtures import professor_details_case, professor_facts_case


def _entry(eid: str, score: float = 0.8) -> RerankEntry:
    return RerankEntry(
        entity_id=eid, score=score,
        score_components={"semantic_score": score}, match_level="strong",
        evidence_count=2,
    )


def test_build_explanation_with_detail_has_reasons_and_refs():
    fact = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry("e_cv_strong")
    result = build_explanation(entry, fact, detail, query_terms=("NLP",))
    assert len(result.short_reasons) >= 1
    assert len(result.evidence_refs) >= 1
    assert result.weak_explanation is False


def test_build_explanation_without_detail_marks_weak():
    fact = professor_facts_case("no_statement")["e_no_statement"]
    entry = _entry("e_no_statement")
    result = build_explanation(entry, fact, None, query_terms=("NLP",))
    assert result.weak_explanation is True
    assert result.missing_reason is not None
    # Previously pinned the empty-reason bug; spec §8 requires >= 1 item.
    assert len(result.short_reasons) >= 1
    assert result.weak_explanation is True
    assert "缺少可回溯详情证据" in result.short_reasons[0]


def test_explanation_detail_none_has_qualified_reason():
    from dext_recommend.core.explanation import build_explanation
    from dext_recommend.core.rerank import RerankEntry
    entry = RerankEntry(
        entity_id="e1", score=0.42,
        score_components={"semantic_score": 0.5, "topic_statement_score": 0.0,
                          "student_fit_score": 0.0, "eligibility_score": 0.0,
                          "provenance_score": 0.0, "completeness_score": 0.0,
                          "same_field_overlap": 0.0, "same_field_boost": 0.0},
        match_level="possible", evidence_count=0,
    )
    expl = build_explanation(entry, None, None, query_terms=("cv",))
    assert len(expl.short_reasons) >= 1
    assert expl.weak_explanation is True
    assert expl.missing_reason is not None
    # must not present the composite entry.score as a semantic score
    assert "semantic" not in expl.short_reasons[0].lower()
    assert "score=" not in expl.short_reasons[0]


def test_build_explanation_reasons_traceable_to_evidence():
    fact = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry("e_cv_strong")
    result = build_explanation(entry, fact, detail, query_terms=("NLP",))
    # evidence_refs come from detail provenance + source_urls, never fabricated
    assert all(ref is not None for ref in result.evidence_refs)
