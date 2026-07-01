from dext_recommend.adapters._sampling import deterministic_sample_ids


def test_empty_input_returns_empty():
    assert deterministic_sample_ids((), 50) == ()
    assert deterministic_sample_ids(("e1",), 0) == ()


def test_deterministic_and_stable():
    ids = [f"e{i}" for i in range(200)]
    a = deterministic_sample_ids(ids, 50)
    b = deterministic_sample_ids(ids, 50)
    assert a == b
    assert len(a) == 50
    # subset of input, no fabrication
    assert set(a).issubset(ids)


def test_order_independent_of_input_order():
    ids = ["e3", "e1", "e2"]
    assert deterministic_sample_ids(ids, 3) == deterministic_sample_ids(sorted(ids), 3)


def test_k_larger_than_input_returns_all_sorted_by_hash():
    ids = ["e1", "e2", "e3"]
    out = deterministic_sample_ids(ids, 50)
    assert len(out) == 3
    assert set(out) == {"e1", "e2", "e3"}
