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
        "dext_recommend.ports.active_snapshot",
        "dext_recommend.ports.release_readback",
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


def test_dext_recommend_adapters_do_not_import_dext_graph():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        importlib.import_module("dext_recommend.adapters")
        importlib.import_module("dext_recommend.adapters._catalog_reader")
        importlib.import_module("dext_recommend.adapters._vector_reader")
        importlib.import_module("dext_recommend.adapters._graph_reader")
        importlib.import_module("dext_recommend.adapters.catalog_release")
        importlib.import_module("dext_recommend.adapters.vector_release")
        importlib.import_module("dext_recommend.adapters.graph_release")
        importlib.import_module("dext_recommend.adapters.ranking_profile")
        assert "dext_graph" not in sys.modules, (
            "dext_recommend.adapters must not import dext_graph"
        )
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_dext_recommend_core_submodules_importable_without_dext_family():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
                del sys.modules[name]
        submodules = [
            "dext_recommend.core.ranking_profile",
            "dext_recommend.core.intent",
            "dext_recommend.core.query_understanding",
            "dext_recommend.core._schemas",
            "dext_recommend.core.filters",
            "dext_recommend.core.recall",
            "dext_recommend.core.detail_fetch",
            "dext_recommend.core.rerank",
            "dext_recommend.core.explanation",
            "dext_recommend.core.cards",
            "dext_recommend.core.validation",
            "dext_recommend.core.service",
        ]
        for sub in submodules:
            importlib.import_module(sub)
        for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_competition"):
            assert forbidden not in sys.modules
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_core_does_not_import_api_or_adapters():
    saved = dict(sys.modules)
    try:
        for name in list(sys.modules):
            if name.startswith("dext_recommend.api") or name.startswith("dext_recommend.adapters"):
                del sys.modules[name]
        importlib.import_module("dext_recommend.core.service")
        importlib.import_module("dext_recommend.core.recall")
        importlib.import_module("dext_recommend.core.rerank")
        for name in sys.modules:
            assert not name.startswith("dext_recommend.api"), f"core imported {name}"
            assert not name.startswith("dext_recommend.adapters"), f"core imported {name}"
    finally:
        sys.modules.clear()
        sys.modules.update(saved)


def test_core_does_not_reference_raw_dense_sparse_scores():
    import re
    from pathlib import Path
    core_dir = Path(__file__).resolve().parents[2] / "src" / "dext_recommend" / "core"
    forbidden = re.compile(r"\b(dense_score|sparse_score|raw_score)\b")
    offenders = []
    for py in core_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        # comments are allowed to mention these; only fail on real references
        for line in text.splitlines():
            stripped = line.split("#", 1)[0]
            if forbidden.search(stripped):
                offenders.append(f"{py.name}: {line.strip()}")
    assert not offenders, f"core references raw dense/sparse scores: {offenders}"
