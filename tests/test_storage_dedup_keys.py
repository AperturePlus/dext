from dext.storage.dedup import SaveResult, looks_like_academician, name_key, node_key_for
from dext.storage.models import NodeType


def test_node_key_org_unit_by_id():
    assert node_key_for(NodeType.org_unit, org_unit_id=7) == "org_unit:id:7"


def test_node_key_org_unit_by_name_when_no_id():
    assert node_key_for(NodeType.org_unit, normalized_name="数学学院") == "org_unit:name:数学学院"


def test_node_key_url_node_is_canonical_url_only():
    key = node_key_for(NodeType.detail_url, normalized_url="https://x/p1", org_unit_id=7)
    assert key == "url:https://x/p1"


def test_node_key_url_node_without_org_unit_is_same_shape():
    key = node_key_for(NodeType.org_listing_url, normalized_url="https://x/list")
    assert key == "url:https://x/list"


def test_node_key_is_org_independent_for_url_nodes():
    # Regression guard for the node_key scheme change (commit 210a66a): URL nodes
    # must key on the canonical URL ONLY, so the same page discovered under
    # different org_units collapses to one node instead of being re-extracted.
    k_org_a = node_key_for(NodeType.detail_url, normalized_url="https://x/p1", org_unit_id=24)
    k_org_b = node_key_for(NodeType.detail_url, normalized_url="https://x/p1", org_unit_id=33)
    k_none = node_key_for(NodeType.detail_url, normalized_url="https://x/p1")
    assert k_org_a == k_org_b == k_none == "url:https://x/p1"


def test_node_key_has_no_legacy_type_or_org_prefix():
    # The legacy format "<type>:org:<id>:url:<url>" must NEVER be produced again —
    # it caused cross-version duplicate nodes (each re-discovery created a new node
    # because the new key never matched the legacy key).
    key = node_key_for(NodeType.detail_url, normalized_url="https://x/p1", org_unit_id=10)
    assert not key.startswith("detail_url:")
    assert ":org:" not in key



def test_form_pagination_pages_get_distinct_keys():
    # Different synthetic/identity URLs (per page) → different node_keys.
    k1 = node_key_for(NodeType.pagination_url, normalized_url="https://x?__ycl_page=1", org_unit_id=3)
    k2 = node_key_for(NodeType.pagination_url, normalized_url="https://x?__ycl_page=2", org_unit_id=3)
    assert k1 != k2


def test_node_key_missing_inputs_raise():
    import pytest

    with pytest.raises(ValueError):
        node_key_for(NodeType.org_unit)  # neither id nor name
    with pytest.raises(ValueError):
        node_key_for(NodeType.detail_url)  # no normalized_url


def test_name_key_strips_and_casefolds():
    assert name_key("  John Smith  ") == "john smith"


def test_name_key_fullwidth_to_halfwidth():
    # full-width ＡＢ and full-width space → half-width
    assert name_key("ＡＢＣ") == "abc"


def test_name_key_drops_decorative_dots():
    assert name_key("买买提·阿不都") == name_key("买买提阿不都")


def test_looks_like_academician():
    assert looks_like_academician("中国科学院院士")
    assert looks_like_academician("教授 中国工程院院士")
    assert not looks_like_academician("教授")
    assert not looks_like_academician(None)


def test_save_result_defaults_zero():
    r = SaveResult()
    assert (r.inserted, r.updated, r.affiliations_added, r.academicians, r.save_errors) == (0, 0, 0, 0, 0)
