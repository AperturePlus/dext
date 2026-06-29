import json

import pytest

from dext_graph.catalog.graph_gold import _catalog_metrics, generate_graph_gold
from dext_graph.catalog.workflow import create_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


@pytest.mark.asyncio
async def test_graph_gold_is_snapshot_bound_and_evidence_backed(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_SKIP_NEO4J", "1")
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=3)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]

    generated = generate_graph_gold(
        settings.catalog_path,
        build_id,
        size=3,
        output_root=tmp_path / "gold",
    )
    assert generated["rows"] == 3
    rows = [json.loads(line) for line in open(generated["dataset"], encoding="utf-8")]
    assert all(row["source_snapshot_hash"] for row in rows)
    assert all(row["expected_facts"] for row in rows)
    metrics = _catalog_metrics(settings.catalog_path, build_id, rows)
    assert metrics["fact_precision"] == 1.0
    assert metrics["fact_recall"] == 1.0
    assert metrics["provenance_traceability"] == 1.0
