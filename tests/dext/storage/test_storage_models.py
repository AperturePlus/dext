from dext.storage.models import Base


def _table(name):
    return Base.metadata.tables[name]


def test_all_expected_tables_present():
    assert set(Base.metadata.tables) == {
        "university_meta", "crawl_runs", "org_units", "crawl_graph_nodes",
        "crawl_graph_edges", "crawl_page_cache", "crawl_extraction_attempts",
        "crawl_extraction_failures", "professors", "academicians",
        "professor_affiliations",
    }


def test_graph_nodes_columns():
    cols = set(_table("crawl_graph_nodes").columns.keys())
    assert {
        "id", "node_key", "type", "url", "org_unit_id", "org_unit_name",
        "status", "priority_score", "base_priority", "confidence", "depth",
        "attempt_count", "max_attempts", "last_error", "run_id", "claimed_at",
        "completed_at", "next_retry_at", "content_hash", "metadata_json",
        "created_at", "updated_at",
    } <= cols


def test_node_key_is_unique():
    assert _table("crawl_graph_nodes").columns["node_key"].unique is True


def test_claim_index_on_status_priority():
    idx_cols = {tuple(c.name for c in i.columns) for i in _table("crawl_graph_nodes").indexes}
    assert ("status", "priority_score") in idx_cols


def test_org_units_unique_name_and_url():
    t = _table("org_units")
    assert t.columns["name"].unique is True
    assert t.columns["url"].unique is True


def test_page_cache_primary_key_is_url():
    pk = [c.name for c in _table("crawl_page_cache").primary_key.columns]
    assert pk == ["url"]


def test_edges_unique_triple():
    from sqlalchemy import UniqueConstraint

    t = _table("crawl_graph_edges")
    uniques = {
        tuple(c.name for c in con.columns)
        for con in t.constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("from_node_id", "to_node_id", "edge_type") in uniques


def test_affiliation_and_academician_uniques():
    from sqlalchemy import UniqueConstraint

    aff = {
        tuple(c.name for c in con.columns)
        for con in Base.metadata.tables["professor_affiliations"].constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("professor_id", "org_unit_id") in aff

    aca = {
        tuple(c.name for c in con.columns)
        for con in Base.metadata.tables["academicians"].constraints
        if isinstance(con, UniqueConstraint)
    }
    assert ("name", "org_unit_id") in aca
    assert ("org_unit_id", "name_key") in aca


def test_professors_have_no_name_key_column():
    # By design (spec §3.9): professor dedup is code-only, no name_key hard key.
    assert "name_key" not in _table("professors").columns.keys()
