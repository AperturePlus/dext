"""C1 scanner tests — deterministic scan of data/竞赛助手/*.md.

Deterministic path order is documented in scanner.py. Reads are UTF-8,
mojibake-free. Only ``.md`` files are picked up.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from dext_competition.index.scanner import (
    KnowledgeSourceReadError,
    MarkdownFile,
    scan_markdown_root,
)

CORPUS_ROOT = Path(__file__).resolve().parents[3] / "data" / "竞赛助手"


def _corpus_root() -> str:
    # Late-bound so the test skips cleanly if the corpus is absent in a
    # stripped-down checkout.
    return str(CORPUS_ROOT)


def test_scan_returns_markdown_files_only():
    files = scan_markdown_root(_corpus_root())
    assert all(isinstance(f, MarkdownFile) for f in files)
    assert all(f.path.endswith(".md") for f in files)


def test_scan_covers_exactly_18_markdown_files():
    files = scan_markdown_root(_corpus_root())
    assert len(files) == 18, [f.path for f in files]


def test_scan_is_deterministic_across_calls():
    a = scan_markdown_root(_corpus_root())
    b = scan_markdown_root(_corpus_root())
    assert [f.path for f in a] == [f.path for f in b]


def test_scan_paths_are_relative_to_root():
    files = scan_markdown_root(_corpus_root())
    for f in files:
        assert not os.path.isabs(f.path)
        assert "/" not in f.path or f.path.count("/") == 0  # flat dir
        assert f.path == os.path.basename(f.path)


def test_scan_reads_content_as_utf8_no_mojibake():
    files = scan_markdown_root(_corpus_root())
    # A known Chinese phrase from the corpus must survive intact.
    needle = "竞赛"
    found = any(needle in f.content for f in files)
    assert found, "UTF-8 read must preserve Chinese characters"


def test_scan_markdown_file_records_relative_path_and_content():
    files = scan_markdown_root(_corpus_root())
    readme = next(f for f in files if f.path == "README.md")
    assert readme.content.startswith("# 竞赛助手知识库")


def test_scan_excludes_non_markdown_files(tmp_path: Path):
    (tmp_path / "a.md").write_text("# a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("not md\n", encoding="utf-8")
    (tmp_path / "c.MD").write_text("# c\n", encoding="utf-8")  # case-sensitive
    files = scan_markdown_root(str(tmp_path))
    paths = sorted(f.path for f in files)
    # Only lowercase-suffixed .md by default; document the choice here.
    assert "a.md" in paths
    assert "b.txt" not in paths


def test_scan_raises_on_missing_root():
    with pytest.raises(KnowledgeSourceReadError) as captured:
        scan_markdown_root("/does/not/exist/xyz")
    assert captured.value.category == "source_root_missing"
    assert captured.value.path == "/does/not/exist/xyz"


def test_scan_classifies_invalid_utf8_with_relative_path(tmp_path: Path):
    (tmp_path / "bad.md").write_bytes(b"\xff\xfe")
    with pytest.raises(KnowledgeSourceReadError) as captured:
        scan_markdown_root(str(tmp_path))
    assert captured.value.category == "invalid_utf8"
    assert captured.value.path == "bad.md"


def test_scan_order_documented_utf8_codepoint_then_path():
    """Path order is sorted by UTF-8 codepoint of the basename (documented
    in scanner.py)."""
    files = scan_markdown_root(_corpus_root())
    paths = [f.path for f in files]
    assert paths == sorted(paths, key=lambda p: p.encode("utf-8"))
