"""C1 chunker tests — chunk boundaries + canonical text + chunk_hash.

A chunk is the leaf section: all blocks under the deepest current heading.
Every chunk in a doc carries ``last_verified`` parsed from the leading
blockquote (``最近核验：YYYY-MM-DD``).
"""
from __future__ import annotations

import hashlib

from dext_competition import Chunk
from dext_competition.index.chunker import chunk_document, chunk_hash
from dext_competition.index.markdown import parse_markdown


def test_chunk_hash_is_sha256_of_canonical_text():
    text = "正文内容"
    h = chunk_hash(text)
    assert h == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_chunk_document_produces_chunks_with_all_fields():
    md = "# 工学类\n\n## 一、核心赛事\n\n机械创新作品说明\n"
    doc = parse_markdown("工学类竞赛规则.md", md)
    chunks = chunk_document(doc)
    assert len(chunks) >= 1
    for c in chunks:
        assert isinstance(c, Chunk)
        assert c.doc_path == "工学类竞赛规则.md"
        assert c.heading_path
        assert c.chunk_hash
        assert c.text


def test_chunk_groups_blocks_under_same_leaf_heading():
    md = (
        "# A\n\n"
        "## B\n\n"
        "段落一\n\n"
        "段落二\n\n"
        "## C\n\n"
        "段落三\n"
    )
    doc = parse_markdown("f.md", md)
    chunks = chunk_document(doc)
    # Two leaf sections B and C (heading blocks themselves are metadata).
    texts = [c.text for c in chunks]
    assert any("段落一" in t and "段落二" in t for t in texts)
    assert any("段落三" in t for t in texts)
    assert all("段落一" not in c.text or "段落三" not in c.text for c in chunks)


def test_chunk_heading_path_records_leaf_section():
    md = "# A\n\n## B\n\n正文\n"
    doc = parse_markdown("f.md", md)
    chunks = chunk_document(doc)
    assert chunks[0].heading_path == "f.md > A > B"


def test_chunk_source_links_aggregate_from_blocks():
    md = (
        "# A\n\n"
        "## B\n\n"
        "参见[官网](http://example.com) 文字。\n\n"
        "|赛事|入口|\n|---|---|\n|机械|[官网](http://umic.ckcest.cn/)|\n"
    )
    doc = parse_markdown("f.md", md)
    chunks = chunk_document(doc)
    chunk = chunks[0]
    assert "http://example.com" in chunk.source_links
    assert "http://umic.ckcest.cn/" in chunk.source_links


def test_chunk_last_verified_parsed_from_leading_blockquote():
    md = (
        "# 工学类\n\n> 最近核验：2026-06-30。注意逐届调整。\n\n"
        "## 一\n\n正文\n\n## 二\n\n更多正文\n"
    )
    doc = parse_markdown("工学类竞赛规则.md", md)
    chunks = chunk_document(doc)
    assert len(chunks) == 3
    assert {chunk.last_verified for chunk in chunks} == {"2026-06-30"}


def test_chunk_last_verified_none_when_no_blockquote_date():
    md = "# A\n\n正文无核验日期\n"
    doc = parse_markdown("f.md", md)
    chunks = chunk_document(doc)
    assert chunks[0].last_verified is None


def test_chunk_hash_stable_for_identical_input():
    md = "# A\n\n## B\n\n正文一\n\n## C\n\n正文二\n"
    doc = parse_markdown("f.md", md)
    a = chunk_document(doc)
    b = chunk_document(doc)
    assert [c.chunk_hash for c in a] == [c.chunk_hash for c in b]


def test_chunk_canonical_text_excludes_heading_markup():
    md = "# A\n\n## B\n\n正文一\n"
    doc = parse_markdown("f.md", md)
    chunks = chunk_document(doc)
    chunk = chunks[0]
    # The chunk text is the body content, not the heading line.
    assert "正文一" in chunk.text
    assert "# B" not in chunk.text


def test_chunk_document_empty_doc_returns_empty():
    doc = parse_markdown("f.md", "")
    assert chunk_document(doc) == ()


def test_chunk_table_content_serialized_into_text():
    md = (
        "# A\n\n"
        "## B\n\n"
        "|赛事|方向|\n|---|---|\n|机械|机械设计|\n"
    )
    doc = parse_markdown("f.md", md)
    chunks = chunk_document(doc)
    assert len(chunks) == 1
    assert "机械" in chunks[0].text
    assert "机械设计" in chunks[0].text
