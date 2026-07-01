# src/dext_recommend/core/cards.py
"""Assemble a RecommendedProfessor card from rerank + explanation + facts.

Card fields follow overview §5 minimum display semantics. Contacts are
gated by viewer permissions (R3 fakes omit contacts entirely).
"""
from __future__ import annotations

from dext_recommend.models import QueryUnderstanding, RecommendedProfessor
from dext_recommend.ports.professor_facts import ProfessorDetail, ProfessorFact
from dext_recommend.core.explanation import ExplanationResult
from dext_recommend.core.rerank import RerankEntry


def assemble_card(
    entry: RerankEntry,
    fact: ProfessorFact | None,
    detail: ProfessorDetail | None,
    explanation: ExplanationResult,
    qu: QueryUnderstanding,
    *,
    include_contacts: bool,
) -> RecommendedProfessor:
    risk_flags: list[str] = []
    available_actions = ["detail", "match", "email", "compare", "favorite", "follow_up"]
    if fact is not None and fact.role_status == "review":
        risk_flags.append("role_status_review")

    display_name = fact.display_name if fact is not None else (detail.display_name if detail else entry.entity_id)
    university = fact.university if fact is not None else (detail.university if detail else "")
    org_units = fact.org_units if fact is not None else (detail.org_units if detail else ())
    title = fact.title if fact is not None else (detail.title if detail else "")
    title_family = fact.title_family if fact is not None else (detail.title_family if detail else "professor")
    master_elig = fact.master_eligibility if fact is not None else (detail.master_eligibility if detail else "unknown")
    phd_elig = fact.phd_eligibility if fact is not None else (detail.phd_eligibility if detail else "unknown")
    role_status = fact.role_status if fact is not None else (detail.role_status if detail else "included")
    profile_url = fact.profile_url if fact is not None else (detail.profile_url if detail else None)
    research_summary = fact.research_summary if fact is not None else None

    return RecommendedProfessor(
        entity_id=entry.entity_id,
        display_name=display_name,
        university=university,
        org_units=org_units,
        title=title,
        title_family=title_family,
        master_eligibility=master_elig,
        phd_eligibility=phd_elig,
        role_status=role_status,
        profile_url=profile_url,
        research_summary=research_summary,
        match_level=entry.match_level,
        short_reasons=explanation.short_reasons,
        score=entry.score,
        score_components=entry.score_components,
        matched_topics=explanation.matched_topics,
        matched_statements=explanation.matched_statements,
        matched_publications=explanation.matched_publications,
        evidence_refs=explanation.evidence_refs,
        risk_flags=tuple(risk_flags),
        available_actions=tuple(available_actions),
    )


__all__ = ["assemble_card"]
