import sqlite3
from pathlib import Path

import pytest

from dext_graph.models import ValueValidationError
from dext_graph.source import inspect_source, iter_professor_batches


def _source_db(path: Path, *, status: str = "completed") -> Path:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE university_meta (
          id INTEGER PRIMARY KEY, name TEXT, abbr TEXT, crawl_status TEXT
        );
        CREATE TABLE professors (
          id INTEGER PRIMARY KEY, name TEXT, org_unit_name TEXT, title TEXT,
          research_areas TEXT, publications TEXT, bio TEXT
        );
        CREATE TABLE org_units (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE professor_affiliations (
          professor_id INTEGER, org_unit_id INTEGER
        );
        """
    )
    connection.execute(
        "INSERT INTO university_meta VALUES (1, '测试大学', 'test', ?)", (status,)
    )
    connection.executemany(
        "INSERT INTO org_units VALUES (?, ?)", [(1, "计算机学院"), (2, "人工智能学院")]
    )
    connection.executemany(
        "INSERT INTO professors VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "张三", "计算机学院", "教授", "图学习", "论文 A", "简介 A"),
            (2, "李四", None, None, None, None, None),
        ],
    )
    connection.executemany(
        "INSERT INTO professor_affiliations VALUES (?, ?)", [(1, 1), (1, 2)]
    )
    connection.commit()
    connection.close()
    return path


def test_source_inspection_and_stable_rows(tmp_path):
    path = _source_db(tmp_path / "school.db")
    info = inspect_source(path)
    assert info.university == "测试大学"
    assert info.row_counts["professors"] == 2
    assert info.coverage["with_semantic_content"] == 1
    assert info.coverage["without_semantic_content"] == 1

    batches = list(iter_professor_batches(path, info, batch_size=1))
    first = batches[0][0]
    assert first.source_row_key == f"{info.sha256}:professors:1"
    assert first.org_units == ("人工智能学院", "计算机学院")
    assert first.point_id == list(iter_professor_batches(path, info, batch_size=2))[0][0].point_id
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM professors").fetchone()[0] == 2


def test_source_records_incomplete_crawl_but_explicit_selection_accepts_it(tmp_path):
    path = _source_db(tmp_path / "failed.db", status="failed")
    info = inspect_source(path)
    assert info.crawl_status == "failed"
    with pytest.raises(ValueValidationError, match="crawl_status='failed'"):
        inspect_source(path, require_completed=True)


def test_source_rejects_nonempty_wal(tmp_path):
    path = _source_db(tmp_path / "wal.db")
    Path(str(path) + "-wal").write_bytes(b"not checkpointed")
    with pytest.raises(ValueValidationError, match="non-empty WAL"):
        inspect_source(path)
