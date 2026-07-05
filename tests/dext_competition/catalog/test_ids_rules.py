from __future__ import annotations

import pytest

from dext_competition.catalog.ids import (
    build_alias_index,
    competition_id,
    normalize_competition_name,
)
from dext_competition.catalog.rules import load_catalog_rules


def test_name_normalization_and_uuidv5_are_stable():
    assert normalize_competition_name("  ＩＣＰＣ   Contest  ") == "icpc contest"
    assert competition_id("  ＩＣＰＣ   Contest  ") == competition_id("icpc contest")
    assert competition_id("ICPC Contest") != competition_id("ICPC Regional")
    assert competition_id("ICPC Contest").startswith("cmp_")
    assert len(competition_id("ICPC Contest")) == 36


def test_alias_index_is_exact_and_rejects_ambiguity():
    index = build_alias_index(("Alpha",), {"Alpha": ("A",)})
    assert index[normalize_competition_name("A")] == "Alpha"
    with pytest.raises(ValueError, match="ambiguous"):
        build_alias_index(("Alpha", "Beta"), {"Alpha": ("same",), "Beta": ("same",)})


def test_catalog_rules_resource_is_versioned():
    rules = load_catalog_rules()
    assert rules.version == "catalog-rules-v1"
    assert rules.identity_prefix == "dext/competition/v1/"
    assert "schedule" in rules.freshness_sensitive_fields
    assert len(rules.enrichment_docs) == 11
