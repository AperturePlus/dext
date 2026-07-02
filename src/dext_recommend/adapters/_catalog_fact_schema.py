"""Frozen published-catalog contract for R4 professor-fact reads.

The runtime must NOT import dext_graph; this module is the only place R4 pins
the published table/column names and JSON payload keys it depends on. Mirror
of dext_graph.catalog.models CATALOG_SCHEMA_VERSION>=1 published tables.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# R4b §2: facts contract minimum schema version. A catalog that satisfies the
# version but lacks the topic/evidence/profile tables is still unusable for R4;
# the required-tables capability check below enforces that.
MIN_FACT_CATALOG_SCHEMA_VERSION = 1

# SQLite default host parameter limit is 999; keep a safety margin.
FACT_SQLITE_PARAM_LIMIT = 999

REQUIRED_FACT_TABLES: tuple[str, ...] = (
    "canonical_professors",
    "professor_profiles",
    "professor_observations",
    "entity_observations",
    "research_statements",
    "publication_mentions",
    "statement_topic_links",
    "topics",
    "quality_findings",
    "source_documents",
    "build_source_tasks",
)

REQUIRED_FACT_COLUMNS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "canonical_professors": (
        "entity_id", "build_id", "name", "title_raw", "title_family",
        "role_status", "role_reason_codes", "master_eligibility",
        "phd_eligibility", "research_areas_text", "bio", "email", "phone",
        "profile_url", "external_url", "active", "completeness",
    ),
    "professor_profiles": (
        "build_id", "entity_id", "profile_hash", "payload_json", "created_at",
    ),
    "professor_observations": (
        "id", "university_id", "source_url", "source_page_kind",
        "source_document_id", "payload_json", "active",
    ),
    "entity_observations": (
        "entity_id", "observation_id", "build_id",
    ),
    "research_statements": (
        "id", "build_id", "entity_id", "observation_id", "normalized_text",
        "language",
    ),
    "publication_mentions": (
        "id", "build_id", "entity_id", "observation_id", "normalized_text",
        "year", "confidence", "needs_review",
    ),
    "statement_topic_links": (
        "build_id", "statement_id", "taxonomy_version", "topic_id",
        "relation_type", "evidence_span", "review_status", "provenance_ref",
    ),
    "topics": ("taxonomy_version", "id", "canonical_name", "kind", "status"),
    "quality_findings": (
        "id", "build_id", "severity", "code", "entity_id", "details_json",
        "resolved",
    ),
    "source_documents": ("id", "url", "fetched_at"),
    "build_source_tasks": ("build_id", "university_id", "university_name"),
})

# JSON keys inside professor_profiles.payload_json (R4b §2 authority matrix).
PROFILE_PAYLOAD_KEYS = (
    "university_id", "org_unit_ids", "city", "topic_ids", "role_status",
    "master_eligibility", "phd_eligibility", "title_family", "profile_hash",
    "provenance_ref",
)

# JSON keys inside professor_observations.payload_json (R4b §2 org-unit display).
OBSERVATION_PAYLOAD_AFFILIATION_KEYS = ("org_unit_name",)

SCHEMA_VERSION_SQL = "SELECT value FROM catalog_meta WHERE key='schema_version'"
TABLE_LIST_SQL = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
)
COLUMN_LIST_SQL = "PRAGMA table_info({table})"


__all__ = [
    "FACT_SQLITE_PARAM_LIMIT",
    "MIN_FACT_CATALOG_SCHEMA_VERSION",
    "OBSERVATION_PAYLOAD_AFFILIATION_KEYS",
    "PROFILE_PAYLOAD_KEYS",
    "REQUIRED_FACT_COLUMNS",
    "REQUIRED_FACT_TABLES",
    "SCHEMA_VERSION_SQL",
    "TABLE_LIST_SQL",
    "COLUMN_LIST_SQL",
]
