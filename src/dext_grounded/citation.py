"""Per-claim citation validation (grounded-generation spec §5.2).

Runs immediately after LLM output. For each Claim, by content_class:
- fact: fact_refs must be non-empty AND every ref must be in the FactBundle's
  source_refs set; otherwise downgrade to uncertain (missing refs) or drop
  (fabricated refs).
- advice: may omit fact_refs; if it carries a user_context_ref, the field must
  be a present, non-empty field on the passed StudentContext, else strip the
  user_context_ref and warn fabricated_user_context.
- uncertain: must not carry fact_refs (would masquerade as certain).

When ALL claims are dropped, a no_grounded_output warning is emitted so the
caller never returns un-cited pure-LLM text as if grounded.
"""
from __future__ import annotations

from dataclasses import replace

from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.student_context import StudentContext


def _bundle_ref_keys(bundle: FactBundle) -> set[tuple[str, str]]:
    return {(ref.doc_path, ref.chunk_hash) for ref in bundle.source_refs}


def _present_student_fields(student_context: StudentContext | None) -> set[str]:
    if student_context is None:
        return set()
    present: set[str] = set()
    for name in (
        "education_stage", "school", "major", "gpa_bucket", "rank_bucket",
        "achievements_summary", "competition_experience_summary",
    ):
        if getattr(student_context, name, None) not in (None, ""):
            present.add(name)
    if student_context.research_interests:
        present.add("research_interests")
    return present


class CitationValidator:
    def validate(
        self,
        result: GenerationResult,
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
    ) -> GenerationResult:
        valid_keys = _bundle_ref_keys(fact_bundle)
        present_fields = _present_student_fields(student_context)
        warnings: list[GenerationWarning] = list(result.warnings)
        kept_claims: list[Claim] = []

        for claim in result.claims:
            new_claim, claim_warnings = self._validate_claim(
                claim, valid_keys, present_fields,
            )
            warnings.extend(claim_warnings)
            if new_claim is not None:
                kept_claims.append(new_claim)

        if result.claims and not kept_claims:
            warnings.append(GenerationWarning(
                code="no_grounded_output",
                message="all claims dropped by citation validation; no grounded output",
            ))

        cited: list = []
        for claim in kept_claims:
            cited.extend(claim.fact_refs)
        # dedupe cited refs by (doc_path, chunk_hash) preserving order
        seen: set[tuple[str, str]] = set()
        unique_cited = []
        for ref in cited:
            key = (ref.doc_path, ref.chunk_hash)
            if key not in seen:
                seen.add(key)
                unique_cited.append(ref)

        return replace(
            result,
            claims=kept_claims,
            cited_refs=unique_cited,
            warnings=warnings,
        )

    def _validate_claim(
        self,
        claim: Claim,
        valid_keys: set[tuple[str, str]],
        present_fields: set[str],
    ) -> tuple[Claim | None, list[GenerationWarning]]:
        warnings: list[GenerationWarning] = []
        if claim.content_class == ContentClass.FACT:
            if not claim.fact_refs:
                # downgrade to uncertain (spec §5.2)
                warnings.append(GenerationWarning(
                    code="fact_ref_missing",
                    message="fact claim had no refs; downgraded to uncertain",
                    claim_text=claim.text,
                ))
                return replace(claim, content_class=ContentClass.UNCERTAIN), warnings
            fabricated = [
                r for r in claim.fact_refs
                if (r.doc_path, r.chunk_hash) not in valid_keys
            ]
            if fabricated:
                warnings.append(GenerationWarning(
                    code="fabricated_ref",
                    message=f"dropped claim with fabricated refs: {len(fabricated)}",
                    claim_text=claim.text,
                ))
                return None, warnings
            return claim, warnings

        if claim.content_class == ContentClass.ADVICE:
            if claim.user_context_ref is not None:
                field = claim.user_context_ref.field
                if field not in present_fields:
                    warnings.append(GenerationWarning(
                        code="fabricated_user_context",
                        message=f"stripped user_context_ref for absent field {field!r}",
                        claim_text=claim.text,
                    ))
                    return replace(claim, user_context_ref=None), warnings
            return claim, warnings

        # uncertain
        if claim.fact_refs:
            warnings.append(GenerationWarning(
                code="uncertain_claim_with_refs",
                message="uncertain claim carried fact_refs; stripped",
                claim_text=claim.text,
            ))
            return replace(claim, fact_refs=[]), warnings
        return claim, warnings


__all__ = ["CitationValidator"]
