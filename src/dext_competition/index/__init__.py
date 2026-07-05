"""C1 knowledge index — scanner/markdown/chunker/manifest/bm25 + build.

Re-exports the C1 public surface. Concrete implementation objects are owned
by this package; downstream consumers (C2/C4) must use ``KnowledgeIndexPort``
from ``dext_competition.ports.knowledge``, never these private types.
"""
from __future__ import annotations

from dext_competition.index.artifacts import IndexArtifactError
from dext_competition.index.build import IndexBuildResult, build_index
from dext_competition.index.repository import FileKnowledgeIndex, verify_index
from dext_competition.index.scanner import (
    KnowledgeSourceReadError,
    MarkdownFile,
    scan_markdown_root,
)

__all__ = [
    "FileKnowledgeIndex",
    "IndexArtifactError",
    "IndexBuildResult",
    "KnowledgeSourceReadError",
    "MarkdownFile",
    "build_index",
    "scan_markdown_root",
    "verify_index",
]
