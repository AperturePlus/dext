from __future__ import annotations

import inspect

from dext_grounded import (
    FactBundle, FakeLLMGenerationPort, GenerationResult, LLMGenerationPort,
    StudentContext,
)


def test_llm_generation_port_is_protocol():
    assert hasattr(LLMGenerationPort, "_is_protocol") or LLMGenerationPort._is_runtime_protocol


def test_llm_generation_port_generate_signature():
    sig = inspect.signature(LLMGenerationPort.generate)
    params = list(sig.parameters)
    # 'self' + the six spec params (§4)
    assert params[1:] == [
        "system_prompt_id", "user_inputs", "fact_bundle", "student_context",
        "json_schema", "generation_profile_version",
    ]


def test_fake_llm_generation_port_returns_preset_result():
    preset = GenerationResult(output={"summary": "ok"})
    port = FakeLLMGenerationPort(preset)
    assert isinstance(port, LLMGenerationPort)
    result = port.generate(
        system_prompt_id="match-analysis-v1",
        user_inputs={},
        fact_bundle=FactBundle(build_id="b", subject_id="s", facts=[], source_refs=[]),
        student_context=StudentContext(),
        json_schema={},
        generation_profile_version="gen-v1.0",
    )
    assert result is preset


def test_fake_llm_generation_port_records_calls():
    preset = GenerationResult(output="hi")
    port = FakeLLMGenerationPort(preset)
    port.generate(
        system_prompt_id="p1", user_inputs={"x": 1},
        fact_bundle=FactBundle(build_id="b", subject_id="s", facts=[], source_refs=[]),
        student_context=None, json_schema=None, generation_profile_version="v1",
    )
    assert len(port.calls) == 1
    assert port.calls[0]["system_prompt_id"] == "p1"
    assert port.calls[0]["generation_profile_version"] == "v1"
