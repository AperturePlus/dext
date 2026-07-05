"""Validated, read-only implementation of ``KnowledgeIndexPort``."""
from __future__ import annotations

import json
from pathlib import Path

from dext_competition.contracts.knowledge import (
    Chunk,
    KnowledgeBaseManifest,
    KnowledgeHit,
)
from dext_competition.index.artifacts import (
    BM25_FILE,
    CHUNKS_FILE,
    MANIFEST_FILE,
    IndexArtifactError,
    chunks_from_jsonl,
)
from dext_competition.index.bm25 import BM25FormatError, BM25Index
from dext_competition.index.chunker import chunk_hash
from dext_competition.index.manifest import (
    content_hash,
    manifest_from_json,
    version_id,
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise IndexArtifactError(f"missing index artifact: {path.name}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise IndexArtifactError(f"cannot read index artifact {path.name}: {exc}") from exc


class FileKnowledgeIndex:
    """Load and validate a complete artifact snapshot once, then query in memory."""

    def __init__(self, artifact_dir: str | Path) -> None:
        root = Path(artifact_dir)
        try:
            manifest = manifest_from_json(_read_text(root / MANIFEST_FILE))
            chunks = chunks_from_jsonl(_read_text(root / CHUNKS_FILE))
            bm25 = BM25Index.from_dict(json.loads(_read_text(root / BM25_FILE)))
        except IndexArtifactError:
            raise
        except (BM25FormatError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise IndexArtifactError(f"invalid index artifact: {exc}") from exc
        self._validate(manifest, chunks, bm25)
        self._manifest = manifest
        self._chunks = chunks
        self._bm25 = bm25
        by_doc: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            by_doc.setdefault(chunk.doc_path, []).append(chunk)
        self._by_doc = {path: tuple(values) for path, values in by_doc.items()}

    @staticmethod
    def _validate(
        manifest: KnowledgeBaseManifest,
        chunks: tuple[Chunk, ...],
        bm25: BM25Index,
    ) -> None:
        for chunk in chunks:
            if chunk_hash(chunk.text) != chunk.chunk_hash:
                raise IndexArtifactError(
                    f"chunk hash mismatch: {chunk.doc_path} > {chunk.heading_path}"
                )
        aggregate = content_hash(chunks)
        if manifest.content_hash != aggregate:
            raise IndexArtifactError("manifest content_hash mismatch")
        if manifest.version_id != version_id(aggregate):
            raise IndexArtifactError("manifest version_id mismatch")
        document_count = len({chunk.doc_path for chunk in chunks})
        if manifest.file_count != document_count or manifest.markdown_file_count != document_count:
            raise IndexArtifactError("manifest file count mismatch")
        hashes = tuple(chunk.chunk_hash for chunk in chunks)
        if bm25.document_hashes != hashes:
            raise IndexArtifactError("BM25 document order/hash mismatch")

    def manifest(self) -> KnowledgeBaseManifest:
        return self._manifest

    async def retrieve(self, doc_path: str) -> tuple[Chunk, ...]:
        return self._by_doc.get(doc_path.replace("\\", "/"), ())

    async def query(self, text: str, *, limit: int = 10) -> tuple[KnowledgeHit, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if not text.strip():
            return ()
        scores = self._bm25.score(text)
        ranked = sorted(
            scores.items(),
            key=lambda item: (
                -item[1],
                self._chunks[item[0]].doc_path,
                self._chunks[item[0]].heading_path,
                self._chunks[item[0]].chunk_hash,
            ),
        )
        return tuple(
            KnowledgeHit(
                chunk=self._chunks[document_id],
                source_ref=self._chunks[document_id].to_source_ref(),
                score=score,
            )
            for document_id, score in ranked[:limit]
            if score > 0
        )

    async def search(
        self, keywords: tuple[str, ...], *, limit: int = 10
    ) -> tuple[KnowledgeHit, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return await self.query(
            " ".join(keyword.strip() for keyword in keywords if keyword.strip()),
            limit=limit,
        )


def verify_index(artifact_dir: str | Path) -> FileKnowledgeIndex:
    """Load all artifacts and fail if any integrity invariant is violated."""

    return FileKnowledgeIndex(artifact_dir)


__all__ = ["FileKnowledgeIndex", "verify_index"]
