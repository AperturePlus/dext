# src/dext_recommend/core/filters.py
"""Two-stage filtering: payload pre-filter on VectorHit.payload, then
hydrated final filter on ProfessorFact (authority). Fact is authoritative;
payload is recall acceleration only. org_unit coverage unavailable degrades
the org_unit hard filter to soft.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dext_recommend.models import RecommendationFilters
from dext_recommend.ports.vector_search import VectorHit
from dext_recommend.ports.professor_facts import ProfessorFact
from dext_recommend.core.intent import RecommendRoute


@dataclass(frozen=True, slots=True)
class FilterDiagnostics:
    role_excluded: int = 0
    role_review: int = 0
    hard_filtered: int = 0
    org_unit_degraded: bool = False


def _payload_match(payload: Mapping, key: str, requested: tuple[str, ...]) -> bool | None:
    """Return True if matches, False if explicitly mismatches, None if field absent."""
    if not requested:
        return True
    val = payload.get(key)
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        return any(v in requested for v in val)
    return val in requested


def payload_prefilter(
    hits: list[VectorHit] | tuple[VectorHit, ...],
    filters: RecommendationFilters,
    *,
    org_unit_degraded: bool = False,
) -> list[VectorHit]:
    out: list[VectorHit] = []
    for h in hits:
        keep = True
        for key, req in (
            ("university_id", filters.university_ids),
            ("city_name", filters.city_names),
            ("title_family", filters.title_families),
        ):
            m = _payload_match(h.payload, key, tuple(req))
            if m is False:
                keep = False
                break
        if keep and not org_unit_degraded:
            m = _payload_match(h.payload, "org_unit_ids", tuple(filters.org_unit_ids))
            if m is False:
                keep = False
        if filters.master_eligibility == "confirmed":
            m = _payload_match(h.payload, "master_eligibility", ("confirmed",))
            if m is False:
                keep = False
        if filters.phd_eligibility == "confirmed":
            m = _payload_match(h.payload, "phd_eligibility", ("confirmed",))
            if m is False:
                keep = False
        if keep:
            out.append(h)
    return out


def _fact_authority_match(fact: ProfessorFact, key: str, requested: tuple[str, ...]) -> bool:
    if not requested:
        return True
    val = getattr(fact, key, None)
    if val is None:
        return False  # authority field missing -> hard filter fails (no display-name guess)
    if isinstance(val, (list, tuple)):
        return any(v in requested for v in val)
    return val in requested


def final_filter(
    hits: list[VectorHit],
    fact_map: dict[str, ProfessorFact],
    filters: RecommendationFilters,
    route: RecommendRoute,
    coverage_flags: Mapping[str, bool],
    *,
    review_policy: str = "exclude",
) -> tuple[list[VectorHit], FilterDiagnostics]:
    excluded = 0
    review = 0
    hard = 0
    org_unit_degraded = coverage_flags.get("org_unit_ids") is not True
    exclude_set = set(route.exclude_entity_ids)

    org_unit_hard = tuple(filters.org_unit_ids)
    if org_unit_degraded:
        org_unit_hard = ()  # degrade to soft: do not hard-filter on org_unit

    out: list[VectorHit] = []
    for h in hits:
        fact = fact_map.get(h.entity_id)
        if fact is None:
            hard += 1
            continue
        if fact.role_status == "excluded":
            excluded += 1
            continue
        if fact.role_status == "review":
            if review_policy != "include_downranked":
                review += 1
                continue
        if h.entity_id in exclude_set:
            hard += 1
            continue
        if not _fact_authority_match(fact, "university_id", tuple(filters.university_ids)):
            hard += 1
            continue
        if not _fact_authority_match(fact, "city_name", tuple(filters.city_names)):
            hard += 1
            continue
        if org_unit_hard and not _fact_authority_match(fact, "org_unit_ids", org_unit_hard):
            hard += 1
            continue
        if not _fact_authority_match(fact, "title_family", tuple(filters.title_families)):
            hard += 1
            continue
        if filters.master_eligibility == "confirmed" and fact.master_eligibility != "confirmed":
            hard += 1
            continue
        if filters.phd_eligibility == "confirmed" and fact.phd_eligibility != "confirmed":
            hard += 1
            continue
        if filters.topic_filter_mode == "hard":
            if not _fact_authority_match(fact, "topic_ids", tuple(filters.topic_ids)):
                hard += 1
                continue
        out.append(h)
    return out, FilterDiagnostics(
        role_excluded=excluded, role_review=review,
        hard_filtered=hard, org_unit_degraded=org_unit_degraded,
    )


__all__ = ["FilterDiagnostics", "final_filter", "payload_prefilter"]
