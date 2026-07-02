"""Pure evidence/snippet/FactItem assembly for R4 (R4b §4, grounded §3).

No I/O. Snippets are truncated + capped; FactItems are FACT when a SourceRef
exists and UNCERTAIN otherwise (FactItem.__post_init__ enforces the invariant).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from dext_grounded import FactItem, SourceRef
from dext_grounded.content import ContentClass

WEAK_EXPLANATION_TEXT = (
    "候选排序主要由语义相似度贡献，但当前缺少可回溯的详情证据。"
)


def select_statement_snippets(
    statements: Sequence[Mapping], *, max_chars: int = 600, max_count: int = 5,
) -> tuple[str, ...]:
    out: list[str] = []
    total = 0
    for row in statements:
        if len(out) >= max_count:
            break
        text = str(row.get("normalized_text") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars and out:
            break
        out.append(text)
        total += len(text)
    return tuple(out)


def select_publication_snippets(
    mentions: Sequence[Mapping], *, max_chars: int = 400, max_count: int = 5,
) -> tuple[str, ...]:
    out: list[str] = []
    total = 0
    for row in mentions:
        if len(out) >= max_count:
            break
        if int(row.get("needs_review") or 0) != 0:
            continue
        text = str(row.get("normalized_text") or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars and out:
            break
        out.append(text)
        total += len(text)
    return tuple(out)


def build_fact_items(
    *,
    identity: Mapping[str, str],
    eligibility: Mapping[str, str],
    research_statements: Sequence[str],
    approved_topics: Sequence[str],
    publications: Sequence[str],
    source_refs_by_field: Mapping[str, Sequence[SourceRef]],
) -> tuple[FactItem, ...]:
    refs = source_refs_by_field or {}
    items: list[FactItem] = []

    def _add(field: str, value: str) -> None:
        sr = tuple(refs.get(field, ()))
        items.append(FactItem(
            field=field, value=value,
            content_class=ContentClass.FACT if sr else ContentClass.UNCERTAIN,
            source_refs=sr,
        ))

    _add("display_name", str(identity.get("display_name") or ""))
    _add("master_eligibility", str(eligibility.get("master_eligibility") or ""))
    _add("phd_eligibility", str(eligibility.get("phd_eligibility") or ""))
    if research_statements:
        _add("research_statement", "; ".join(research_statements))
    if approved_topics:
        _add("approved_topics", "; ".join(approved_topics))
    if publications:
        _add("publications", "; ".join(publications))
    return tuple(items)


__all__ = [
    "WEAK_EXPLANATION_TEXT",
    "build_fact_items",
    "select_publication_snippets",
    "select_statement_snippets",
]
