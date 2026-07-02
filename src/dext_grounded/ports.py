"""LLM generation port — the constrained-generation protocol (spec §4).

``generate`` is a raw provider generation call. It returns a GenerationResult
that is NOT yet citation/safety-validated — callers MUST pass it through
ConstrainedGenerationPipeline.generate before handing it to business code.
The concrete LLM client lives in each consumer module (recommend/
competition); this package only fixes the contract and a test fake.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from dext_grounded.claims import GenerationResult
from dext_grounded.fact_bundle import FactBundle
from dext_grounded.student_context import StudentContext


@runtime_checkable
class LLMGenerationPort(Protocol):
    async def generate(
        self,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
    ) -> GenerationResult:
        ...


class FakeLLMGenerationPort:
    """Test double returning a preset result; records calls for assertions."""

    def __init__(self, preset: GenerationResult) -> None:
        self._preset = preset
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle: FactBundle,
        student_context: StudentContext | None,
        json_schema: dict | None,
        generation_profile_version: str,
    ) -> GenerationResult:
        self.calls.append(
            {
                "system_prompt_id": system_prompt_id,
                "user_inputs": user_inputs,
                "fact_bundle": fact_bundle,
                "student_context": student_context,
                "json_schema": json_schema,
                "generation_profile_version": generation_profile_version,
            }
        )
        return self._preset


__all__ = ["LLMGenerationPort", "FakeLLMGenerationPort"]
