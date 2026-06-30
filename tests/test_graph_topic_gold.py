import json

import pytest

from dext_graph.catalog.db import CatalogError
from dext_graph.catalog.topic_gold import evaluate_topic_gold, generate_topic_gold
from dext_graph.catalog.workflow import create_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


@pytest.mark.asyncio
async def test_topic_gold_template_requires_explicit_human_completion(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_SKIP_VECTOR", "1")
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=2)
    result = await create_build(["测试大学"], settings)
    generated = generate_topic_gold(
        settings.catalog_path,
        result["build"]["id"],
        size=2,
        output_root=tmp_path / "gold",
    )
    dataset = tmp_path / "gold" / f"{result['build']['id']}.jsonl"
    assert generated["rows"] == 2
    with pytest.raises(CatalogError, match="incomplete annotation"):
        evaluate_topic_gold(settings.catalog_path, result["build"]["id"], dataset)

    rows = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["annotated"] = True
        row["topics"] = []
        row["subtopic_of"] = []
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    evaluated = evaluate_topic_gold(
        settings.catalog_path, result["build"]["id"], dataset
    )
    assert evaluated["status"] == "evaluated"
    assert evaluated["topic_gate_passed"] is True
