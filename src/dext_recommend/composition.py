"""Composition seam for RecommendationCore.

R3c provides only explicit injection seams — assemble_core and build_test_core.
A production factory that constructs live adapters lands in R7 once all
required live adapters (snapshot/embedding/vector/facts/llm) exist and a
startup readiness check is in place. Do NOT add a fake production factory
that returns a Core whose methods raise NotImplementedError.
"""
from __future__ import annotations

from dext_recommend.config import RecommendSettings
from dext_recommend.core.service import RecommendDeps, RecommendationCore


def assemble_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return RecommendationCore(deps, settings)


def build_test_core(deps: RecommendDeps, settings: RecommendSettings) -> RecommendationCore:
    return assemble_core(deps, settings)


__all__ = ["assemble_core", "build_test_core"]
