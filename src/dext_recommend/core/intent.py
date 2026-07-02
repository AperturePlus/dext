# src/dext_recommend/core/intent.py
"""Intent routing — execute-only, no classification.

R5 strict routing: terminal failures are surfaced as structured
terminal_issues (severity=error) instead of falling back to new_search.
detail_followup is flagged for the dispatcher (Task 6), not short-circuited.
R3 ranking/filter semantics are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from dext_recommend.errors import RecommendationErrorCode
from dext_recommend.models import RecommendationWarning, RecommendRequest

_RECOMMEND_INTENTS = {"new_search", "more_mentors", "same_field", "refine_direction"}
_ALL_INTENTS = _RECOMMEND_INTENTS | {"detail_followup"}


@dataclass(frozen=True, slots=True)
class RecommendRoute:
    intent: str
    exclude_entity_ids: tuple[str, ...]
    anchor_entity_id: str | None
    refine_merge: bool
    detail_followup: bool
    terminal_issues: tuple[RecommendationWarning, ...]
    warnings: tuple[RecommendationWarning, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "exclude_entity_ids", tuple(self.exclude_entity_ids or ()))
        object.__setattr__(self, "terminal_issues", tuple(self.terminal_issues or ()))
        object.__setattr__(self, "warnings", tuple(self.warnings or ()))


def _terminal(code: RecommendationErrorCode, message: str) -> RecommendationWarning:
    return RecommendationWarning(code=code.value, message=message, severity="error")


def resolve_recommend_route(request: RecommendRequest) -> RecommendRoute:
    ctx = request.conversation_context
    intent = (ctx.intent if ctx is not None else None) or "new_search"
    terminal: list[RecommendationWarning] = []
    warnings: list[RecommendationWarning] = []
    detail_followup = False

    if intent not in _ALL_INTENTS:
        terminal.append(_terminal(RecommendationErrorCode.INVALID_INTENT, f"unknown intent: {intent!r}"))
        return RecommendRoute(
            intent="new_search", exclude_entity_ids=(), anchor_entity_id=None,
            refine_merge=False, detail_followup=False,
            terminal_issues=tuple(terminal), warnings=tuple(warnings),
        )

    if intent == "detail_followup":
        anchor = ctx.anchor_entity_id if ctx is not None else None
        if not anchor:
            terminal.append(_terminal(
                RecommendationErrorCode.DETAIL_FOLLOWUP_REQUIRES_ANCHOR,
                "detail_followup requires anchor_entity_id",
            ))
        detail_followup = True
        return RecommendRoute(
            intent="detail_followup", exclude_entity_ids=(),
            anchor_entity_id=anchor, refine_merge=False,
            detail_followup=detail_followup,
            terminal_issues=tuple(terminal), warnings=tuple(warnings),
        )

    exclude_entity_ids: tuple[str, ...] = ()
    anchor_entity_id: str | None = None
    refine_merge = False

    if intent == "more_mentors":
        prior = tuple(ctx.prior_result_entity_ids) if ctx is not None else ()
        if not prior:
            terminal.append(_terminal(
                RecommendationErrorCode.MORE_MENTORS_REQUIRES_PRIOR,
                "more_mentors requires prior_result_entity_ids",
            ))
        else:
            exclude_entity_ids = prior

    elif intent == "same_field":
        anchor = ctx.anchor_entity_id if ctx is not None else None
        if not anchor:
            terminal.append(_terminal(
                RecommendationErrorCode.SAME_FIELD_REQUIRES_ANCHOR,
                "same_field requires anchor_entity_id",
            ))
        else:
            anchor_entity_id = anchor

    elif intent == "refine_direction":
        refine_merge = True

    return RecommendRoute(
        intent=intent, exclude_entity_ids=exclude_entity_ids,
        anchor_entity_id=anchor_entity_id, refine_merge=refine_merge,
        detail_followup=False, terminal_issues=tuple(terminal),
        warnings=tuple(warnings),
    )


__all__ = ["RecommendRoute", "resolve_recommend_route"]
