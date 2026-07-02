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

    reasons: list[str] = []
    if matched_topics:
        reasons.append(f"研究方向匹配: {', '.join(matched_topics[:3])}")
    if matched_statements:
        reasons.append(f"研究陈述命中: {matched_statements[0][:60]}")
    if matched_publications:
        reasons.append(f"代表成果命中: {matched_publications[0][:60]}")
    if not reasons:
        reasons.append(f"语义相似度匹配 (score={entry.score:.2f})")

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

    weak = not evidence_refs
    return ExplanationResult(
        short_reasons=tuple(reasons),
        evidence_refs=evidence_refs,
        matched_topics=matched_topics,
        matched_statements=matched_statements,
        matched_publications=matched_publications,
        weak_explanation=weak,
        missing_reason="no provenance or source URLs" if weak else None,
    )


__all__ = ["ExplanationResult", "build_explanation"]
