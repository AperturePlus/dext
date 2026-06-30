"""Per-assertion claim classification + generation result (spec §4, §4.1).

Output is split into Claims, each independently classified and independently
cited, so mixed fact/advice/uncertain output is NOT collapsed into a single
global content_class. Validation produces structured warnings; the caller
(CitationValidator / SafetyGuard) decides downgrade vs. drop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dext_grounded.content import ContentClass
from dext_grounded.source_ref import SourceRef, UserContextRef


@dataclass(frozen=True, slots=True)
class GenerationWarning:
    code: str
    message: str
    claim_text: str | None = None


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    content_class: ContentClass
    fact_refs: list[SourceRef] = field(default_factory=list)
    user_context_ref: UserContextRef | None = None

    def validate(self) -> list[GenerationWarning]:
        """Return warnings for this claim's citation shape (spec §5.2)."""
        warnings: list[GenerationWarning] = []
        if self.content_class == ContentClass.FACT and not self.fact_refs:
            warnings.append(GenerationWarning(
                code="fact_ref_missing",
                message="fact claim must carry non-empty fact_refs",
                claim_text=self.text,
            ))
        if self.content_class == ContentClass.UNCERTAIN and self.fact_refs:
            warnings.append(GenerationWarning(
                code="uncertain_claim_with_refs",
                message="uncertain claim must not carry fact_refs as if certain",
                claim_text=self.text,
            ))
        return warnings


@dataclass(frozen=True, slots=True)
class GenerationResult:
    output: dict | str         # dict when json_schema provided, else markdown
    claims: list[Claim] = field(default_factory=list)
    cited_refs: list[SourceRef] = field(default_factory=list)
    warnings: list[GenerationWarning] = field(default_factory=list)


__all__ = ["Claim", "GenerationResult", "GenerationWarning"]
