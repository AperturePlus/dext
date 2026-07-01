# src/dext_recommend/core/intent.py
"""Intent routing — execute-only, no classification.

R3 resolves the 4 recommend-path intents into route modifiers and validates
minimum context. detail_followup is short-circuited as unsupported (R5 owns
its execution). Free-text intent classification is out of scope (R5).
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import RecommendationWarning, RecommendRequest

_RECOMMEND_INTENTS = {"new_search", "more_mentors", "same_field", "refine_direction"}
_ALL_INTENTS = _RECOMMEND_INTENTS | {"detail_followup"}


@dataclass(frozen=True, slots=True)
class RecommendRoute:
    intent: str                          # new_search|more_mentors|same_field|refine_direction
    exclude_entity_ids: tuple[str, ...]
    anchor_entity_id: str | None
    refine_merge: bool
    unsupported: str | None              # detail_followup -> "unsupported_for_recommend_core"
    warnings: tuple[RecommendationWarning, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclude_entity_ids", tuple(self.exclude_entity_ids or ()))
        object.__setattr__(self, "warnings", tuple(self.warnings or ()))


def _warn(code: RecommendationErrorCode, message: str) -> RecommendationWarning:
    return RecommendationWarning(code=code.value, message=message, severity="warning")


def resolve_recommend_route(request: RecommendRequest) -> RecommendRoute:
    ctx = request.conversation_context
    intent = (ctx.intent if ctx is not None else None) or "new_search"
    warnings: list[RecommendationWarning] = []

    if intent not in _ALL_INTENTS:
        warnings.append(_warn(RecommendationErrorCode.INVALID_INTENT, f"unknown intent: {intent!r}"))
        intent = "new_search"

    if intent == "detail_followup":
        return RecommendRoute(
            intent="new_search",
            exclude_entity_ids=(), anchor_entity_id=None, refine_merge=False,
            unsupported="unsupported_for_recommend_core", warnings=tuple(warnings),
        )

    exclude_entity_ids: tuple[str, ...] = ()
    anchor_entity_id: str | None = None
    refine_merge = False

    if intent == "more_mentors":
        prior = tuple(ctx.prior_result_entity_ids) if ctx is not None else ()
        if not prior:
            warnings.append(_warn(
                RecommendationErrorCode.MISSING_PRIOR_RESULTS,
                "more_mentors requires prior_result_entity_ids; falling back to new_search",
            ))
            intent = "new_search"
        else:
            exclude_entity_ids = prior

    elif intent == "same_field":
        anchor = ctx.anchor_entity_id if ctx is not None else None
        if not anchor:
            warnings.append(_warn(
                RecommendationErrorCode.MISSING_ANCHOR,
                "same_field requires anchor_entity_id; falling back to new_search",
            ))
            intent = "new_search"
        else:
            anchor_entity_id = anchor

    elif intent == "refine_direction":
        refine_merge = True

    return RecommendRoute(
        intent=intent,
        exclude_entity_ids=exclude_entity_ids,
        anchor_entity_id=anchor_entity_id,
        refine_merge=refine_merge,
        unsupported=None,
        warnings=tuple(warnings),
    )


__all__ = ["RecommendRoute", "resolve_recommend_route"]
