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


@dataclass(frozen=True, slots=True)
class OperationConfig:
    system_prompt_id: str
    json_schema: Mapping[str, Any]
    timeout: float
    token_budget: int
    # implicit_intent-only optional knobs
    confidence_threshold: float | None = None
    query_max_chars: int | None = None
    summary_max_chars: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.system_prompt_id, str) or not self.system_prompt_id:
            raise ValueError("operation.system_prompt_id must be non-empty str")
        if not isinstance(self.timeout, (int, float)) or isinstance(self.timeout, bool) or self.timeout <= 0:
            raise ValueError("operation.timeout must be positive number")
        if not isinstance(self.token_budget, int) or isinstance(self.token_budget, bool) or self.token_budget <= 0:
            raise ValueError("operation.token_budget must be positive int")
        if not isinstance(self.json_schema, Mapping):
            raise ValueError("operation.json_schema must be a mapping")
        object.__setattr__(self, "json_schema", dict(self.json_schema))
        if self.confidence_threshold is not None:
            if not (0.0 <= float(self.confidence_threshold) <= 1.0):
                raise ValueError("confidence_threshold must be in [0,1]")
        if self.query_max_chars is not None and self.query_max_chars <= 0:
            raise ValueError("query_max_chars must be positive")
        if self.summary_max_chars is not None and self.summary_max_chars <= 0:
            raise ValueError("summary_max_chars must be positive")


@dataclass(frozen=True, slots=True)
class RecommendGenerationProfile:
    version: str
    grounded_rules_manifest_hash: str
    operations: Mapping[str, OperationConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("version must be non-empty str")
        if not isinstance(self.grounded_rules_manifest_hash, str) or not self.grounded_rules_manifest_hash:
            raise ValueError("grounded_rules_manifest_hash must be non-empty str")
        if not isinstance(self.operations, Mapping) or not self.operations:
            raise ValueError("operations must be a non-empty mapping")
        object.__setattr__(self, "operations", dict(self.operations))

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecommendGenerationProfile":
        if "operations" not in payload:
            raise ValueError("operations key required")
        ops_raw = payload["operations"]
        ops: dict[str, OperationConfig] = {}
        for op_id, cfg in ops_raw.items():
            for key in ("system_prompt_id", "json_schema", "timeout", "token_budget"):
                if key not in cfg:
                    raise ValueError(f"{op_id}.{key} required")
            ops[op_id] = OperationConfig(
                system_prompt_id=cfg["system_prompt_id"],
                json_schema=cfg["json_schema"],
                timeout=cfg["timeout"],
                token_budget=cfg["token_budget"],
                confidence_threshold=cfg.get("confidence_threshold"),
                query_max_chars=cfg.get("query_max_chars"),
                summary_max_chars=cfg.get("summary_max_chars"),
            )
        return cls(
            version=payload["version"],
            grounded_rules_manifest_hash=payload["grounded_rules_manifest_hash"],
            operations=ops,
        )

    @classmethod
    def from_file(cls, path: Path) -> "RecommendGenerationProfile":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


__all__ = ["OperationConfig", "RecommendGenerationProfile"]
