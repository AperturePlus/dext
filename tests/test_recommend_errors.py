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
        "unauthorized_contact", "generation_unavailable",
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
