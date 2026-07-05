"""OpenAI-compatible generation adapter for competition grounded operations."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from dext_grounded import GenerationResult, GenerationWarning


def _schema_errors(value: Any, schema: dict, path: str = "$") -> list[str]:
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


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return [_jsonable(child) for child in value]
    return value


def _json_default(value: object) -> object:
    converted = _jsonable(value)
    if converted is not value:
        return converted
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _value(raw: Any, name: str) -> Any:
    if isinstance(raw, Mapping):
        return raw.get(name)
    return getattr(raw, name, None)


def _content_part_text(part: Any) -> str | None:
    if isinstance(part, str):
        return part
    text = _value(part, "text")
    if isinstance(text, str):
        return text
    content = _value(part, "content")
    if isinstance(content, str):
        return content
    return None


def _message_content(response: Any) -> str | None:
    choices = _value(response, "choices")
    try:
        choice = choices[0]
    except (IndexError, TypeError):
        return None
    message = _value(choice, "message")
    content = _value(message, "content")
    if isinstance(content, str):
        stripped = content.strip()
        return stripped or None
    if isinstance(content, list):
        combined = "".join(text for part in content if (text := _content_part_text(part)))
        stripped = combined.strip()
        return stripped or None
    return None


class CompetitionOpenAICompatibleGenerationAdapter:
    """Profile-agnostic LLMGenerationPort for dext_competition operations."""

    def __init__(self, *, client: Any, model: str, timeout: float) -> None:
        if not model:
            raise ValueError("model must be non-empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = client
        self._model = model
        self._timeout = timeout

    async def generate(
        self,
        system_prompt_id: str,
        user_inputs: dict[str, Any],
        fact_bundle,
        student_context,
        json_schema: dict | None,
        generation_profile_version: str,
    ) -> GenerationResult:
        schema = _jsonable(json_schema or {"type": "object"})
        payload = {
            "operation": system_prompt_id,
            "generation_profile_version": generation_profile_version,
            "user_inputs": user_inputs,
            "fact_bundle": fact_bundle,
            "student_context": student_context,
            "output_contract": {
                "json_schema": schema,
                "requirements": [
                    "只返回一个 JSON object，不要 Markdown，不要解释。",
                    "reply、summary、rationale、advice_text 必须使用简体中文。",
                    "只能使用 user_inputs.plan_snapshot 中存在的 phase key、task id 和日期范围。",
                    "如果用户要求移动任务，优先输出 move_task，并填写 target_task_id 与 YYYY-MM-DD 格式 new_date。",
                    "如果无法安全修改计划，输出 append_advice，而不是询问更多信息。",
                    "不要编造报名日期、获奖信息、主办方或规则事实。",
                ],
            },
        }
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _system_prompt(system_prompt_id)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=_json_default)},
            ],
            response_format={"type": "json_object"},
            max_tokens=1600,
            timeout=self._timeout,
            stream=False,
        )
        content = _message_content(response)
        if content is None:
            return GenerationResult(
                output={},
                warnings=(GenerationWarning("llm_unavailable", "provider response missing message content"),),
            )
        try:
            output = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return GenerationResult(
                output={},
                warnings=(GenerationWarning("generation_parse_error", "provider returned invalid JSON"),),
            )
        errors = _schema_errors(output, schema)
        if errors:
            return GenerationResult(
                output={},
                warnings=(GenerationWarning("schema_validation_failed", "; ".join(errors[:5])),),
            )
        return GenerationResult(output=output)

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


def _system_prompt(system_prompt_id: str) -> str:
    if "assistant" in system_prompt_id:
        return (
            "你是中文竞赛备赛计划助手。你的任务是根据当前 plan_snapshot 和用户消息，"
            "生成可由用户审批的计划改动卡片。必须遵守："
            "1. 只输出符合 schema 的 JSON object；"
            "2. reply 和所有卡片文案使用简体中文；"
            "3. cards 最多 5 张；"
            "4. move_task 必须使用现有 target_task_id，new_date 必须是 YYYY-MM-DD；"
            "5. add_task 必须使用现有 target_phase_key，new_task.due_date 必须在该阶段日期内；"
            "6. 不确定时生成 append_advice，不要返回英文澄清句。"
        )
    if "query" in system_prompt_id:
        return (
            "从用户请求中提取竞赛推荐偏好。只输出符合 schema 的 JSON object，文本使用简体中文。"
        )
    return "Return JSON matching the provided schema."


__all__ = ["CompetitionOpenAICompatibleGenerationAdapter"]
