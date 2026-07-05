"""G0 import-boundary tests for dext_competition.

Mirrors tests/dext_recommend/test_recommend_import_boundary.py.

Invariants pinned here (competition parallel plan §2):
- dext_competition imports cleanly and re-exports its public contract surface.
- It MAY import dext_grounded (shared constrained-generation + SourceRef).
- It MUST NOT import dext / dext_graph / dext_monitor / dext_recommend.
- The shared ConstrainedGenerationPipeline is the only generation seam; the
  competition package re-exports it from dext_grounded, never redefines it.
"""
from __future__ import annotations

import importlib
import sys

_FORBIDDEN = ("dext", "dext_graph", "dext_monitor", "dext_recommend")


def _scrub_forbidden() -> None:
    for name in list(sys.modules):
        if name in _FORBIDDEN or name.startswith("dext."):
            del sys.modules[name]
    for name in list(sys.modules):
        if name in _FORBIDDEN:
            del sys.modules[name]


def test_dext_competition_is_importable():
    mod = importlib.import_module("dext_competition")
    assert mod.__name__ == "dext_competition"


def test_dext_competition_imports_grounded_contract():
    saved = dict(sys.modules)
    try:
        importlib.import_module("dext_competition")
        assert "dext_grounded" in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_dext_competition_does_not_import_dext_family():
    saved = dict(sys.modules)
    try:
        _scrub_forbidden()
        importlib.import_module("dext_competition")
        for forbidden in _FORBIDDEN:
            assert forbidden not in sys.modules, (
                f"dext_competition must not import peer module {forbidden!r}"
            )
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_contracts_and_ports_submodules_importable_without_dext_family():
    saved = dict(sys.modules)
    try:
        _scrub_forbidden()
        submodules = [
            "dext_competition",
            "dext_competition.config",
            "dext_competition.errors",
            "dext_competition.contracts",
            "dext_competition.contracts.knowledge",
            "dext_competition.contracts.catalog",
            "dext_competition.contracts.recommend",
            "dext_competition.contracts.qa",
            "dext_competition.contracts.plan",
            "dext_competition.contracts.assistant",
            "dext_competition.ports",
            "dext_competition.ports.knowledge",
            "dext_competition.ports.catalog",
            "dext_competition.ports.generation_profile",
            "dext_competition.ports._fakes",
            "dext_competition.recommend",
            "dext_competition.recommend.profile",
            "dext_competition.recommend.query_understanding",
            "dext_competition.recommend.recall",
            "dext_competition.recommend.filters",
            "dext_competition.recommend.ranking",
            "dext_competition.recommend.explanation",
            "dext_competition.recommend.service",
            "dext_competition.assistant",
            "dext_competition.assistant.change_cards",
            "dext_competition.assistant.profile",
            "dext_competition.assistant.schemas",
            "dext_competition.assistant.service",
            "dext_competition.assistant.validation",
            "dext_competition.eval",
            "dext_competition.eval.recommend",
        ]
        for sub in submodules:
            importlib.import_module(sub)
        for forbidden in _FORBIDDEN:
            assert forbidden not in sys.modules, (
                f"dext_competition submodule must not import {forbidden!r}"
            )
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_competition_reexports_shared_pipeline_not_redefine():
    # The shared seam is owned by dext_grounded; competition must re-export the
    # same object, never a local subclass/redefinition.
    import dext_competition as dc
    import dext_grounded as dg
    assert dc.ConstrainedGenerationPipeline is dg.ConstrainedGenerationPipeline
    assert dc.LLMGenerationPort is dg.LLMGenerationPort


def test_competition_reexports_sourcereffrom_grounded():
    import dext_competition as dc
    import dext_grounded as dg
    assert dc.SourceRef is dg.SourceRef
    assert dc.FactBundle is dg.FactBundle
