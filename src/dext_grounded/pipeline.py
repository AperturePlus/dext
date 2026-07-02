"""ConstrainedGenerationPipeline — the only caller-facing validated-generation
boundary (grounded-generation spec §4/§5/§6).

raw LLMGenerationPort.generate
  -> JSON/schema parse (delegated to the provider's json_schema mode)
  -> operation-specific support-map validation (optional callback)
  -> CitationValidator.validate
  -> SafetyGuard.inspect
  -> validated GenerationResult

Business code (dext_recommend, dext_competition) MUST call this pipeline, not
the raw LLMGenerationPort, and MUST NOT re-run CitationValidator on the result
(double-warning). The raw port returns a GenerationResult that is NOT yet
citation/safety-validated; only this pipeline returns the final, safe result.
"""
from __future__ import annotations

from typing import Any, Callable

from dext_grounded.claims import GenerationResult
from dext_grounded.citation import CitationValidator
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.ports import LLMGenerationPort
from dext_grounded.safety import SafetyGuard
from dext_grounded.student_context import StudentContext

SupportValidator = Callable[[GenerationResult, FactBundle], GenerationResult]


class ConstrainedGenerationPipeline:
    """Wraps a raw LLMGenerationPort with citation + safety validation.

    The pipeline instance holds no request-mutable state. Each generate() call
    is independent.
    """

    def __init__(
        self,
        llm_port: LLMGenerationPort,
        citation: CitationValidator | None = None,
        safety: SafetyGuard | None = None,
    ) -> None:
        self._llm_port = llm_port
        self._citation = citation or CitationValidator()
        self._safety = safety or SafetyGuard()

    async def generate(
        self,
        *,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
        safety_domain: str,
        include_contacts: bool = False,
        operation_id: str | None = None,
        subject_kind: str | None = None,
        support_validator: SupportValidator | None = None,
    ) -> GenerationResult:
        preflight = self._safety.inspect_input(
            user_inputs,
            domain=safety_domain,
            operation=operation_id,
            subject_kind=subject_kind,
            output_template={} if json_schema is not None else "",
        )
        if preflight is not None:
            return preflight
        raw = await self._llm_port.generate(
            system_prompt_id=system_prompt_id,
            user_inputs=user_inputs,
            fact_bundle=fact_bundle,
            student_context=student_context,
            json_schema=json_schema,
            generation_profile_version=generation_profile_version,
        )
        result = raw
        if support_validator is not None:
            result = support_validator(result, fact_bundle)
        result = self._citation.validate(result, fact_bundle, student_context)
        result = self._safety.inspect(
            result, domain=safety_domain, include_contacts=include_contacts,
            operation=operation_id, subject_kind=subject_kind,
        )
        return result


__all__ = ["ConstrainedGenerationPipeline", "SupportValidator"]
