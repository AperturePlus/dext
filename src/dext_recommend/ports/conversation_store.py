"""Store-neutral conversation persistence contract; PostgreSQL belongs to R7b."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from dext_recommend.models import ConversationContext, ConversationSummary


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    build_id: str
    ranking_profile_version: str
    generation_profile_version: str | None
    result_entity_ids: tuple[str, ...]
    intent: str | None
    intent_source: str | None
    sanitized_summary: str | None
    created_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_entity_ids", tuple(self.result_entity_ids or ()))


@runtime_checkable
class ConversationStorePort(Protocol):
    async def load_context(self, session_id: str, turn_id: str | None) -> ConversationContext | None: ...
    async def load_summary(self, session_id: str, through_turn_id: str | None) -> ConversationSummary | None: ...
    async def save_turn(self, session_id: str, turn_id: str, context: ConversationContext,
                        snapshot: TurnSnapshot) -> None: ...
    async def list_prior_entity_ids(self, session_id: str, limit: int = 50) -> tuple[str, ...]: ...
    async def resolve_fork(self, main_session_id: str,
                           source_turn_id: str) -> ConversationContext | None: ...


__all__ = ["ConversationStorePort", "TurnSnapshot"]
