"""Immutable fact bundle — the sole fact input to constrained generation
(grounded-generation spec §3).

Both recommend (ProfessorDetail) and competition (knowledge-base snippets)
assemble their own FactBundle but implement the same read-only interface.
LLM generation may ONLY consume ``facts``; it MUST NOT supplement from
training memory. A FactItem without source_refs MUST be marked uncertain.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_grounded.content import ContentClass
from dext_grounded.source_ref import SourceRef


@dataclass(frozen=True, slots=True)
class FactItem:
    field: str                        # e.g. research_statement / eligibility / rule
    value: str
    content_class: ContentClass
    source_refs: tuple[SourceRef, ...] = ()

    def __post_init__(self) -> None:
        # accept list/tuple/generator input; store as tuple (spec §3 deep immutability)
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        if not self.source_refs and self.content_class != ContentClass.UNCERTAIN:
            # spec §3: missing source_refs => must be uncertain
            object.__setattr__(self, "content_class", ContentClass.UNCERTAIN)


@dataclass(frozen=True, slots=True)
class FactBundle:
    build_id: str                     # recommend: ACTIVE build id; competition: kb version
    subject_id: str                   # recommend: entity_id; competition: competition_id
    facts: tuple[FactItem, ...]
    source_refs: tuple[SourceRef, ...]      # canonical lookup set for citation validation

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts) if self.facts is not None else ())
        object.__setattr__(
            self, "source_refs",
            tuple(self.source_refs) if self.source_refs is not None else (),
        )


__all__ = ["FactBundle", "FactItem"]
