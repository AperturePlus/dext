"""Line-oriented Markdown parser for C1 (spec §4).

Produces a flat, deterministic sequence of :class:`Block` items, each
carrying its resolved ``heading_path`` (``"<doc_path> > <H1> > <H2> ..."``)
and any ``http(s)`` links found inside it. The parser is intentionally
line-based and side-effect free; GFM tables, ATX headings (``#``),
blockquotes, bullet/ordered lists and paragraphs are recognised. Internal
``.md`` links are NOT treated as official source links (only ``http(s)``).

The chunker consumes the block stream and decides chunk boundaries.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "Block",
    "HeadingBlock",
    "Link",
    "ParsedDocument",
    "TableBlock",
    "TextBlock",
    "parse_markdown",
]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_URL_RE = re.compile(r"(https?://[^\s)]+)")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


@dataclass(frozen=True, slots=True)
class Link:
    text: str
    url: str


@dataclass(frozen=True, slots=True)
class Block:
    heading_path: str


@dataclass(frozen=True, slots=True)
class HeadingBlock(Block):
    level: int
    title: str


@dataclass(frozen=True, slots=True)
class TextBlock(Block):
    text: str
    source_links: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TableBlock(Block):
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    source_links: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    doc_path: str
    blocks: tuple[Block, ...] = field(default_factory=tuple)


def _extract_links(text: str) -> tuple[str, ...]:
    urls: list[str] = []
    for m in _LINK_RE.finditer(text):
        urls.append(m.group(2))
    for m in _URL_RE.finditer(text):
        url = m.group(1).rstrip(".,;:")
        if url not in urls:
            urls.append(url)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return tuple(out)


def _row_cells(body: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in body.split("|"))


def _heading_stack_str(doc_path: str, stack: list[tuple[int, str]]) -> str:
    return doc_path + "".join(f" > {t}" for _, t in stack)


def parse_markdown(doc_path: str, content: str) -> ParsedDocument:
    """Parse ``content`` into a :class:`ParsedDocument` of blocks.

    Deterministic: identical input yields identical block sequences.
    """
    lines = content.split("\n")
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []  # (level, title), ascending by level
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Skip blank lines.
        if not stripped:
            i += 1
            continue

        # Heading.
        hm = _HEADING_RE.match(line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            # Pop stack to ancestors with strictly lower level.
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            blocks.append(HeadingBlock(
                heading_path=_heading_stack_str(doc_path, stack),
                level=level, title=title,
            ))
            i += 1
            continue

        # Horizontal rule (skip — pure separator).
        if _HR_RE.match(line):
            i += 1
            continue

        # GFM table: a row line followed by a separator line.
        tr = _TABLE_ROW_RE.match(line)
        if tr and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header = _row_cells(tr.group(1))
            i += 2
            rows: list[tuple[str, ...]] = []
            while i < n:
                rm = _TABLE_ROW_RE.match(lines[i])
                if not rm:
                    break
                rows.append(_row_cells(rm.group(1)))
                i += 1
            joined = "|".join("|".join(r) for r in (header, *rows))
            blocks.append(TableBlock(
                heading_path=_heading_stack_str(doc_path, stack),
                header=header,
                rows=tuple(rows),
                source_links=_extract_links(joined),
            ))
            continue

        # Blockquote: collect consecutive '>' lines as one text block.
        if stripped.startswith(">"):
            buf: list[str] = []
            while i < n and lines[i].lstrip().startswith(">"):
                buf.append(lines[i].lstrip()[1:].strip())
                i += 1
            text = "\n".join(b for b in buf if b)
            if text:
                blocks.append(TextBlock(
                    heading_path=_heading_stack_str(doc_path, stack),
                    text=text,
                    source_links=_extract_links(text),
                ))
            continue

        # Bullet list: collect consecutive bullet lines as one text block.
        if _BULLET_RE.match(line):
            buf = []
            while i < n:
                bm = _BULLET_RE.match(lines[i])
                if not bm:
                    if lines[i].strip() == "":
                        # allow one blank between groups then continue if bullet
                        if i + 1 < n and _BULLET_RE.match(lines[i + 1]):
                            i += 1
                            continue
                    break
                buf.append(bm.group(1).strip())
                i += 1
            text = "\n".join(buf)
            blocks.append(TextBlock(
                heading_path=_heading_stack_str(doc_path, stack),
                text=text,
                source_links=_extract_links(text),
            ))
            continue

        # Ordered list.
        if _ORDERED_RE.match(line):
            buf = []
            while i < n:
                om = _ORDERED_RE.match(lines[i])
                if not om:
                    if lines[i].strip() == "":
                        if i + 1 < n and _ORDERED_RE.match(lines[i + 1]):
                            i += 1
                            continue
                    break
                buf.append(om.group(1).strip())
                i += 1
            text = "\n".join(buf)
            blocks.append(TextBlock(
                heading_path=_heading_stack_str(doc_path, stack),
                text=text,
                source_links=_extract_links(text),
            ))
            continue

        # Paragraph: collect until blank line or structural line.
        buf = []
        while i < n:
            cur = lines[i]
            cs = cur.strip()
            if not cs:
                break
            if (_HEADING_RE.match(cur) or _TABLE_ROW_RE.match(cur)
                    or cs.startswith(">") or _BULLET_RE.match(cur)
                    or _ORDERED_RE.match(cur) or _HR_RE.match(cur)):
                break
            buf.append(cs)
            i += 1
        text = "\n".join(buf)
        if text:
            blocks.append(TextBlock(
                heading_path=_heading_stack_str(doc_path, stack),
                text=text,
                source_links=_extract_links(text),
            ))

    return ParsedDocument(doc_path=doc_path, blocks=tuple(blocks))
