"""RecommendResponse validation. Success/warning-only responses must have
non-empty build_id/ranking_profile_version/embedding_fingerprint. Error
responses (any warning severity=error) may use "unavailable" for those
fields but MUST have results==() and at least one error warning.
"""
from __future__ import annotations

from dext_recommend.models import (
    QueryDiagnostics, QueryUnderstanding, RecommendResponse, RecommendationWarning,
)

_VALID_MATCH_LEVELS = {"excellent", "strong", "possible", "weak"}
_FORBIDDEN_EMPTY_ON_SUCCESS = ("", None)


def _is_error_response(response: RecommendResponse) -> bool:
    return any(w.severity == "error" for w in response.warnings)


def validate(response: RecommendResponse) -> None:
    # tuple immutability is enforced by the dataclass; check shape
    if not isinstance(response.results, tuple):
        raise ValueError("results must be a tuple")
    if not isinstance(response.warnings, tuple):
        raise ValueError("warnings must be a tuple")
    for w in response.warnings:
        if not w.code:
            raise ValueError("warning.code must be non-empty")
    is_error = _is_error_response(response)
    if is_error:
        if response.results != ():
            raise ValueError("error response must have empty results")
        return
    # success / warning-only
    for field in ("build_id", "ranking_profile_version", "embedding_fingerprint"):
        value = getattr(response, field)
        if value in _FORBIDDEN_EMPTY_ON_SUCCESS or value == "unavailable":
            raise ValueError(f"{field} must be non-empty and not 'unavailable' on success")
    for r in response.results:
        if r.match_level not in _VALID_MATCH_LEVELS:
            raise ValueError(f"invalid match_level: {r.match_level!r}")
    if not response.ranking_profile_version:
        raise ValueError("ranking_profile_version must be non-empty")


def make_error_response(
    *, build_id: str, ranking_profile_version: str, embedding_fingerprint: str,
    taxonomy_version: str | None, warning: RecommendationWarning,
) -> RecommendResponse:
    qu = QueryUnderstanding(
        research_interests=(), preferred_universities=(), preferred_cities=(),
        preferred_org_units=(), degree_goal=None,
        mentor_eligibility_requirement=None, missing_information=(),
        needs_clarification=True, confidence=0.0,
    )
    diag = QueryDiagnostics(
        query_length=0, language_summary=None, filter_summary=None,
        recall_count=0, post_filter_count=0, returned_count=0,
    )
    return RecommendResponse(
        build_id=build_id, ranking_profile_version=ranking_profile_version,
        embedding_fingerprint=embedding_fingerprint, taxonomy_version=taxonomy_version,
        query_understanding=qu, query=diag, results=(),
        suggested_followups=(), warnings=(warning,),
    )


__all__ = ["make_error_response", "validate"]
