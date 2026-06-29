import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("psutil")
pytest.importorskip("zstandard")

from test_catalog_workflow import _settings, _source_db


@pytest.mark.parametrize(
    ("kill_variable", "expected_code"),
    [
        ("DEXT_TEST_KILL_AFTER_BACKUP_CHUNKS", 91),
        ("DEXT_TEST_KILL_AFTER_INGEST_COMMITS", 92),
        ("DEXT_TEST_KILL_AFTER_CURATION_IDENTITY_BATCHES", 93),
        ("DEXT_TEST_KILL_AFTER_CURATION_FIELD_BATCHES", 94),
        ("DEXT_TEST_KILL_AFTER_CURATION_CANONICAL_BATCHES", 95),
        ("DEXT_TEST_KILL_AFTER_EVIDENCE_BATCHES", 97),
        ("DEXT_TEST_KILL_AFTER_EXPORT_BATCHES", 98),
    ],
)
def test_subprocess_kill_and_resume_converges(
    tmp_path, kill_variable, expected_code
):
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=3)
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(root / "src"),
            "DEXT_CATALOG_PATH": str(settings.catalog_path),
            "DEXT_SOURCE_DATA_DIR": str(settings.source_data_dir),
            "DEXT_SEED_PATH": str(settings.seed_path),
            "DEXT_BUILD_READ_BATCH": "1",
            "DEXT_BUILD_WRITE_QUEUE": "2",
            "DEXT_BUILD_MAX_RSS_MB": "2048",
            "DEXT_TEST_SKIP_NEO4J": "1",
            kill_variable: "1",
        }
    )
    killed = subprocess.run(
        [sys.executable, "-m", "dext_graph", "graph", "build", "--university", "测试大学"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert killed.returncode == expected_code
    with sqlite3.connect(settings.catalog_path) as connection:
        build_id = connection.execute("SELECT id FROM graph_builds").fetchone()[0]
    env.pop(kill_variable)
    resumed = subprocess.run(
        [sys.executable, "-m", "dext_graph", "graph", "resume", build_id],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    output = json.loads(resumed.stdout)
    assert output["build"]["status"] == "WRITING_VECTOR"
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM professor_observations"
        ).fetchone()[0] == 3
