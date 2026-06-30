from __future__ import annotations

import pytest

from dext_grounded import GenerationProfile, ProfileRegistry


def test_generation_profile_minimum():
    p = GenerationProfile(version="gen-v1.0")
    assert p.version == "gen-v1.0"
    assert p.prompt_ids == []
    assert p.trim_token_budget > 0


def test_generation_profile_empty_version_rejected():
    with pytest.raises(ValueError):
        GenerationProfile(version="")


def test_registry_register_and_get():
    reg = ProfileRegistry()
    p = GenerationProfile(version="gen-v1.0", prompt_ids=["match-analysis-v1"])
    reg.register(p)
    assert reg.get("gen-v1.0") is p


def test_registry_unknown_version_returns_none():
    reg = ProfileRegistry()
    assert reg.get("nope") is None
