"""Grounded rule-question answering for competition knowledge."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from dext_competition.contracts.qa import GroundedAnswer
from dext_competition.ports.catalog import CompetitionCatalogPort
from dext_competition.ports.knowledge import KnowledgeIndexPort
from dext_competition.qa.retrieval import (
    dedupe_source_refs,
    freshness_notice_for_card,
    question_fact_bundle,
)
from dext_competition.qa.schemas import (
    ANSWER_JSON_SCHEMA,
    ANSWER_SYSTEM_PROMPT_ID,
    FRESHNESS_NOTICE,
    QA_GENERATION_PROFILE_VERSION,
    SAFETY_DOMAIN,
    is_freshness_sensitive,
)
from dext_grounded import (
    Claim,
    ConstrainedGenerationPipeline,
    ContentClass,
    FactBundle,
    GenerationResult,
    GenerationWarning,
    GenerationWarningCode,
    SourceRef,
    StudentContext,
)


def _claim_specs(output: dict | str) -> list[dict[str, Any]]:
    if not isinstance(output, dict):
        return []
    claims = output.get("claims")
    if not isinstance(claims, list):
        return []
    return [item for item in claims if isinstance(item, dict)]


def _refs_for_indices(
    fact_bundle: FactBundle,
    indices: object,
) -> tuple[SourceRef, ...] | None:
    if indices is None:
        return None
    if not isinstance(indices, list):
        return ()
    refs: list[SourceRef] = []
    for value in indices:
        if not isinstance(value, int):
            return ()
        if value < 0 or value >= len(fact_bundle.facts):
            return ()
        refs.extend(fact_bundle.facts[value].source_refs)
    return dedupe_source_refs(tuple(refs))


def support_map_validator(
    result: GenerationResult,
    fact_bundle: FactBundle,
) -> GenerationResult:
    """Attach refs from fact_indices and downgrade unsupported fact claims.

    The pipeline still owns canonical citation validation after this callback;
    this layer only enforces the operation-specific support-map shape.
    """

    specs = _claim_specs(result.output)
    claims: list[Claim] = []
    warnings = list(result.warnings)
    for pos, claim in enumerate(result.claims):
        spec = specs[pos] if pos < len(specs) else {}
        indexed_refs = _refs_for_indices(fact_bundle, spec.get("fact_indices"))
        refs = tuple(claim.fact_refs)
        if indexed_refs is not None:
            refs = indexed_refs

        if claim.content_class == ContentClass.FACT and not refs:
            warnings.append(GenerationWarning(
                code=GenerationWarningCode.INSUFFICIENT_FACTS.value,
                message="fact claim could not be mapped to a bundle fact",
                claim_text=claim.text,
            ))
            claims.append(replace(
                claim,
                content_class=ContentClass.UNCERTAIN,
                fact_refs=(),
            ))
            continue

        if claim.content_class == ContentClass.UNCERTAIN and refs:
            refs = ()
        claims.append(replace(claim, fact_refs=refs))

    return replace(result, claims=tuple(claims), warnings=warnings)


def _output_text(output: dict | str, key: str = "answer") -> str:
    if isinstance(output, str):
        return output
    value = output.get(key) if isinstance(output, dict) else None
    return value if isinstance(value, str) else ""


def _evidence_status(result: GenerationResult) -> str:
    if not result.claims:
        return "uncertain"
    if not result.cited_refs:
        return "uncertain"
    warning_codes = {warning.code for warning in result.warnings}
    partial_codes = {
        GenerationWarningCode.FACT_REF_MISSING.value,
        GenerationWarningCode.FABRICATED_REF.value,
        GenerationWarningCode.INSUFFICIENT_FACTS.value,
        GenerationWarningCode.NO_GROUNDED_OUTPUT.value,
        GenerationWarningCode.STALE_FACT.value,
    }
    if warning_codes & partial_codes:
        return "partial"
    if any(claim.content_class == ContentClass.UNCERTAIN for claim in result.claims):
        return "partial"
    return "grounded"


def _freshness_notice(
    *,
    question: str,
    result: GenerationResult,
    card_notice: str | None,
) -> str | None:
    has_stale_warning = any(
        warning.code == GenerationWarningCode.STALE_FACT.value
        for warning in result.warnings
    )
    if card_notice or is_freshness_sensitive(question) or has_stale_warning:
        return card_notice or FRESHNESS_NOTICE
    return None


async def answer_competition_question(
    *,
    question: str,
    catalog: CompetitionCatalogPort,
    index: KnowledgeIndexPort,
    pipeline: ConstrainedGenerationPipeline,
    competition_id: str | None = None,
    student_context: StudentContext | None = None,
    limit: int = 8,
    include_contacts: bool = False,
    generation_profile_version: str = QA_GENERATION_PROFILE_VERSION,
) -> GroundedAnswer:
    """Answer a competition rule question using C1/C2 facts and the shared pipeline."""

    if not question.strip():
        raise ValueError("question must be non-empty")

    bundle, card, _hits = await question_fact_bundle(
        question=question,
        catalog=catalog,
        index=index,
        competition_id=competition_id,
        limit=limit,
    )
    card_notice = freshness_notice_for_card(card) if card is not None else None
    if not bundle.source_refs:
        return GroundedAnswer(
            question=question,
            answer="知识库中没有足够可引用证据回答该问题。",
            evidence_status="uncertain",
            internal_source_refs=(),
            freshness_notice=card_notice or (
                FRESHNESS_NOTICE if is_freshness_sensitive(question) else None
            ),
            competition_id=competition_id,
        )

    result = await pipeline.generate(
        system_prompt_id=ANSWER_SYSTEM_PROMPT_ID,
        user_inputs={
            "question": question,
            "competition_id": competition_id,
            "display_name": card.display_name if card is not None else None,
        },
        fact_bundle=bundle,
        student_context=student_context,
        json_schema=ANSWER_JSON_SCHEMA,
        generation_profile_version=generation_profile_version,
        safety_domain=SAFETY_DOMAIN,
        include_contacts=include_contacts,
        operation_id="competition_qa_answer",
        support_validator=support_map_validator,
    )

    return GroundedAnswer(
        question=question,
        answer=_output_text(result.output),
        evidence_status=_evidence_status(result),
        internal_source_refs=tuple(result.cited_refs),
        freshness_notice=_freshness_notice(
            question=question,
            result=result,
            card_notice=card_notice,
        ),
        competition_id=competition_id,
    )


__all__ = ["answer_competition_question", "support_map_validator"]

