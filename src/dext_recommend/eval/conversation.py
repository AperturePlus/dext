"""Version-bound conversation-routing evaluation contracts for R5."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConversationEvalSample:
    query: str
    source: str
    expected_intent: str
    predicted_intent: str | None
    expected_kind: str
    actual_kind: str
    confidence: float | None
    context_has_prior: bool
    generation_profile_version: str
    build_id: str
    commit_hash: str

    def __post_init__(self) -> None:
        if self.source not in {"implicit", "explicit"}:
            raise ValueError("source must be implicit or explicit")
        for name in ("generation_profile_version", "build_id", "commit_hash"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True, slots=True)
class RoutingMetric:
    passed: int
    total: int
    rate: float


def implicit_conversation_routing_accuracy(samples) -> RoutingMetric:
    relevant = tuple(sample for sample in samples if sample.source == "implicit")
    passed = sum(
        sample.predicted_intent == sample.expected_intent
        and sample.actual_kind == sample.expected_kind
        for sample in relevant
    )
    return RoutingMetric(passed, len(relevant), passed / len(relevant) if relevant else 0.0)


def explicit_route_contract_pass_rate(samples) -> RoutingMetric:
    relevant = tuple(sample for sample in samples if sample.source == "explicit")
    passed = sum(
        sample.predicted_intent == sample.expected_intent
        and sample.actual_kind == sample.expected_kind
        for sample in relevant
    )
    return RoutingMetric(passed, len(relevant), passed / len(relevant) if relevant else 0.0)


__all__ = [
    "ConversationEvalSample", "RoutingMetric",
    "explicit_route_contract_pass_rate", "implicit_conversation_routing_accuracy",
]
