"""Unit tests for the eval contract (grounded-generation spec §9).

describe_metrics() returns metric-name -> definition-string dict.
load_acceptance_samples() returns a non-empty list of AcceptanceSample,
each binding generation_profile_version + grounded_rules_manifest_hash.
"""
from __future__ import annotations

import dataclasses

from dext_grounded.eval import (
    AcceptanceSample, describe_metrics, load_acceptance_samples,
)
from dext_grounded.rules import load_grounded_rules


# (a) describe_metrics has the two required keys ---------------------------

def test_describe_metrics_has_required_keys():
    metrics = describe_metrics()
    assert "grounded_precision" in metrics
    assert "no_probability_claim_rate" in metrics
    assert isinstance(metrics["grounded_precision"], str)
    assert isinstance(metrics["no_probability_claim_rate"], str)
    assert metrics["grounded_precision"]
    assert metrics["no_probability_claim_rate"]


# (b) load_acceptance_samples is non-empty ---------------------------------

def test_load_acceptance_samples_non_empty():
    samples = load_acceptance_samples()
    assert isinstance(samples, list)
    assert len(samples) >= 1


# (c) each sample binds both version fields non-empty ----------------------

def test_samples_bind_version_fields():
    rules_hash = load_grounded_rules().manifest_hash
    samples = load_acceptance_samples()
    for s in samples:
        assert s.generation_profile_version
        assert s.grounded_rules_manifest_hash
        assert s.grounded_rules_manifest_hash == rules_hash


# (d) AcceptanceSample is frozen -------------------------------------------

def test_acceptance_sample_is_frozen():
    samples = load_acceptance_samples()
    sample = samples[0]
    import pytest
    with pytest.raises((AttributeError, TypeError)):
        sample.generation_profile_version = "tampered"  # type: ignore[misc]


# (e) AcceptanceSample carries real sample shape ---------------------------

def test_acceptance_sample_carries_real_shape():
    samples = load_acceptance_samples()
    for s in samples:
        # must carry a query and an expected flag, not just version strings
        assert hasattr(s, "query")
        assert hasattr(s, "expected_no_probability_claim")
        assert s.query  # non-empty query
