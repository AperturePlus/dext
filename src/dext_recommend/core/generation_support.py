"""Shared constrained-generation post-processing for recommend operations."""
from __future__ import annotations

from dataclasses import replace

from dext_grounded import ContentClass, FactBundle, GenerationResult, GenerationWarning
from dext_grounded._output import empty_output, remove_output_fragments
from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import RecommendationWarning

_CONTENT_POLICY_ERROR_CODES = {
    "content_policy_refusal",
    "political_sensitive",
    "personal_attack",
    "sexual_content",
    "violent_content",
    "mentor_attack",
}


def _ref_key(ref) -> tuple[str, str, str]:
    return (ref.doc_path, ref.heading_path, ref.chunk_hash)


def _canonical_refs_for_indices(indices, bundle: FactBundle):
    if not isinstance(indices, list) or not indices:
        return None
    refs = []
    seen: set[tuple[str, str, str]] = set()
    for fact_index in indices:
        if (
            not isinstance(fact_index, int)
            or isinstance(fact_index, bool)
            or fact_index < 0
            or fact_index >= len(bundle.facts)
        ):
            return None
        fact_refs = tuple(bundle.facts[fact_index].source_refs)
        if not fact_refs:
            return None
        for ref in fact_refs:
            key = _ref_key(ref)
            if key in seen:
                continue
            seen.add(key)
            refs.append(ref)
    return tuple(refs)


def validate_fact_index_support(
    result: GenerationResult,
    bundle: FactBundle,
) -> GenerationResult:
    """Resolve fact claims from fact_indices before citation validation."""
    output_claims = result.output.get("claims", ()) if isinstance(result.output, dict) else ()
    kept = []
    dropped: list[str] = []
    warnings = list(result.warnings)
    for index, claim in enumerate(result.claims):
        raw = (
            output_claims[index]
            if index < len(output_claims) and isinstance(output_claims[index], dict)
            else {}
        )
        if claim.content_class == ContentClass.FACT:
            canonical_refs = _canonical_refs_for_indices(raw.get("fact_indices"), bundle)
            if not canonical_refs:
                dropped.append(claim.text)
                warnings.append(GenerationWarning(
                    code=RecommendationErrorCode.INSUFFICIENT_FACTS.value,
                    message="fact claim failed support-map validation",
                    claim_text=claim.text,
                ))
                continue
            claim = replace(claim, fact_refs=canonical_refs)
        elif claim.content_class == ContentClass.UNCERTAIN and claim.fact_refs:
            claim = replace(claim, fact_refs=())
        kept.append(claim)
    output = remove_output_fragments(result.output, tuple(dropped))
    if result.claims and not kept:
        warnings.append(GenerationWarning(
            code=RecommendationErrorCode.NO_GROUNDED_OUTPUT.value,
            message="all generated claims failed grounding",
        ))
        output = empty_output(result.output)
    return replace(result, claims=tuple(kept), output=output, warnings=warnings)


def has_grounded_fact_claim(result: GenerationResult) -> bool:
    return any(
        claim.content_class == ContentClass.FACT and bool(claim.fact_refs)
        for claim in result.claims
    )


def map_generation_warnings(
    warnings,
    *,
    generation_unavailable_code: RecommendationErrorCode = (
        RecommendationErrorCode.FOLLOWUP_GENERATION_UNAVAILABLE
    ),
) -> tuple[RecommendationWarning, ...]:
    mapped = []
    for warning in warnings:
        code = warning.code
        severity = "warning"
        if code in {"generation_parse_error", "json_parse_failed", "schema_validation_failed"}:
            code, severity = RecommendationErrorCode.GENERATION_PARSE_ERROR.value, "error"
        elif code == "generation_unavailable":
            code, severity = generation_unavailable_code.value, "error"
        elif code in {
            RecommendationErrorCode.LLM_UNAVAILABLE.value,
            RecommendationErrorCode.REQUEST_TIMEOUT.value,
        }:
            severity = "error"
        elif code in {"no_grounded_output", "unsafe_advice"} | _CONTENT_POLICY_ERROR_CODES:
            severity = "error"
        mapped.append(RecommendationWarning(code=code, message=warning.message, severity=severity))
    return tuple(mapped)


__all__ = [
    "has_grounded_fact_claim",
    "map_generation_warnings",
    "validate_fact_index_support",
]
