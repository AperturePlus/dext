"""OpenAI-compatible production adapter for constrained recommendation generation."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from dext_grounded import (
    Claim, ContentClass, GenerationResult, GenerationWarning, SourceRef,
)
from dext_recommend.core.generation_profile import RecommendGenerationProfile


def _json_default(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _schema_errors(value: Any, schema: dict, path: str = "$" ) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    allowed = expected if isinstance(expected, list) else [expected] if expected else []
    type_ok = not allowed or any(
        (kind == "object" and isinstance(value, dict))
        or (kind == "array" and isinstance(value, list))
        or (kind == "string" and isinstance(value, str))
        or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (kind == "boolean" and isinstance(value, bool))
        or (kind == "null" and value is None)
        for kind in allowed
    )
    if not type_ok:
        return [f"{path}: invalid type"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value not in enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, dict):
        required = set(schema.get("required", ()))
        errors.extend(f"{path}.{key}: required" for key in sorted(required - value.keys()))
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errors.extend(f"{path}.{key}: additional property" for key in value.keys() - properties.keys())
        for key, child in value.items():
            if key in properties:
                errors.extend(_schema_errors(child, properties[key], f"{path}.{key}"))
    if isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            errors.extend(_schema_errors(child, schema["items"], f"{path}[{index}]"))
    return errors


def _source_ref(raw: Any) -> SourceRef | None:
    if isinstance(raw, SourceRef):
        return raw
    if not isinstance(raw, dict):
        return None
    try:
        return SourceRef(
            doc_path=str(raw["doc_path"]), heading_path=str(raw["heading_path"]),
            chunk_hash=str(raw["chunk_hash"]),
            quote_or_summary=str(raw["quote_or_summary"]),
            official_url=raw.get("official_url"), last_verified=raw.get("last_verified"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _claims(output: dict) -> tuple[Claim, ...]:
    claims: list[Claim] = []
    for raw in output.get("claims", ()):
        if not isinstance(raw, dict):
            continue
        try:
            content_class = ContentClass(raw["content_class"])
            refs = tuple(ref for item in raw.get("fact_refs", ()) if (ref := _source_ref(item)))
            claims.append(Claim(text=str(raw["text"]), content_class=content_class, fact_refs=refs))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(claims)


class OpenAICompatibleLLMGenerationAdapter:
    """Stateless LLMGenerationPort backed by an injected AsyncOpenAI-compatible client."""

    def __init__(self, *, client: Any, model: str, profile: RecommendGenerationProfile) -> None:
        if not model:
            raise ValueError("model must be non-empty")
        self._client = client
        self._model = model
        self._profile = profile
        self._operations = {op.system_prompt_id: op for op in profile.operations.values()}

    @classmethod
    def from_settings(cls, settings, profile: RecommendGenerationProfile):
        key = settings.llm_api_key.get_secret_value()
        if not key:
            raise ValueError("recommendation LLM API key is not configured")
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=key, base_url=settings.llm_base_url,
            max_retries=settings.llm_max_retries,
        )
        return cls(client=client, model=settings.llm_model, profile=profile)

    async def generate(
        self, system_prompt_id: str, user_inputs: dict[str, Any], fact_bundle,
        student_context, json_schema: dict | None, generation_profile_version: str,
    ) -> GenerationResult:
        if generation_profile_version != self._profile.version:
            raise ValueError("generation profile version mismatch")
        operation = self._operations.get(system_prompt_id)
        if operation is None:
            raise ValueError(f"unknown system_prompt_id: {system_prompt_id}")
        payload = {
            "user_inputs": user_inputs,
            "fact_bundle": fact_bundle,
            "student_context": student_context,
        }
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": operation.system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=_json_default)},
            ],
            response_format={"type": "json_object"},
            max_tokens=operation.token_budget,
            timeout=operation.timeout,
            stream=False,
        )
        content = response.choices[0].message.content or ""
        try:
            output = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return GenerationResult(
                output={}, warnings=[GenerationWarning(
                    code="generation_parse_error", message="provider returned invalid JSON"
                )],
            )
        schema = json_schema or dict(operation.json_schema)
        errors = _schema_errors(output, schema)
        if errors:
            return GenerationResult(
                output={}, warnings=[GenerationWarning(
                    code="schema_validation_failed", message="; ".join(errors[:5])
                )],
            )
        return GenerationResult(output=output, claims=_claims(output))

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["OpenAICompatibleLLMGenerationAdapter"]
