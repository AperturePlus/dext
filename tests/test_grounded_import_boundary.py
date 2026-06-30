"""dext_grounded must be importable and must NOT import dext / dext_graph / dext_monitor."""
from __future__ import annotations

import importlib
import sys


def test_dext_grounded_is_importable():
    mod = importlib.import_module("dext_grounded")
    assert mod is not None
    assert mod.__name__ == "dext_grounded"


def test_dext_grounded_does_not_import_dext_family():
    # import dext_grounded fresh, then check no dext* peer leaked in.
    for name in list(sys.modules):
        if name in ("dext", "dext_graph", "dext_monitor", "dext_recommend", "dext_competition"):
            del sys.modules[name]
    importlib.import_module("dext_grounded")
    for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_recommend", "dext_competition"):
        assert forbidden not in sys.modules, (
            f"dext_grounded must not import peer module {forbidden!r}"
        )


def test_dext_grounded_submodules_do_not_import_dext_family():
    # importing the package then its submodules must not pull in any dext* peer.
    for name in list(sys.modules):
        if name in ("dext", "dext_graph", "dext_monitor", "dext_recommend", "dext_competition"):
            del sys.modules[name]
    importlib.import_module("dext_grounded")
    importlib.import_module("dext_grounded.content")
    importlib.import_module("dext_grounded.source_ref")
    importlib.import_module("dext_grounded.student_context")
    for forbidden in ("dext", "dext_graph", "dext_monitor", "dext_recommend", "dext_competition"):
        assert forbidden not in sys.modules, (
            f"dext_grounded submodules must not import peer module {forbidden!r}"
        )
