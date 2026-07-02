from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids


def test_dedupe_entity_ids_preserves_order():
    assert dedupe_entity_ids(["e2", "e1", "e2", "e3", "e1"]) == ("e2", "e1", "e3")


def test_dedupe_entity_ids_empty():
    assert dedupe_entity_ids([]) == ()
    assert dedupe_entity_ids(None) == ()


def test_chunk_entity_ids_below_limit():
    assert chunk_entity_ids(["e1", "e2"], 999) == (("e1", "e2"),)


def test_chunk_entity_ids_splits_at_limit():
    ids = [f"e{i}" for i in range(5)]
    chunks = chunk_entity_ids(ids, 2)
    assert chunks == (("e0", "e1"), ("e2", "e3"), ("e4",))


def test_chunk_entity_ids_empty():
    assert chunk_entity_ids([], 999) == ()


def test_chunk_entity_ids_limit_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        chunk_entity_ids(["e1"], 0)
