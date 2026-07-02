from dext_recommend.eval.conversation import (
    ConversationEvalSample, explicit_route_contract_pass_rate,
    implicit_conversation_routing_accuracy,
)


def _sample(**overrides):
    values = dict(
        query="more", source="implicit", expected_intent="more_mentors",
        predicted_intent="more_mentors", expected_kind="recommendation",
        actual_kind="recommendation", confidence=0.9, context_has_prior=True,
        generation_profile_version="gp1", build_id="b1", commit_hash="abc",
    )
    values.update(overrides)
    return ConversationEvalSample(**values)


def test_implicit_accuracy_checks_intent_and_dispatch_kind():
    metric = implicit_conversation_routing_accuracy([
        _sample(), _sample(predicted_intent="new_search"),
    ])
    assert (metric.passed, metric.total, metric.rate) == (1, 2, 0.5)


def test_explicit_contract_rate():
    metric = explicit_route_contract_pass_rate([
        _sample(source="explicit", confidence=None),
    ])
    assert metric.rate == 1.0
