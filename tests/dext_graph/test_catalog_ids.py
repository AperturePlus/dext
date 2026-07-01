import uuid

from dext_graph.catalog.ids import (
    canonical_json,
    canonical_source_url,
    name_key,
    observation_id,
    uuid7,
)


def test_uuid7_and_normalization_contracts():
    allocated = uuid.UUID(uuid7())
    assert allocated.version == 7
    assert allocated.variant == uuid.RFC_4122
    assert name_key("  阿·里Ａ  ") == "阿里a"
    assert canonical_source_url(
        "HTTPS://Example.EDU.CN:443/a/?z=2&x=&z=1#bio"
    ) == "https://example.edu.cn/a?x=&z=1&z=2"
    assert canonical_source_url("about:blank") is None


def test_canonical_json_and_observation_id_are_stable():
    first = canonical_json({"中文": "值", "b": 2, "a": 1})
    second = canonical_json({"a": 1, "b": 2, "中文": "值"})
    assert first == second
    assert observation_id("univ:test", "https://x/", "张三", "row") == observation_id(
        "univ:test", "https://x/", "张三", "row"
    )
