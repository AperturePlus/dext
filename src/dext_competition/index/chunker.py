"""Chunk a parsed Markdown document into :class:`Chunk` units (C1 spec §4).

A chunk is the **leaf section**: the run of body blocks (paragraphs, lists,
tables, blockquotes) sharing one resolved ``heading_path``. Heading blocks
themselves are metadata, not chunk bodies. Each chunk records all §4 fields;
``chunk_hash`` is SHA-256 of the canonical chunk text (UTF-8). ``last_verified``
is parsed once from the document's leading blockquote
(``最近核验：YYYY-MM-DD``) and attached to every chunk in the document so a
standalone search hit retains document-level freshness metadata.
"""
from __future__ import annotations

import re

from dext_competition.contracts.knowledge import Chunk
from dext_competition.index.markdown import (
    Block,
    HeadingBlock,
    ParsedDocument,
    TableBlock,
    TextBlock,
)

__all__ = ["chunk_document", "chunk_hash", "canonical_chunk_text"]

_VERIFY_RE = re.compile(r"最近核验[：:]\s*(\d{4}-\d{2}-\d{2})")


def chunk_hash(canonical_text: str) -> str:
    """SHA-256 hex of the canonical chunk text (UTF-8)."""
    import hashlib
    return hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _table_to_text(block: TableBlock) -> str:
    lines = ["|".join(block.header)]
    for row in block.rows:
        lines.append("|".join(row))
    return "\n".join(lines)


def canonical_chunk_text(blocks: tuple[Block, ...]) -> str:
    """Deterministic serialization of a chunk's body blocks."""
    parts: list[str] = []
    for blk in blocks:
        if isinstance(blk, (HeadingBlock,)):
            continue
        if isinstance(blk, TextBlock):
            parts.append(blk.text)
        elif isinstance(blk, TableBlock):
            parts.append(_table_to_text(blk))
    return "\n\n".join(p for p in parts if p)


def _aggregate_links(blocks: tuple[Block, ...]) -> tuple[str, ...]:
    links: list[str] = []
    seen: set[str] = set()
    for blk in blocks:
        if isinstance(blk, (TextBlock, TableBlock)):
            for url in blk.source_links:
                if url not in seen:
                    seen.add(url)
                    links.append(url)
    return tuple(links)


def _doc_last_verified(doc: ParsedDocument) -> str | None:
    # The leading blockquote (first body block of the first heading) carries
    # the document's verification date.
    for blk in doc.blocks:
        if isinstance(blk, TextBlock):
            m = _VERIFY_RE.search(blk.text)
            if m:
                return m.group(1)
    return None


def chunk_document(doc: ParsedDocument) -> tuple[Chunk, ...]:
    """Split ``doc`` into chunks, one per leaf section (distinct heading_path)."""
    if not doc.blocks:
        return ()

    last_verified = _doc_last_verified(doc)

    # Group body blocks by their heading_path (leaf section), preserving order.
    sections: list[tuple[str, tuple[Block, ...]]] = []
    current_path: str | None = None
    current_body: list[Block] = []

    def _flush() -> None:
        if current_path is not None and current_body:
            sections.append((current_path, tuple(current_body)))

    for blk in doc.blocks:
        if isinstance(blk, HeadingBlock):
            # A heading starts a new leaf section.
            _flush()
            current_path = blk.heading_path
            current_body = []
        else:
            if current_path is None:
                # Body before any heading: attach to a synthetic doc-level path.
                current_path = doc.doc_path
            current_body.append(blk)
    _flush()

    chunks: list[Chunk] = []
    for path, body in sections:
        text = canonical_chunk_text(body)
        if not text.strip():
            continue
        links = _aggregate_links(body)
        chunks.append(Chunk(
            doc_path=doc.doc_path,
            heading_path=path,
            chunk_hash=chunk_hash(text),
            text=text,
            source_links=links,
            last_verified=last_verified,
        ))
    return tuple(chunks)
