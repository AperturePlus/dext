# src/dext_recommend/core/explanation.py
"""Explanation items + evidence refs for a single recommended professor.

Every reason must trace to ProfessorDetail evidence. Missing detail ->
weak_explanation warning + missing_reason. LLM never rewrites facts.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_grounded import SourceRef

from dext_recommend.ports.professor_facts import ProfessorDetail, ProfessorFact
from dext_recommend.core.rerank import RerankEntry


@dataclass(frozen=True, slots=True)
class ExplanationResult:
    short_reasons: tuple[str, ...]
    evidence_refs: tuple[SourceRef, ...]
    matched_topics: tuple[str, ...]
    matched_statements: tuple[str, ...]
    matched_publications: tuple[str, ...]
    weak_explanation: bool
    missing_reason: str | None

    def __post_init__(self) -> None:
        for name in ("short_reasons", "evidence_refs", "matched_topics",
                     "matched_statements", "matched_publications"):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))


def _query_terms(query_terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(t.strip().lower() for t in query_terms if t and t.strip())


def _direction_evidence(
    fact: ProfessorFact | None,
    detail: ProfessorDetail | None,
    query_terms: tuple[str, ...],
) -> float:
    terms = _query_terms(query_terms)
    if not terms:
        return 0.0

    hay_parts = []
    if fact is not None and fact.research_summary:
        hay_parts.append(fact.research_summary)
    if detail is not None:
        hay_parts.extend(detail.research_statements)
        hay_parts.extend(detail.approved_topics)
    hay = " ".join(hay_parts).lower()
    if not hay:
        return 0.0

    hits = sum(1 for term in terms if term in hay)
    return min(1.0, hits / len(terms))


def _entry_direction_evidence(
    entry: RerankEntry,
    fact: ProfessorFact | None,
    detail: ProfessorDetail | None,
    query_terms: tuple[str, ...],
) -> float:
    value = entry.score_components.get("direction_evidence_score")
    if value is not None:
        return float(value)
    return _direction_evidence(fact, detail, query_terms)


def build_explanation(
    entry: RerankEntry,
    fact: ProfessorFact | None,
    detail: ProfessorDetail | None,
    *,
    query_terms: tuple[str, ...],
) -> ExplanationResult:
    if detail is None:
        return ExplanationResult(
            short_reasons=("综合匹配信号较高；当前缺少可回溯详情证据",),
            evidence_refs=(),
            matched_topics=(),
            matched_statements=(),
            matched_publications=(),
            weak_explanation=True,
            missing_reason="ProfessorDetail unavailable in ACTIVE build",
        )

    terms_lower = {t.lower() for t in query_terms if t}
    matched_topics = tuple(
        t for t in detail.approved_topics
        if any(term in t.lower() for term in terms_lower)
    )
    matched_statements = tuple(
        s for s in detail.research_statements
        if any(term in s.lower() for term in terms_lower)
    )
    matched_publications = tuple(
        p for p in detail.selected_publication_mentions
        if any(term in p.lower() for term in terms_lower)
    )
    matched_summary = (
        fact.research_summary
        if fact is not None
        and fact.research_summary
        and any(term in fact.research_summary.lower() for term in terms_lower)
        else None
    )
    no_direction_evidence = bool(terms_lower) and (
        _entry_direction_evidence(entry, fact, detail, query_terms) <= 0.0
    )

    reasons: list[str] = []
    if no_direction_evidence:
        reasons.append("未找到与查询方向直接对应的研究证据，仅作为语义近邻参考")
    elif matched_topics:
        reasons.append(f"研究方向匹配: {', '.join(matched_topics[:3])}")
    if not no_direction_evidence and matched_statements:
        reasons.append(f"研究陈述命中: {matched_statements[0][:60]}")
    if not no_direction_evidence and matched_summary and not reasons:
        reasons.append(f"研究摘要命中: {matched_summary[:60]}")
    if not no_direction_evidence and matched_publications:
        reasons.append(f"代表成果命中: {matched_publications[0][:60]}")
    if not reasons:
        reasons.append(f"语义召回匹配，未找到精确方向证据 (score={entry.score:.2f})")

    evidence_refs = tuple(detail.provenance_refs)
    # if no provenance_refs, synthesize SourceRefs from source_urls (R4 fills real refs)
    if not evidence_refs and detail.source_urls:
        evidence_refs = tuple(
            SourceRef(
                doc_path=url, heading_path="", chunk_hash="",
                quote_or_summary=url,
            )
            for url in detail.source_urls[:5]
        )

    weak = no_direction_evidence or not evidence_refs
    return ExplanationResult(
        short_reasons=tuple(reasons),
        evidence_refs=evidence_refs,
        matched_topics=matched_topics,
        matched_statements=matched_statements,
        matched_publications=matched_publications,
        weak_explanation=weak,
        missing_reason=(
            "no direct direction evidence for query terms"
            if no_direction_evidence
            else ("no provenance or source URLs" if weak else None)
        ),
    )


__all__ = ["ExplanationResult", "build_explanation"]
