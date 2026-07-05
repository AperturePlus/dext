"""Knowledge-base chunk + manifest contracts (C1 spec §4/§5).

``SourceRef`` is reused from dext_grounded; ``Chunk`` is the C1-scanned unit
that C2/C4 will consume via KnowledgeIndexPort. Chunk collection fields coerce
list input to tuple for deep immutability.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from dext_grounded import SourceRef


@dataclass(frozen=True, slots=True)
class Chunk:
    doc_path: str
    heading_path: str
    chunk_hash: str
    text: str
    source_links: tuple[str, ...] = ()
    last_verified: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_links",
            tuple(self.source_links) if self.source_links is not None else (),
        )

    def to_source_ref(self) -> SourceRef:
        """Return the canonical citation identity for this chunk."""

        return SourceRef(
            doc_path=self.doc_path,
            heading_path=self.heading_path,
            chunk_hash=self.chunk_hash,
            quote_or_summary=self.text,
            official_url=self.source_links[0] if self.source_links else None,
            last_verified=self.last_verified,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeBaseManifest:
    version_id: str
    source_root: str
    file_count: int
    markdown_file_count: int
    content_hash: str
    generated_at: datetime.datetime


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    """A scored knowledge-index hit with its canonical citation reference."""

    chunk: Chunk
    source_ref: SourceRef
    score: float


__all__ = ["Chunk", "KnowledgeBaseManifest", "KnowledgeHit"]
