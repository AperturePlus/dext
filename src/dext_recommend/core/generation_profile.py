"""Recommendation generation profile — checked-in prompts/schemas/thresholds
(R5 spec §6.7). R6 adds match/email/compare operations to the same artifact
without changing R5 field semantics.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dext_grounded import load_grounded_rules
from dext_recommend._immutable import freeze_mapping

_REQUIRED_OPERATIONS = frozenset({
    "query_understanding",
    "implicit_intent",
    "detail_followup",
    "match_analysis",
    "outreach_email",
    "professor_comparison",
    "quick_actions",
    "conversation_title",
    "achievement_extraction",
})
_REQUIRED_SCHEMA_FIELDS = {
    "query_understanding": frozenset({
        "research_interests", "preferred_universities", "preferred_cities",
        "preferred_org_units", "degree_goal", "mentor_eligibility_requirement",
        "missing_information", "needs_clarification", "confidence",
    }),
    "implicit_intent": frozenset({"intent", "confidence", "rationale"}),
    "detail_followup": frozenset({"answer", "claims"}),
    "match_analysis": frozenset({"summary", "dimension_scores", "next_steps", "claims"}),
    "outreach_email": frozenset({"subject", "body", "claims"}),
    "professor_comparison": frozenset({
        "summary", "professor_notes", "evidence_gaps", "claims",
    }),
    "quick_actions": frozenset({"quick_actions"}),
    "conversation_title": frozenset({"title"}),
    "achievement_extraction": frozenset({"competitions", "research"}),
}


def _read_prompt_file(base_path: Path | None, value: Any, field: str) -> str:
    if base_path is None:
        raise ValueError(f"{field} requires prompt_base_path")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty str")
    rel_path = Path(value)
    if rel_path.is_absolute():
        raise ValueError(f"{field} must be relative to the generation profile")
    root = Path(base_path).resolve()
    path = (root / rel_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field} must stay under the generation profile directory") from exc
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"{field} is unavailable: {value}") from exc
    if not text:
        raise ValueError(f"{field} prompt file must be non-empty")
    return text


def _instructions_from_markdown(text: str, field: str) -> tuple[str, ...]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line:
            lines.append(line)
    if not lines:
        raise ValueError(f"{field} must contain at least one instruction")
    return tuple(lines)


def _inline_instructions(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    instructions = tuple(item.strip() for item in value)
    if not instructions or any(not item for item in instructions):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return instructions


def _resolve_output_contract_instructions(
    payload: Mapping[str, Any], prompt_base_path: Path | None,
) -> tuple[str, ...]:
    has_inline = "output_contract_instructions" in payload
    has_path = "output_contract_prompt_path" in payload
    if has_inline and has_path:
        raise ValueError(
            "output_contract_instructions and output_contract_prompt_path are mutually exclusive"
        )
    if has_path:
        text = _read_prompt_file(
            prompt_base_path,
            payload["output_contract_prompt_path"],
            "output_contract_prompt_path",
        )
        return _instructions_from_markdown(text, "output_contract_prompt_path")
    if has_inline:
        return _inline_instructions(
            payload["output_contract_instructions"],
            "output_contract_instructions",
        )
    raise ValueError("output_contract_prompt_path required")


def _resolve_system_prompt(
    op_id: str, cfg: Mapping[str, Any], prompt_base_path: Path | None,
) -> str:
    has_inline = "system_prompt" in cfg
    has_path = "system_prompt_path" in cfg
    if has_inline and has_path:
        raise ValueError(f"{op_id}.system_prompt and system_prompt_path are mutually exclusive")
    if has_path:
        return _read_prompt_file(
            prompt_base_path,
            cfg["system_prompt_path"],
            f"{op_id}.system_prompt_path",
        )
    if has_inline:
        value = cfg["system_prompt"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{op_id}.system_prompt must be non-empty str")
        return value
    raise ValueError(f"{op_id}.system_prompt_path required")


@dataclass(frozen=True, slots=True)
class OperationConfig:
    system_prompt_id: str
    system_prompt: str
    json_schema: Mapping[str, Any]
    timeout: float
    token_budget: int
    # implicit_intent-only optional knobs
    confidence_threshold: float | None = None
    query_max_chars: int | None = None
    summary_max_chars: int | None = None
    fact_limit: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.system_prompt_id, str) or not self.system_prompt_id:
            raise ValueError("operation.system_prompt_id must be non-empty str")
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError("operation.system_prompt must be non-empty str")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool) or self.timeout <= 0:
            raise ValueError("operation.timeout must be positive number")
        if not isinstance(self.token_budget, int) or isinstance(self.token_budget, bool) or self.token_budget <= 0:
            raise ValueError("operation.token_budget must be positive int")
        if not isinstance(self.json_schema, Mapping) or self.json_schema.get("type") != "object":
            raise ValueError("operation.json_schema must be a mapping")
        object.__setattr__(self, "json_schema", freeze_mapping(self.json_schema))
        if self.confidence_threshold is not None:
            if not (0.0 <= float(self.confidence_threshold) <= 1.0):
                raise ValueError("confidence_threshold must be in [0,1]")
        if self.query_max_chars is not None and self.query_max_chars <= 0:
            raise ValueError("query_max_chars must be positive")
        if self.summary_max_chars is not None and self.summary_max_chars <= 0:
            raise ValueError("summary_max_chars must be positive")
        if (
            self.fact_limit is not None
            and (
                not isinstance(self.fact_limit, int)
                or isinstance(self.fact_limit, bool)
                or self.fact_limit <= 0
            )
        ):
            raise ValueError("fact_limit must be positive int")


@dataclass(frozen=True, slots=True)
class RecommendGenerationProfile:
    version: str
    grounded_rules_manifest_hash: str
    output_contract_instructions: tuple[str, ...] = ()
    operations: Mapping[str, OperationConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("version must be non-empty str")
        if not isinstance(self.grounded_rules_manifest_hash, str) or not self.grounded_rules_manifest_hash:
            raise ValueError("grounded_rules_manifest_hash must be non-empty str")
        if not isinstance(self.operations, Mapping) or not self.operations:
            raise ValueError("operations must be a non-empty mapping")
        output_contract_instructions = tuple(self.output_contract_instructions)
        if not output_contract_instructions or any(
            not isinstance(item, str) or not item.strip()
            for item in output_contract_instructions
        ):
            raise ValueError("output_contract_instructions must be non-empty")
        object.__setattr__(
            self,
            "output_contract_instructions",
            tuple(item.strip() for item in output_contract_instructions),
        )
        missing = sorted(_REQUIRED_OPERATIONS - set(self.operations))
        if missing:
            raise ValueError("missing required operations: " + ", ".join(missing))
        object.__setattr__(self, "operations", freeze_mapping(self.operations))

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, prompt_base_path: Path | None = None,
    ) -> "RecommendGenerationProfile":
        if "version" not in payload:
            raise ValueError("version required")
        if "grounded_rules_manifest_hash" not in payload:
            raise ValueError("grounded_rules_manifest_hash required")
        if "operations" not in payload:
            raise ValueError("operations key required")
        ops_raw = payload["operations"]
        output_contract_instructions = _resolve_output_contract_instructions(
            payload, prompt_base_path
        )
        ops: dict[str, OperationConfig] = {}
        for op_id, cfg in ops_raw.items():
            for key in ("system_prompt_id", "json_schema", "timeout", "token_budget"):
                if key not in cfg:
                    raise ValueError(f"{op_id}.{key} required")
            ops[op_id] = OperationConfig(
                system_prompt_id=cfg["system_prompt_id"],
                system_prompt=_resolve_system_prompt(op_id, cfg, prompt_base_path),
                json_schema=cfg["json_schema"],
                timeout=cfg["timeout"],
                token_budget=cfg["token_budget"],
                confidence_threshold=cfg.get("confidence_threshold"),
                query_max_chars=cfg.get("query_max_chars"),
                summary_max_chars=cfg.get("summary_max_chars"),
                fact_limit=cfg.get("fact_limit"),
            )
        return cls(
            version=payload["version"],
            grounded_rules_manifest_hash=payload["grounded_rules_manifest_hash"],
            output_contract_instructions=output_contract_instructions,
            operations=ops,
        )

    @classmethod
    def from_file(cls, path: Path) -> "RecommendGenerationProfile":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = cls.from_dict(data, prompt_base_path=path.parent)
        expected = load_grounded_rules().manifest_hash
        if profile.grounded_rules_manifest_hash != expected:
            raise ValueError("grounded_rules_manifest_hash does not match loaded grounded rules")
        for operation_id, required in _REQUIRED_SCHEMA_FIELDS.items():
            schema = profile.operations[operation_id].json_schema
            if schema.get("additionalProperties") is not False:
                raise ValueError(f"{operation_id}.json_schema must forbid additional properties")
            if frozenset(schema.get("required", ())) != required:
                raise ValueError(f"{operation_id}.json_schema has invalid required fields")
        return profile


__all__ = ["OperationConfig", "RecommendGenerationProfile"]
