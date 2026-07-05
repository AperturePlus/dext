"""C5 plan generation profile loader."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


DEFAULT_PLAN_PROFILE_PATH = Path("data/competition/profiles/generation/plan-v1.json")


@dataclass(frozen=True, slots=True)
class PlanGenerationProfile:
    version: str
    safety_domain: str
    operation_id: str
    system_prompt_id: str
    timeout_seconds: float
    max_optional_tasks_per_phase: int
    json_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not all((self.version, self.safety_domain, self.operation_id, self.system_prompt_id)):
            raise ValueError("plan generation profile identifiers must be non-empty")
        if self.timeout_seconds <= 0 or self.max_optional_tasks_per_phase < 0:
            raise ValueError("invalid plan generation profile limits")
        if self.json_schema.get("additionalProperties") is not False:
            raise ValueError("plan generation schema must be closed")
        object.__setattr__(self, "json_schema", MappingProxyType(dict(self.json_schema)))


def load_plan_generation_profile(path: str | Path = DEFAULT_PLAN_PROFILE_PATH) -> PlanGenerationProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    op = payload["operation"]
    return PlanGenerationProfile(
        version=payload["version"], safety_domain=payload["safety_domain"],
        operation_id=op["id"], system_prompt_id=op["system_prompt_id"],
        timeout_seconds=float(op["timeout_seconds"]),
        max_optional_tasks_per_phase=int(op["max_optional_tasks_per_phase"]),
        json_schema=op["json_schema"],
    )


__all__ = ["DEFAULT_PLAN_PROFILE_PATH", "PlanGenerationProfile", "load_plan_generation_profile"]
