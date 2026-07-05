"""Deterministic scan of a Markdown knowledge root (C1 spec §2/§5).

Path order is **sorted by UTF-8 bytes of the relative path** (basename in a
flat corpus); this is stable across platforms and independent of locale.
Only a lowercase ``.md`` suffix is treated as Markdown — the corpus
is authored that way and we want a deterministic, documented rule. Reads
are UTF-8 (``ensure_ascii=False`` end to end, no mojibake repair on disk).

Output is a tuple of :class:`MarkdownFile` (relative path + raw content),
the input to the markdown parser.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["KnowledgeSourceReadError", "MarkdownFile", "scan_markdown_root"]


class KnowledgeSourceReadError(RuntimeError):
    """Classified, path-aware failure while reading the Markdown corpus."""

    def __init__(self, path: str, category: str) -> None:
        self.path = path
        self.category = category
        super().__init__(f"{category}: {path}")


@dataclass(frozen=True, slots=True)
class MarkdownFile:
    """A scanned Markdown file — relative path + raw UTF-8 content."""

    path: str        # relative to source_root, POSIX-style separators
    content: str


def _relative_path(root: str, full: str) -> str:
    rel = os.path.relpath(full, root)
    return rel.replace(os.sep, "/")


def scan_markdown_root(source_root: str) -> tuple[MarkdownFile, ...]:
    """Scan ``source_root`` for ``*.md`` files (non-recursive flat scan).

    Order: UTF-8 codepoint sort of the relative path. Raises FileNotFoundError
    if ``source_root`` does not exist.
    """
    if not os.path.isdir(source_root):
        raise KnowledgeSourceReadError(source_root, "source_root_missing")

    names = [
        n for n in os.listdir(source_root)
        if n.endswith(".md") and os.path.isfile(os.path.join(source_root, n))
    ]
    # Deterministic: UTF-8 codepoint order of the relative path.
    names.sort(key=lambda n: _relative_path(source_root, os.path.join(source_root, n)).encode("utf-8"))

    files: list[MarkdownFile] = []
    for name in names:
        full = os.path.join(source_root, name)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                content = fh.read()
        except UnicodeDecodeError as exc:
            raise KnowledgeSourceReadError(
                _relative_path(source_root, full), "invalid_utf8"
            ) from exc
        except OSError as exc:
            raise KnowledgeSourceReadError(
                _relative_path(source_root, full), "read_failed"
            ) from exc
        files.append(MarkdownFile(path=_relative_path(source_root, full), content=content))
    return tuple(files)
