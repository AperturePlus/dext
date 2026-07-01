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
    assert result.short_reasons == ()


def test_build_explanation_reasons_traceable_to_evidence():
    fact = professor_facts_case("happy")["e_cv_strong"]
    detail = professor_details_case("happy")["e_cv_strong"]
    entry = _entry("e_cv_strong")
    result = build_explanation(entry, fact, detail, query_terms=("NLP",))
    # evidence_refs come from detail provenance + source_urls, never fabricated
    assert all(ref is not None for ref in result.evidence_refs)
