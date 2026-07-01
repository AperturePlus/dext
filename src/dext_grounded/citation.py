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

import hashlib
from dataclasses import replace

from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.codes import GenerationWarningCode
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.rules import GroundedRules, load_grounded_rules
from dext_grounded.source_ref import SourceRef
from dext_grounded.student_context import StudentContext


def _ref_content_hash(ref: SourceRef) -> str:
    """Stable hash of the ref's content fields (spec §5.2 canonical identity).

    Two refs with the same (doc_path, heading_path, chunk_hash) triple but
    different content (quote_or_summary/official_url/last_verified) are NOT the
    same ref — the LLM one is "冒充真实引用" (impersonating a real ref).
    """
    payload = "\x1f".join((
        ref.quote_or_summary or "",
        ref.official_url or "",
        ref.last_verified or "",
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bundle_ref_index(
    bundle: FactBundle,
) -> dict[tuple[str, str, str], tuple[SourceRef, str]]:
    """Map (doc_path, heading_path, chunk_hash) → (canonical SourceRef, content_hash)."""
    index: dict[tuple[str, str, str], tuple[SourceRef, str]] = {}
    for ref in bundle.source_refs:
        key = (ref.doc_path, ref.heading_path, ref.chunk_hash)
        index[key] = (ref, _ref_content_hash(ref))
    return index


def _present_student_fields(
    student_context: StudentContext | None,
    allowed_fields: tuple[str, ...],
) -> set[str]:
    if student_context is None:
        return set()
    present: set[str] = set()
    for name in allowed_fields:
        value = getattr(student_context, name, None)
        if isinstance(value, list):
            if value:
                present.add(name)
        elif value not in (None, ""):
            present.add(name)
    return present


class CitationValidator:
    def __init__(self, rules: GroundedRules | None = None) -> None:
        self.rules = rules or load_grounded_rules()

    def validate(
        self,
        result: GenerationResult,
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
    ) -> GenerationResult:
        ref_index = _bundle_ref_index(fact_bundle)
        present_fields = _present_student_fields(
            student_context, self.rules.student_context_fields,
        )
        warnings: list[GenerationWarning] = list(result.warnings)
        kept_claims: list[Claim] = []

        for claim in result.claims:
            new_claim, claim_warnings = self._validate_claim(
                claim, ref_index, present_fields,
            )
            warnings.extend(claim_warnings)
            if new_claim is not None:
                kept_claims.append(new_claim)

        if result.claims and not kept_claims:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.NO_GROUNDED_OUTPUT.value,
                message=self.rules.warning_messages[
                    GenerationWarningCode.NO_GROUNDED_OUTPUT.value
                ],
            ))

        cited: list = []
        for claim in kept_claims:
            cited.extend(claim.fact_refs)
        # dedupe cited refs by (doc_path, heading_path, chunk_hash) preserving order;
        # all refs here are already canonical bundle objects
        seen: set[tuple[str, str, str]] = set()
        unique_cited = []
        for ref in cited:
            key = (ref.doc_path, ref.heading_path, ref.chunk_hash)
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
        ref_index: dict[tuple[str, str, str], tuple[SourceRef, str]],
        present_fields: set[str],
    ) -> tuple[Claim | None, list[GenerationWarning]]:
        warnings: list[GenerationWarning] = []
        if claim.content_class == ContentClass.FACT:
            if not claim.fact_refs:
                # downgrade to uncertain (spec §5.2)
                warnings.append(GenerationWarning(
                    code=GenerationWarningCode.FACT_REF_MISSING.value,
                    message=self.rules.warning_messages[
                        GenerationWarningCode.FACT_REF_MISSING.value
                    ],
                    claim_text=claim.text,
                ))
                return replace(claim, content_class=ContentClass.UNCERTAIN), warnings
            canonical_refs, fabricated_count = self._resolve_refs(
                claim.fact_refs, ref_index,
            )
            if fabricated_count:
                message = (
                    self.rules.warning_messages[GenerationWarningCode.FABRICATED_REF.value]
                    + f": {fabricated_count}"
                )
                warnings.append(GenerationWarning(
                    code=GenerationWarningCode.FABRICATED_REF.value,
                    message=message,
                    claim_text=claim.text,
                ))
                return None, warnings
            return replace(claim, fact_refs=canonical_refs), warnings

        if claim.content_class == ContentClass.ADVICE:
            if claim.user_context_ref is not None:
                field = claim.user_context_ref.field
                if field not in present_fields:
                    message = (
                        self.rules.warning_messages[
                            GenerationWarningCode.FABRICATED_USER_CONTEXT.value
                        ]
                        + f" {field!r}"
                    )
                    warnings.append(GenerationWarning(
                        code=GenerationWarningCode.FABRICATED_USER_CONTEXT.value,
                        message=message,
                        claim_text=claim.text,
                    ))
                    claim = replace(claim, user_context_ref=None)
            # advice may carry fact_refs; if present, validate each the same way
            # as a fact claim (spec §5.2). Fabricated advice refs are treated
            # identically to fabricated fact refs.
            if claim.fact_refs:
                canonical_refs, fabricated_count = self._resolve_refs(
                    claim.fact_refs, ref_index,
                )
                if fabricated_count:
                    message = (
                        self.rules.warning_messages[
                            GenerationWarningCode.FABRICATED_REF.value
                        ]
                        + f": {fabricated_count}"
                    )
                    warnings.append(GenerationWarning(
                        code=GenerationWarningCode.FABRICATED_REF.value,
                        message=message,
                        claim_text=claim.text,
                    ))
                    if not canonical_refs:
                        # all advice refs fabricated → drop the whole claim
                        return None, warnings
                    claim = replace(claim, fact_refs=canonical_refs)
            return claim, warnings

        # uncertain
        if claim.fact_refs:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.UNCERTAIN_CLAIM_WITH_REFS.value,
                message=self.rules.warning_messages[
                    GenerationWarningCode.UNCERTAIN_CLAIM_WITH_REFS.value
                ],
                claim_text=claim.text,
            ))
            return replace(claim, fact_refs=[]), warnings
        return claim, warnings

    def _resolve_refs(
        self,
        refs: list[SourceRef],
        ref_index: dict[tuple[str, str, str], tuple[SourceRef, str]],
    ) -> tuple[list[SourceRef], int]:
        """Resolve each LLM-reported ref to its canonical bundle SourceRef.

        Returns (canonical_refs, fabricated_count). A ref is genuine iff its
        (doc_path, heading_path, chunk_hash) triple is in the bundle AND its
        content hash matches the bundle's canonical entry. Triple match with a
        content-hash mismatch is the "冒充真实引用" case → fabricated. Triple
        miss → fabricated. Genuine refs are replaced by the bundle's canonical
        object (never the LLM's self-reported copy).
        """
        canonical: list[SourceRef] = []
        fabricated = 0
        for ref in refs:
            key = (ref.doc_path, ref.heading_path, ref.chunk_hash)
            entry = ref_index.get(key)
            if entry is None:
                fabricated += 1
                continue
            bundle_ref, bundle_hash = entry
            if _ref_content_hash(ref) != bundle_hash:
                # same triple, different content → impersonation
                fabricated += 1
                continue
            canonical.append(bundle_ref)
        return canonical, fabricated


__all__ = ["CitationValidator"]
