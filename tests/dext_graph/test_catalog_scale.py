import math
import os
import sqlite3

import pytest

psutil = pytest.importorskip("psutil")

from dext_graph.catalog.workflow import create_build
from test_catalog_workflow import _settings


@pytest.mark.slow
@pytest.mark.skipif(
    os.getenv("DEXT_RUN_SLOW_TESTS") != "1",
    reason="set DEXT_RUN_SLOW_TESTS=1 for the one-million-observation RSS gate",
)
async def test_one_million_observations_stay_below_configured_rss(tmp_path, monkeypatch):
    monkeypatch.setenv("DEXT_TEST_SKIP_NEO4J", "1")
    monkeypatch.setenv("DEXT_TEST_STOP_AFTER_INGEST", "1")
    baseline_mb = math.ceil(psutil.Process().memory_info().rss / 1024 / 1024)
    settings = _settings(tmp_path, batch=100)
    settings = settings.model_copy(update={"build_max_rss_mb": baseline_mb + 256})
    source = settings.source_data_dir / "test.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE university_meta(name TEXT, abbr TEXT, crawl_status TEXT);
            CREATE TABLE professors(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO university_meta VALUES ('测试大学', 'test', 'failed');
            """
        )
        connection.executemany(
            "INSERT INTO professors(id, name) VALUES (?, ?)",
            ((index, f"教师{index}") for index in range(1, 1_000_001)),
        )
    result = await create_build(["测试大学"], settings)
    assert result["build"]["status"] == "CURATING"
    assert result["build"]["summary_json"]["observations_written"] == 1_000_000
    assert result["build"]["summary_json"]["peak_observed_rss_bytes"] <= (
        settings.build_max_rss_mb * 1024 * 1024
    )
