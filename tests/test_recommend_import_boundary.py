# tests/test_recommend_import_boundary.py
"""dext_recommend must not import dext / dext_graph / dext_monitor / dext_competition."""
from __future__ import annotations

import importlib
import sys


def test_dext_recommend_is_importable():
    mod = importlib.import_module("dext_recommend")
    assert mod.__name__ == "dext_recommend"


def test_dext_recommend_core_submodules_importable():
    # the placeholder orchestrator lives in core.service and is re-exported
    # by core; both submodule paths must import cleanly
    core_pkg = importlib.import_module("dext_recommend.core")
    svc = importlib.import_module("dext_recommend.core.service")
    assert hasattr(core_pkg, "RecommendationCore")
    assert hasattr(core_pkg, "RecommendDeps")
    assert hasattr(svc, "RecommendationCore")
    assert hasattr(svc, "RecommendDeps")


def test_dext_recommend_all_submodules_importable():
    # final cumulative boundary check: every submodule must import cleanly
    # without pulling in the forbidden dext family
    submodules = [
        "dext_recommend.config",
        "dext_recommend.errors",
        "dext_recommend.readiness",
        "dext_recommend.models",
        "dext_recommend.ports.build_snapshot",
        "dext_recommend.ports.embedding",
        "dext_recommend.ports.vector_search",
        "dext_recommend.ports.professor_facts",
        "dext_recommend.ports.generation",
        "dext_recommend.ports._fakes",
        "dext_recommend.core.service",
    ]
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        for sub in submodules:
            importlib.import_module(sub)
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules, (
                f"dext_recommend submodule must not import peer module {forbidden!r}"
            )
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_dext_recommend_does_not_import_dext_family():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        importlib.import_module("dext_recommend")
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules, (
                f"dext_recommend must not import peer module {forbidden!r}"
            )
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_dext_recommend_imports_grounded_contract():
    saved = dict(sys.modules)
    try:
        # it MAY import dext_grounded (shared contract)
        importlib.import_module("dext_recommend")
        assert "dext_grounded" in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)
