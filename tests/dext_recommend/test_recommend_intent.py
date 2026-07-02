from __future__ import annotations

import pytest

from dext_recommend import ConversationContext, RecommendRequest
from dext_recommend.core.intent import RecommendRoute, resolve_recommend_route
from dext_recommend.models import RecommendationWarning


def _req(intent: str | None = None, *, prior: tuple[str, ...] = (),
         anchor: str | None = None, query: str = "NLP",
         intent_source: str = "explicit") -> RecommendRequest:
    return RecommendRequest(
        query_text=query,
        conversation_context=ConversationContext(
            intent=intent, intent_source=intent_source,
            prior_result_entity_ids=prior, anchor_entity_id=anchor,
        ) if intent or anchor or prior else None,
    )


def test_new_search_when_no_conversation_context():
    route = resolve_recommend_route(RecommendRequest(query_text="NLP"))
    assert route.intent == "new_search"
    assert route.exclude_entity_ids == ()
    assert route.anchor_entity_id is None
    assert route.refine_merge is False
    assert route.detail_followup is False
    assert route.terminal_issues == ()
    assert route.warnings == ()


def test_new_search_explicit():
    route = resolve_recommend_route(_req("new_search"))
    assert route.intent == "new_search"
    assert route.warnings == ()


def test_more_mentors_excludes_prior():
    route = resolve_recommend_route(_req("more_mentors", prior=("e1", "e2")))
    assert route.intent == "more_mentors"
    assert route.exclude_entity_ids == ("e1", "e2")


def test_more_mentors_without_prior_returns_terminal_error():
    route = resolve_recommend_route(_req("more_mentors", prior=()))
    assert route.intent == "more_mentors"
    codes = [w.code for w in route.terminal_issues]
    assert "more_mentors_requires_prior" in codes
    assert all(w.severity == "error" for w in route.terminal_issues)


def test_same_field_uses_anchor():
    route = resolve_recommend_route(_req("same_field", anchor="e_anchor"))
    assert route.intent == "same_field"
    assert route.anchor_entity_id == "e_anchor"


def test_same_field_without_anchor_returns_terminal_error():
    route = resolve_recommend_route(_req("same_field"))
    codes = [w.code for w in route.terminal_issues]
    assert "same_field_requires_anchor" in codes


def test_refine_direction_sets_merge():
    route = resolve_recommend_route(_req("refine_direction"))
    assert route.intent == "refine_direction"
    assert route.refine_merge is True


def test_detail_followup_sets_flag_not_unsupported():
    route = resolve_recommend_route(_req("detail_followup", anchor="e1"))
    assert route.detail_followup is True
    assert route.terminal_issues == ()
    assert not hasattr(route, "unsupported")


def test_invalid_intent_returns_terminal_error():
    route = resolve_recommend_route(_req("bogus_intent"))
    codes = [w.code for w in route.terminal_issues]
    assert "invalid_intent" in codes
    assert route.detail_followup is False


def test_detail_followup_without_anchor_returns_terminal_error():
    route = resolve_recommend_route(_req("detail_followup"))
    codes = [w.code for w in route.terminal_issues]
    assert "detail_followup_requires_anchor" in codes


def test_recommend_route_is_immutable():
    route = resolve_recommend_route(_req("more_mentors", prior=("e1",)))
    with pytest.raises((AttributeError, TypeError)):
        route.exclude_entity_ids.append("e2")
    with pytest.raises((AttributeError, TypeError)):
        route.warnings.append(RecommendationWarning("x", "y"))
