# src/dext_recommend/core/rerank.py
"""6 score components, weighted sum, tie-break, match_level derivation.

Components are computed from whatever data is available: detail rerank window
candidates have ProfessorDetail (full 6 components); missing detail degrades
topic/provenance components to 0 and produces weak_explanation downstream.
student_fit never expresses admission probability (spec §12).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from dext_grounded import StudentContext

from dext_recommend.ports.professor_facts import ProfessorDetail, ProfessorFact
from dext_recommend.ports.vector_search import VectorHit
from dext_recommend.core.intent import RecommendRoute
from dext_recommend.core.ranking_profile import RankingProfile


@dataclass(frozen=True, slots=True)
class RerankEntry:
    entity_id: str
    score: float
    score_components: Mapping[str, float]
    match_level: str
    evidence_count: int

    def __post_init__(self) -> None:
        from dext_recommend._immutable import freeze_mapping
        object.__setattr__(self, "score_components", freeze_mapping(self.score_components))


def _semantic(semantic_scores: Mapping[str, float], eid: str) -> float:
    return float(semantic_scores.get(eid, 0.0))


def _topic_statement(detail: ProfessorDetail | None, query_terms: tuple[str, ...]) -> float:
    if detail is None or not query_terms:
        return 0.0
    hay = " ".join(detail.approved_topics) + " " + " ".join(detail.research_statements)
    if not hay:
        return 0.0
    hits = sum(1 for t in query_terms if t and t.lower() in hay.lower())
    return min(1.0, hits / max(1, len(query_terms)))


def _student_fit(student: StudentContext | None, detail: ProfessorDetail | None,
                 fact: ProfessorFact | None) -> float:
    if student is None or detail is None:
        return 0.0
    hay_parts = []
    if fact is not None and fact.research_summary:
        hay_parts.append(fact.research_summary)
    hay_parts.extend(detail.research_statements)
    hay = " ".join(hay_parts).lower()
    if not hay:
        return 0.0
    interests = list(getattr(student, "research_interests", ()) or ())
    if not interests:
        return 0.0
    hits = sum(1 for t in interests if t and t.lower() in hay)
    return min(1.0, hits / max(1, len(interests)))


def _eligibility(fact: ProfessorFact | None) -> float:
    if fact is None:
        return 0.0
    score = 0.0
    if fact.master_eligibility == "confirmed":
        score += 0.5
    if fact.phd_eligibility == "confirmed":
        score += 0.5
    if fact.role_status == "review":
        score *= 0.7  # downrank
    return score


def _provenance(detail: ProfessorDetail | None) -> tuple[float, int]:
    if detail is None:
        return 0.0, 0
    evidence_count = len(detail.provenance_refs) + len(detail.source_urls)
    score = min(1.0, evidence_count / 5.0)  # 5+ pieces of evidence saturates
    return score, evidence_count


def _completeness(fact: ProfessorFact | None, detail: ProfessorDetail | None) -> float:
    if fact is None:
        return 0.0
    fields = [fact.research_summary is not None]
    if detail is not None:
        fields.extend([
            bool(detail.research_statements), bool(detail.approved_topics),
            bool(detail.selected_publication_mentions), bool(detail.source_urls),
        ])
    return sum(1 for f in fields if f) / max(1, len(fields))


def _match_level(score: float, thresholds: Mapping[str, float]) -> str:
    if score >= thresholds["excellent"]:
        return "excellent"
    if score >= thresholds["strong"]:
        return "strong"
    if score >= thresholds["possible"]:
        return "possible"
    return "weak"


def _tie_break_key(entry: RerankEntry, spec: tuple[str, ...]) -> tuple:
    keys = []
    for field in spec:
        if field == "score":
            keys.append(-entry.score)
        elif field == "semantic_score":
            keys.append(-entry.score_components["semantic_score"])
        elif field == "evidence_count":
            keys.append(-entry.evidence_count)
        elif field == "entity_id":
            keys.append(entry.entity_id)
        else:
            raise ValueError(f"unknown tie_break field: {field}")
    return tuple(keys)

def _same_field_affinity(
    fact: ProfessorFact | None, anchor_topics: tuple[str, ...],
    base: float, profile: RankingProfile,
) -> tuple[float, float, float]:
    """Return (new_score, overlap_component, boost)."""
    if not anchor_topics or fact is None:
        return base, 0.0, 0.0
    cand = set(fact.topic_ids or ())
    anchor = set(anchor_topics)
    overlap_count = len(cand & anchor)
    overlap_component = overlap_count / max(1, len(anchor))
    boost = min(
        profile.same_field_boost_max,
        profile.same_field_boost_per_topic * overlap_count,
    )
    return min(1.0, base + boost), overlap_component, boost


def rerank(
    window: list[VectorHit],
    fact_map: Mapping[str, ProfessorFact],
    detail_map: Mapping[str, ProfessorDetail | None],
    semantic_scores: Mapping[str, float],
    student_context: StudentContext | None,
    profile: RankingProfile,
    route: RecommendRoute,
    *,
    query_terms: tuple[str, ...] = (),
    anchor_topics: tuple[str, ...] = (),
) -> list[RerankEntry]:
    w = profile.weights
    entries: list[RerankEntry] = []
    for hit in window:
        eid = hit.entity_id
        fact = fact_map.get(eid)
        detail = detail_map.get(eid)
        sem = _semantic(semantic_scores, eid)
        topic = _topic_statement(detail, query_terms)
        fit = _student_fit(student_context, detail, fact)
        elig = _eligibility(fact)
        prov, evidence_count = _provenance(detail)
        comp = _completeness(fact, detail)
        score = (
            w["semantic_score"] * sem
            + w["topic_statement_score"] * topic
            + w["student_fit_score"] * fit
            + w["eligibility_score"] * elig
            + w["provenance_score"] * prov
            + w["completeness_score"] * comp
        )
        score, sf_overlap, sf_boost = _same_field_affinity(
            fact, anchor_topics, score, profile,
        )
        score = min(1.0, max(0.0, score))
        components = {
            "semantic_score": sem,
            "topic_statement_score": topic,
            "student_fit_score": fit,
            "eligibility_score": elig,
            "provenance_score": prov,
            "completeness_score": comp,
            "same_field_overlap": sf_overlap,
            "same_field_boost": sf_boost,
        }
        entries.append(RerankEntry(
            entity_id=eid, score=score, score_components=components,
            match_level=_match_level(score, profile.match_level_thresholds),
            evidence_count=evidence_count,
        ))
    # tie-break driven by profile.tie_break (validated to be a complete
    # permutation of {score, semantic_score, evidence_count, entity_id})
    entries.sort(key=lambda e: _tie_break_key(e, profile.tie_break))
    return entries


__all__ = ["RerankEntry", "rerank"]
