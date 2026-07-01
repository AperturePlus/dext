from __future__ import annotations

import pytest

from dext_recommend import QueryDiagnostics, QueryUnderstanding, RecommendResponse, RecommendationWarning
from dext_recommend.core.validation import make_error_response, validate


def _qu():
    return QueryUnderstanding(
        research_interests=("NLP",), preferred_universities=(),
        preferred_cities=(), preferred_org_units=(), degree_goal=None,
        mentor_eligibility_requirement=None, missing_information=(),
        needs_clarification=False, confidence=0.8,
    )


def _diag():
    return QueryDiagnostics(query_length=3, language_summary="zh", filter_summary="")


def test_validate_accepts_success_response():
    resp = RecommendResponse(
        build_id="b-1", ranking_profile_version="r1", embedding_fingerprint="fp",
        taxonomy_version="t1", query_understanding=_qu(), query=_diag(),
        results=(), suggested_followups=(), warnings=(),
    )
    validate(resp)  # no raise


def test_validate_rejects_missing_build_id_on_success():
    resp = RecommendResponse(
        build_id="", ranking_profile_version="r1", embedding_fingerprint="fp",
        taxonomy_version="t1", query_understanding=_qu(), query=_diag(),
        results=(), suggested_followups=(), warnings=(),
    )
    with pytest.raises(ValueError):
        validate(resp)


def test_validate_accepts_error_response_with_unavailable_fields():
    resp = make_error_response(
        build_id="unavailable", ranking_profile_version="unavailable",
        embedding_fingerprint="unavailable", taxonomy_version=None,
        warning=RecommendationWarning(code="active_build_unavailable", message="x", severity="error"),
    )
    validate(resp)
    assert resp.results == ()
    assert any(w.severity == "error" for w in resp.warnings)


def test_validate_rejects_error_response_with_results():
    resp = make_error_response(
        build_id="b-1", ranking_profile_version="r1", embedding_fingerprint="fp",
        taxonomy_version="t1",
        warning=RecommendationWarning(code="active_build_unavailable", message="x", severity="error"),
    )
    # mutate to inject a result — should fail validation
    object.__setattr__(resp, "results", ("not-a-professor",))
    with pytest.raises((ValueError, TypeError)):
        validate(resp)


def test_validate_rejects_error_response_without_error_warning():
    resp = make_error_response(
        build_id="b-1", ranking_profile_version="r1", embedding_fingerprint="fp",
        taxonomy_version="t1",
        warning=RecommendationWarning(code="needs_clarification", message="x", severity="warning"),
    )
    with pytest.raises(ValueError):
        validate(resp)
