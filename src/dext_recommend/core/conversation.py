"""Conversation adapter — context validation, implicit classification, dispatch,
and detail_followup grounded generation (R5 spec).

Pure functions stay synchronous; LLM/store I/O is async. The dispatcher is the
sole public conversation entry point returning ConversationDispatchResult.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Literal

from dext_recommend.models import ConversationContext

_VALID_INTENTS = {
    "new_search", "more_mentors", "same_field", "refine_direction", "detail_followup",
}
_VALID_SOURCES = {"explicit", "implicit", None}


class ConversationValidationError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(f"{code}: {safe_message}")


def _dedup_preserve_order(items) -> tuple:
    seen: set = set()
    out: list = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return tuple(out)


def _validate_context(
    context: ConversationContext,
    *,
    phase: Literal["input", "resolved"],
) -> ConversationContext:
    """Return a normalized copy; raise ConversationValidationError on failure."""
    src = context.intent_source
    intent = context.intent
    conf = context.intent_confidence

    if src not in _VALID_SOURCES:
        raise ConversationValidationError("invalid_intent", f"bad intent_source: {src!r}")
    if intent is not None and intent not in _VALID_INTENTS:
        raise ConversationValidationError("invalid_intent", f"bad intent: {intent!r}")

    if src == "explicit":
        if intent is None:
            raise ConversationValidationError("invalid_intent", "explicit requires intent")
        if conf is not None:
            raise ConversationValidationError("invalid_intent",
                                              "explicit must not carry confidence")
    elif src == "implicit":
        if phase == "input":
            if intent is not None or conf is not None:
                raise ConversationValidationError("invalid_intent",
                                                  "input implicit must be unclassified")
        else:  # resolved
            if intent not in _VALID_INTENTS:
                raise ConversationValidationError("invalid_intent",
                                                  "resolved implicit needs whitelist intent")
            if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= conf <= 1.0):
                raise ConversationValidationError("invalid_intent",
                                                  "resolved implicit confidence out of [0,1]")
    else:  # src is None
        if intent is not None or conf is not None:
            raise ConversationValidationError("invalid_intent",
                                              "None source must not carry intent/confidence")
        if phase == "input":
            context = replace(context, intent_source="implicit")

    # session/turn co-occurrence
    has_s = context.session_id is not None
    has_t = context.turn_id is not None
    if has_s != has_t:
        raise ConversationValidationError("invalid_conversation_state",
                                          "session_id and turn_id must co-occur")
    # fork pair co-occurrence
    has_m = context.main_session_id is not None
    has_st = context.source_turn_id is not None
    if has_m != has_st:
        raise ConversationValidationError("invalid_conversation_state",
                                          "main_session_id and source_turn_id must co-occur")

    deduped = _dedup_preserve_order(context.prior_result_entity_ids)
    if deduped != tuple(context.prior_result_entity_ids):
        context = replace(context, prior_result_entity_ids=deduped)
    return context


def _assemble_context(
    *, session_id, turn_id, main_session_id, source_turn_id,
    anchor_entity_id, intent, intent_source, intent_confidence,
    prior_result_entity_ids,
) -> ConversationContext:
    ctx = ConversationContext(
        session_id=session_id, turn_id=turn_id,
        main_session_id=main_session_id, source_turn_id=source_turn_id,
        anchor_entity_id=anchor_entity_id, intent=intent,
        intent_source=intent_source, intent_confidence=intent_confidence,
        prior_result_entity_ids=tuple(prior_result_entity_ids or ()),
    )
    return _validate_context(ctx, phase="input")


__all__ = [
    "ConversationValidationError", "_validate_context", "_assemble_context",
]
