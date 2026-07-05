"""In-process fakes for dext_competition ports (G0).

Test doubles for C1/C2/C3 TDD — never used in production. Each fake satisfies
its runtime-checkable Protocol so contract tests assert the wiring.
"""
from __future__ import annotations

from dext_competition.contracts.catalog import CompetitionCard, CompetitionCatalogManifest
from dext_competition.contracts.knowledge import (
    Chunk,
    KnowledgeBaseManifest,
    KnowledgeHit,
)
import datetime


def _default_manifest() -> KnowledgeBaseManifest:
    return KnowledgeBaseManifest(
        version_id="kb-fake-v0",
        source_root="data/竞赛助手/",
        file_count=0,
        markdown_file_count=0,
        content_hash="fake",
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )


def _default_catalog_manifest() -> CompetitionCatalogManifest:
    return CompetitionCatalogManifest(
        version_id="catalog-fake-v0",
        knowledge_base_version="kb-fake-v0",
        rules_version="catalog-rules-fake-v0",
        card_count=0,
        in_2024_catalog_count=0,
        content_hash="fake",
        generated_at=datetime.datetime(2026, 7, 2, 0, 0, 0),
    )


class FakeKnowledgeIndexPort:
    """Minimal in-memory fake; preset chunks returned verbatim."""

    def __init__(self, chunks: tuple[Chunk, ...] = (),
                 manifest: KnowledgeBaseManifest | None = None) -> None:
        self._chunks: dict[str, tuple[Chunk, ...]] = {}
        if chunks:
            for c in chunks:
                self._chunks.setdefault(c.doc_path, ())
                self._chunks[c.doc_path] += (c,)
        self._manifest = manifest or _default_manifest()
        self.calls: list[dict] = []

    def manifest(self) -> KnowledgeBaseManifest:
        return self._manifest

    async def retrieve(self, doc_path: str) -> tuple[Chunk, ...]:
        self.calls.append({"op": "retrieve", "doc_path": doc_path})
        return self._chunks.get(doc_path, ())

    async def query(self, text: str, *, limit: int = 10) -> tuple[KnowledgeHit, ...]:
        self.calls.append({"op": "query", "text": text, "limit": limit})
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if not text.strip():
            return ()
        all_chunks = tuple(c for tup in self._chunks.values() for c in tup)
        return tuple(
            KnowledgeHit(c, c.to_source_ref(), 1.0)
            for c in all_chunks[:limit]
        )

    async def search(self, keywords: tuple[str, ...], *,
                     limit: int = 10) -> tuple[KnowledgeHit, ...]:
        self.calls.append({"op": "search", "keywords": keywords, "limit": limit})
        if limit < 1:
            raise ValueError("limit must be at least 1")
        text = " ".join(keyword for keyword in keywords if keyword.strip())
        if not text:
            return ()
        all_chunks = tuple(c for tup in self._chunks.values() for c in tup)
        return tuple(
            KnowledgeHit(c, c.to_source_ref(), 1.0)
            for c in all_chunks[:limit]
        )


class FakeCompetitionCatalogPort:
    def __init__(self, cards: tuple[CompetitionCard, ...] = (),
                 manifest: CompetitionCatalogManifest | None = None) -> None:
        self._cards: dict[str, CompetitionCard] = {c.competition_id: c for c in cards}
        self._manifest = manifest or _default_catalog_manifest()
        self.calls: list[dict] = []

    def manifest(self) -> CompetitionCatalogManifest:
        return self._manifest

    async def get(self, competition_id: str) -> CompetitionCard | None:
        self.calls.append({"op": "get", "competition_id": competition_id})
        return self._cards.get(competition_id)

    async def list_competitions(self) -> tuple[CompetitionCard, ...]:
        self.calls.append({"op": "list_competitions"})
        return tuple(self._cards.values())


__all__ = ["FakeCompetitionCatalogPort", "FakeKnowledgeIndexPort"]
