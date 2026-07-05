"""C6 assistant generation profile loader."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

ASSISTANT_GENERATION_PROFILE_VERSION = "competition.assistant.v1"
DEFAULT_ASSISTANT_PROFILE_PATH = Path("data/competition/profiles/generation/assistant-v1.json")


@dataclass(frozen=True, slots=True)
class AssistantGenerationProfile:
    version: str
    safety_domain: str
    operation_id: str
    system_prompt_id: str
    timeout_seconds: float
    max_cards: int
    json_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not all((self.version, self.safety_domain, self.operation_id, self.system_prompt_id)):
            raise ValueError("assistant generation profile identifiers must be non-empty")
        if self.timeout_seconds <= 0 or not 0 <= self.max_cards <= 5:
            raise ValueError("invalid assistant generation profile limits")
        if self.json_schema.get("additionalProperties") is not False:
            raise ValueError("assistant generation schema must be closed")
        object.__setattr__(self, "json_schema", MappingProxyType(dict(self.json_schema)))


def load_assistant_generation_profile(
    path: str | Path = DEFAULT_ASSISTANT_PROFILE_PATH,
) -> AssistantGenerationProfile:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    op = payload["operation"]
    return AssistantGenerationProfile(
        version=payload["version"],
        safety_domain=payload["safety_domain"],
        operation_id=op["id"],
        system_prompt_id=op["system_prompt_id"],
        timeout_seconds=float(op["timeout_seconds"]),
        max_cards=int(op["max_cards"]),
        json_schema=op["json_schema"],
    )


__all__ = [
    "ASSISTANT_GENERATION_PROFILE_VERSION",
    "DEFAULT_ASSISTANT_PROFILE_PATH",
    "AssistantGenerationProfile",
    "load_assistant_generation_profile",
]
