# tests/test_recommend_errors.py
from __future__ import annotations

import dataclasses

import pytest

from dext_recommend import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)


def test_error_code_enum_has_all_foundations_codes():
    codes = {c.value for c in RecommendationErrorCode}
    assert codes == {
        "active_build_unavailable", "active_build_inconsistent",
        "embedding_fingerprint_mismatch", "no_candidates_after_filters",
        "payload_prefilter_degraded", "insufficient_facts",
        "invalid_request", "unauthorized_contact", "unauthorized_review",
        "generation_unavailable",
        "org_unit_ids_coverage_insufficient",
        "profile_hash_coverage_insufficient",
        "role_status_coverage_insufficient",
        "eligibility_coverage_insufficient",
        "org_unit_filter_unavailable",
        "ranking_profile_unavailable",
        "unsupported_for_recommend_core",
        "needs_clarification",
        "invalid_intent",
        "missing_prior_results",
        "missing_anchor",
        "weak_explanation",
        "request_timeout",
        "llm_unavailable",
        "embedding_unavailable",
        "vector_unavailable",
        "hydrate_unavailable",
        "details_unavailable",
    }


def test_recommendation_error_minimum():
    err = RecommendationError(
        code=RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
        severity=ErrorSeverity.ERROR,
        message="no ACTIVE build",
    )
    assert err.build_id is None
    assert err.retryable is False
    assert err.operator_action is None
    assert err.user_action is None


def test_recommendation_error_is_frozen():
    err = RecommendationError(
        code=RecommendationErrorCode.NO_CANDIDATES_AFTER_FILTERS,
        severity=ErrorSeverity.WARNING,
        message="empty",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        err.message = "other"


def test_recommendation_error_to_dict_for_response():
    err = RecommendationError(
        code=RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
        severity=ErrorSeverity.ERROR,
        message="fp mismatch",
        build_id="b-1",
        retryable=False,
        operator_action="rebuild vectors",
        user_action="try again later",
    )
    d = err.to_dict()
    assert d["code"] == "embedding_fingerprint_mismatch"
    assert d["severity"] == "error"
    assert d["build_id"] == "b-1"
    assert d["retryable"] is False


def test_new_readiness_error_codes_registered():
    from dext_recommend.errors import RecommendationErrorCode as C
    assert C.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT.value == "org_unit_ids_coverage_insufficient"
    assert C.PROFILE_HASH_COVERAGE_INSUFFICIENT.value == "profile_hash_coverage_insufficient"
    assert C.ROLE_STATUS_COVERAGE_INSUFFICIENT.value == "role_status_coverage_insufficient"
    assert C.ELIGIBILITY_COVERAGE_INSUFFICIENT.value == "eligibility_coverage_insufficient"
    assert C.ORG_UNIT_FILTER_UNAVAILABLE.value == "org_unit_filter_unavailable"
    assert C.RANKING_PROFILE_UNAVAILABLE.value == "ranking_profile_unavailable"
