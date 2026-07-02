from __future__ import annotations

import pytest

from dext_recommend import ConversationContext
from dext_recommend.core.conversation import (
    ConversationValidationError, _assemble_context, _validate_context,
)


def test_input_phase_normalizes_all_none_to_implicit():
    ctx = ConversationContext()
    out = _validate_context(ctx, phase="input")
    assert out.intent_source == "implicit"
    assert out.intent is None
    assert out.intent_confidence is None


def test_input_phase_rejects_partial_implicit_prefill():
    ctx = ConversationContext(intent_source="implicit", intent="more_mentors")
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(ctx, phase="input")
    assert exc.value.code == "invalid_intent"


def test_input_phase_rejects_explicit_with_confidence():
    ctx = ConversationContext(intent_source="explicit", intent="new_search",
                              intent_confidence=0.9)
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(ctx, phase="input")
    assert exc.value.code == "invalid_intent"


def test_input_phase_rejects_none_source_with_intent():
    ctx = ConversationContext(intent="new_search")
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(ctx, phase="input")
    assert exc.value.code == "invalid_intent"


def test_resolved_phase_requires_implicit_whitelist_and_confidence():
    ctx = ConversationContext(intent_source="implicit", intent="more_mentors",
                              intent_confidence=0.8)
    out = _validate_context(ctx, phase="resolved")
    assert out.intent == "more_mentors"
    with pytest.raises(ConversationValidationError):
        _validate_context(
            ConversationContext(intent_source="implicit", intent="more_mentors",
                                intent_confidence=1.5),
            phase="resolved",
        )


def test_session_turn_must_co_occur():
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(
            ConversationContext(session_id="s1", intent_source="explicit",
                                intent="new_search"),
            phase="input",
        )
    assert exc.value.code == "invalid_conversation_state"


def test_fork_pair_must_co_occur():
    with pytest.raises(ConversationValidationError) as exc:
        _validate_context(
            ConversationContext(main_session_id="m1", intent_source="explicit",
                                intent="new_search", session_id="s1", turn_id="t1"),
            phase="input",
        )
    assert exc.value.code == "invalid_conversation_state"


def test_prior_dedup_preserves_order():
    ctx = _assemble_context(
        session_id="s1", turn_id="t1", main_session_id=None, source_turn_id=None,
        anchor_entity_id=None, intent="new_search", intent_source="explicit",
        intent_confidence=None, prior_result_entity_ids=["e2", "e1", "e2", "e3"],
    )
    assert ctx.prior_result_entity_ids == ("e2", "e1", "e3")


def test_assemble_runs_input_validation():
    with pytest.raises(ConversationValidationError):
        _assemble_context(
            session_id="s1", turn_id="t1", main_session_id=None, source_turn_id=None,
            anchor_entity_id=None, intent="bogus", intent_source="explicit",
            intent_confidence=None, prior_result_entity_ids=(),
        )
