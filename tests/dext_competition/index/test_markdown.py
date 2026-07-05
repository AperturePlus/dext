"""C1 markdown parser tests — heading/table/link/block extraction.

The parser produces a sequence of blocks under a resolved heading_path. It
is line-oriented and deterministic for identical input.
"""
from __future__ import annotations

from dext_competition.index.markdown import (
    Block,
    HeadingBlock,
    Link,
    TableBlock,
    TextBlock,
    parse_markdown,
)


def test_parse_records_headings_with_level_and_path():
    md = "# 工学类\n\n## 一、核心赛事\n\n正文\n"
    doc = parse_markdown("工学类竞赛规则.md", md)
    heads = [b for b in doc.blocks if isinstance(b, HeadingBlock)]
    assert [(h.level, h.title) for h in heads] == [
        (1, "工学类"),
        (2, "一、核心赛事"),
    ]


def test_parse_resolves_heading_path_for_blocks():
    md = "# 工学类\n\n## 一、核心赛事\n\n正文段\n"
    doc = parse_markdown("工学类竞赛规则.md", md)
    text = [b for b in doc.blocks if isinstance(b, TextBlock)]
    assert len(text) == 1
    assert text[0].heading_path == "工学类竞赛规则.md > 工学类 > 一、核心赛事"


def test_parse_heading_path_only_includes_ancestors():
    md = "# A\n\n## B\n\n### C\n\nbody\n"
    doc = parse_markdown("f.md", md)
    body = [b for b in doc.blocks if isinstance(b, TextBlock)][0]
    # The body under C: path includes A, B, C.
    assert body.heading_path == "f.md > A > B > C"


def test_parse_heading_path_pops_on_same_or_lower_level():
    md = "# A\n\n## B\n\nb1\n\n## C\n\nc1\n"
    doc = parse_markdown("f.md", md)
    texts = [b for b in doc.blocks if isinstance(b, TextBlock)]
    assert texts[0].heading_path == "f.md > A > B"
    assert texts[1].heading_path == "f.md > A > C"


def test_parse_table_block_captures_rows():
    md = (
        "# T\n\n"
        "|赛事|方向|\n"
        "|---|---|\n"
        "|机械|机械设计|\n"
        "|结构|土木|\n"
    )
    doc = parse_markdown("f.md", md)
    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1
    assert tables[0].header == ("赛事", "方向")
    assert tables[0].rows == (("机械", "机械设计"), ("结构", "土木"))


def test_parse_table_links_extracted_to_block_source_links():
    md = (
        "# T\n\n"
        "|赛事|入口|\n"
        "|---|---|\n"
        "|机械|[官网](http://umic.ckcest.cn/)|\n"
    )
    doc = parse_markdown("f.md", md)
    table = [b for b in doc.blocks if isinstance(b, TableBlock)][0]
    assert table.source_links == ("http://umic.ckcest.cn/",)


def test_parse_inline_links_collected_from_text_blocks():
    md = "# T\n\n参见[高校竞赛全景索引](竞赛信息总览.md)与[官网](http://example.com)\n"
    doc = parse_markdown("f.md", md)
    text = [b for b in doc.blocks if isinstance(b, TextBlock)][0]
    assert "http://example.com" in text.source_links
    # Internal .md links are NOT official_url source_links (only http(s)).
    assert all(not s.endswith(".md") for s in text.source_links)


def test_parse_blockquote_is_text_block():
    md = "# T\n\n> 最近核验：2026-06-30。注意逐届调整。\n"
    doc = parse_markdown("f.md", md)
    texts = [b for b in doc.blocks if isinstance(b, TextBlock)]
    assert any("最近核验" in t.text for t in texts)


def test_parse_list_items_form_text_block():
    md = "# T\n\n- 第一项\n- 第二项\n- 第三项\n"
    doc = parse_markdown("f.md", md)
    texts = [b for b in doc.blocks if isinstance(b, TextBlock)]
    assert len(texts) == 1
    assert "第一项" in texts[0].text
    assert "第三项" in texts[0].text


def test_parse_consecutive_blank_lines_do_not_create_empty_blocks():
    md = "# T\n\n\n\n正文\n"
    doc = parse_markdown("f.md", md)
    texts = [b for b in doc.blocks if isinstance(b, TextBlock)]
    assert len(texts) == 1
    assert texts[0].text.strip() == "正文"


def test_parse_block_carry_heading_path_after_table():
    md = "# T\n\n## A\n\n|a|b|\n|---|---|\n|1|2|\n\n尾部段落\n"
    doc = parse_markdown("f.md", md)
    blocks = doc.blocks
    # trailing paragraph still under A
    tail = [b for b in blocks if isinstance(b, TextBlock)][0]
    assert tail.heading_path == "f.md > T > A"


def test_parse_is_deterministic_for_identical_input():
    md = "# T\n\n正文一\n\n## S\n\n|a|b|\n|---|---|\n|1|2|\n"
    a = parse_markdown("f.md", md)
    b = parse_markdown("f.md", md)
    assert type(a.blocks) is type(b.blocks)


def _serialize(blocks):
    out = []
    for blk in blocks:
        if isinstance(blk, HeadingBlock):
            out.append(("H", blk.level, blk.title, blk.heading_path))
        elif isinstance(blk, TextBlock):
            out.append(("T", blk.text, blk.source_links, blk.heading_path))
        elif isinstance(blk, TableBlock):
            out.append(("TB", blk.header, blk.rows, blk.source_links,
                        blk.heading_path))
    return out


def test_parse_block_sequence_stable_for_identical_input():
    md = "# T\n\n正文一\n\n## S\n\n|a|b|\n|---|---|\n|1|2|\n"
    a = parse_markdown("f.md", md)
    b = parse_markdown("f.md", md)
    assert _serialize(a.blocks) == _serialize(b.blocks)
