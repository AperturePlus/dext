# tests/test_grounded_content.py
from __future__ import annotations

from dext_grounded import ContentClass


def test_content_class_members():
    assert ContentClass.FACT.value == "fact"
    assert ContentClass.ADVICE.value == "advice"
    assert ContentClass.UNCERTAIN.value == "uncertain"


def test_content_class_is_exhaustive_for_spec():
    # spec §2.3: exactly fact | advice | uncertain
    members = {m.value for m in ContentClass}
    assert members == {"fact", "advice", "uncertain"}
