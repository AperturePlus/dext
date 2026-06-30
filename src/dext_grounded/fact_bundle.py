"""Immutable fact bundle — the sole fact input to constrained generation
(grounded-generation spec §3).

Both recommend (ProfessorDetail) and competition (knowledge-base snippets)
assemble their own FactBundle but implement the same read-only interface.
LLM generation may ONLY consume ``facts``; it MUST NOT supplement from
training memory. A FactItem without source_refs MUST be marked uncertain.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dext_grounded.content import ContentClass
from dext_grounded.source_ref import SourceRef


@dataclass(frozen=True, slots=True)
class FactItem:
    field: str                        # e.g. research_statement / eligibility / rule
    value: str
    content_class: ContentClass
    source_refs: list[SourceRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.source_refs and self.content_class != ContentClass.UNCERTAIN:
            # spec §3: missing source_refs => must be uncertain
            object.__setattr__(self, "content_class", ContentClass.UNCERTAIN)


@dataclass(frozen=True, slots=True)
class FactBundle:
    build_id: str                     # recommend: ACTIVE build id; competition: kb version
    subject_id: str                   # recommend: entity_id; competition: competition_id
    facts: list[FactItem]
    source_refs: list[SourceRef]      # canonical lookup set for citation validation

    def __post_init__(self) -> None:
        if self.facts is None:
            object.__setattr__(self, "facts", [])
        if self.source_refs is None:
            object.__setattr__(self, "source_refs", [])


__all__ = ["FactBundle", "FactItem"]
