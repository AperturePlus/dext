"""Checked-in generation profile for competition query understanding."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


DEFAULT_QUERY_PROFILE_PATH = Path(
    "data/competition/profiles/generation/query-understanding-v1.json"
)


@dataclass(frozen=True, slots=True)
class QueryUnderstandingProfile:
    version: str
    safety_domain: str
    operation_id: str
    system_prompt_id: str
    timeout_seconds: float
    json_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        for value, label in (
            (self.version, "version"),
            (self.safety_domain, "safety_domain"),
            (self.operation_id, "operation_id"),
            (self.system_prompt_id, "system_prompt_id"),
        ):
            if not value:
                raise ValueError(f"{label} must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.json_schema.get("type") != "object":
            raise ValueError("json_schema must describe an object")
        if self.json_schema.get("additionalProperties") is not False:
            raise ValueError("json_schema must reject additional properties")
        object.__setattr__(self, "json_schema", MappingProxyType(dict(self.json_schema)))


def load_query_understanding_profile(
    path: str | Path = DEFAULT_QUERY_PROFILE_PATH,
) -> QueryUnderstandingProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    operation = payload["operation"]
    return QueryUnderstandingProfile(
        version=str(payload["version"]),
        safety_domain=str(payload["safety_domain"]),
        operation_id=str(operation["id"]),
        system_prompt_id=str(operation["system_prompt_id"]),
        timeout_seconds=float(operation["timeout_seconds"]),
        json_schema=operation["json_schema"],
    )


__all__ = [
    "DEFAULT_QUERY_PROFILE_PATH",
    "QueryUnderstandingProfile",
    "load_query_understanding_profile",
]
