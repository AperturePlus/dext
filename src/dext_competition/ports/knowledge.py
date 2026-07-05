"""Read-only knowledge-index port (C1 spec §6).

C1 owns the concrete scanner/chunker/manifest implementation; C2/C4 consume
this protocol, never C1 private objects. BM25/keyword query returns SourceRef
hits. No vector reuse — competition has its own collection (spec §6).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from dext_competition.contracts.knowledge import (
    Chunk,
    KnowledgeBaseManifest,
    KnowledgeHit,
)


@runtime_checkable
class KnowledgeIndexPort(Protocol):
    """Read-only access to the built Markdown knowledge index."""

    def manifest(self) -> KnowledgeBaseManifest:
        ...

    async def retrieve(self, doc_path: str) -> tuple[Chunk, ...]:
        ...

    async def query(self, text: str, *, limit: int = 10) -> tuple[KnowledgeHit, ...]:
        """BM25/keyword match (C1 spec §6)."""
        ...

    async def search(
        self, keywords: tuple[str, ...], *, limit: int = 10
    ) -> tuple[KnowledgeHit, ...]:
        ...


__all__ = ["KnowledgeIndexPort"]
