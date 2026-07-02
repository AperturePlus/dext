"""Professor fact-bundle assembly (R4)."""
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids
from dext_recommend.facts.evidence import (
    WEAK_EXPLANATION_TEXT, build_fact_items, select_publication_rows,
    select_publication_snippets, select_statement_rows, select_statement_snippets,
)
from dext_recommend.facts.source_urls import (
    canonicalize_source_url, dedupe_source_urls,
)

__all__ = [
    "WEAK_EXPLANATION_TEXT", "build_fact_items", "canonicalize_source_url",
    "chunk_entity_ids", "dedupe_entity_ids", "dedupe_source_urls",
    "select_publication_rows", "select_publication_snippets",
    "select_statement_rows", "select_statement_snippets",
]
