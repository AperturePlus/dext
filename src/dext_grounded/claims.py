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
    fact_refs: tuple[SourceRef, ...] = ()
    user_context_ref: UserContextRef | None = None

    def __post_init__(self) -> None:
        # accept list/tuple/generator input; store as tuple (spec §3 deep immutability)
        object.__setattr__(
            self, "fact_refs",
            tuple(self.fact_refs) if self.fact_refs is not None else (),
        )

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
    claims: tuple[Claim, ...] = ()
    cited_refs: tuple[SourceRef, ...] = ()
    warnings: list[GenerationWarning] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "claims",
            tuple(self.claims) if self.claims is not None else (),
        )
        object.__setattr__(
            self, "cited_refs",
            tuple(self.cited_refs) if self.cited_refs is not None else (),
        )
        # warnings stays list (spec §3 scope excludes it)


__all__ = ["Claim", "GenerationResult", "GenerationWarning"]
