"""Shared SQLite fixture builder for R4 professor-fact tests.

Mirrors the real published catalog DDL (dext_graph.catalog.models, schema
version 6) with the tables/columns R4 reads. Constraints match production so
fixture inserts exercise the same CHECK/PRIMARY KEY rules. JSON columns take
pre-serialized strings (callers use json.dumps).
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path

# A trimmed-but-faithful subset of the published DDL: every table R4 reads,
# with the columns and CHECK constraints R4 relies on. Mirrors
# src/dext_graph/catalog/models.py CATALOG_SCHEMA_VERSION=6.
_SCHEMA = """
CREATE TABLE catalog_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE graph_builds (
    id TEXT PRIMARY KEY, status TEXT NOT NULL,
    curation_version TEXT NOT NULL, taxonomy_version TEXT,
    graph_schema_version INTEGER NOT NULL, vector_schema_version INTEGER NOT NULL,
    settings_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT, finished_at TEXT, last_error TEXT
);
CREATE TABLE build_source_tasks (
    build_id TEXT NOT NULL, university_id TEXT NOT NULL,
    university_name TEXT NOT NULL, abbr TEXT NOT NULL,
    source_path TEXT NOT NULL, ordinal INTEGER NOT NULL,
    status TEXT NOT NULL, source_snapshot_id TEXT,
    snapshot_reused INTEGER NOT NULL DEFAULT 0,
    rows_read INTEGER NOT NULL DEFAULT 0,
    observations_written INTEGER NOT NULL DEFAULT 0,
    observations_inserted INTEGER NOT NULL DEFAULT 0,
    observations_reused INTEGER NOT NULL DEFAULT 0,
    documents_seen INTEGER NOT NULL DEFAULT 0,
    documents_inserted INTEGER NOT NULL DEFAULT 0,
    findings INTEGER NOT NULL DEFAULT 0,
    rejected_rows INTEGER NOT NULL DEFAULT 0,
    deactivation_eligible INTEGER NOT NULL DEFAULT 0,
    observations_inactivated INTEGER NOT NULL DEFAULT 0,
    last_error TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY (build_id, university_id),
    UNIQUE (build_id, ordinal)
);
CREATE TABLE source_documents (
    id TEXT PRIMARY KEY, university_id TEXT NOT NULL, url TEXT NOT NULL,
    content_hash TEXT NOT NULL, title TEXT, text_zstd BLOB, fetched_at TEXT,
    first_seen_build TEXT NOT NULL, last_seen_build TEXT NOT NULL,
    UNIQUE (url, content_hash)
);
CREATE TABLE professor_observations (
    id TEXT PRIMARY KEY, university_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL, source_professor_id INTEGER,
    source_url TEXT, source_page_kind TEXT NOT NULL,
    extraction_batch_size INTEGER, source_content_hash TEXT,
    source_document_id TEXT, org_unit_source_id INTEGER,
    name_raw TEXT NOT NULL, name_key TEXT NOT NULL,
    payload_json TEXT NOT NULL, row_hash TEXT NOT NULL,
    provenance_grade TEXT NOT NULL,
    first_seen_build TEXT NOT NULL, last_seen_build TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);
CREATE TABLE quality_findings (
    id TEXT PRIMARY KEY, build_id TEXT NOT NULL, severity TEXT NOT NULL,
    code TEXT NOT NULL, entity_id TEXT, observation_id TEXT,
    details_json TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE entities (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
    merged_into_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE entity_observations (
    entity_id TEXT NOT NULL, observation_id TEXT NOT NULL,
    match_method TEXT NOT NULL, match_score REAL, build_id TEXT NOT NULL,
    PRIMARY KEY (build_id, observation_id)
);
CREATE TABLE canonical_professors (
    entity_id TEXT NOT NULL, build_id TEXT NOT NULL, name TEXT NOT NULL,
    title_raw TEXT, title_family TEXT NOT NULL,
    role_status TEXT NOT NULL, role_reason_codes TEXT NOT NULL,
    master_eligibility TEXT NOT NULL, phd_eligibility TEXT NOT NULL,
    research_areas_text TEXT, bio TEXT, email TEXT, phone TEXT,
    profile_url TEXT, external_url TEXT,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    completeness REAL NOT NULL,
    PRIMARY KEY (entity_id, build_id)
);
CREATE TABLE research_statements (
    id TEXT NOT NULL, build_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    observation_id TEXT NOT NULL, raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL, language TEXT NOT NULL,
    statement_hash TEXT NOT NULL,
    PRIMARY KEY (build_id, id)
);
CREATE TABLE publication_mentions (
    id TEXT NOT NULL, build_id TEXT NOT NULL, entity_id TEXT NOT NULL,
    observation_id TEXT NOT NULL, raw_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL, doi TEXT, year INTEGER,
    confidence REAL NOT NULL, needs_review INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (build_id, id, observation_id)
);
CREATE TABLE professor_profiles (
    build_id TEXT NOT NULL, entity_id TEXT NOT NULL, profile_hash TEXT NOT NULL,
    template_version TEXT NOT NULL, tokenizer_identity TEXT NOT NULL,
    normalized_profile TEXT NOT NULL, token_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (build_id, entity_id)
);
CREATE TABLE taxonomy_versions (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, parent_version TEXT,
    manifest_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE topics (
    taxonomy_version TEXT NOT NULL, id TEXT NOT NULL,
    canonical_name TEXT NOT NULL, normalized_name TEXT NOT NULL,
    kind TEXT NOT NULL, status TEXT NOT NULL, created_method TEXT NOT NULL,
    PRIMARY KEY (taxonomy_version, id)
);
CREATE TABLE statement_topic_links (
    build_id TEXT NOT NULL, statement_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL, topic_id TEXT NOT NULL,
    relation_type TEXT NOT NULL, evidence_span TEXT NOT NULL,
    method TEXT NOT NULL, confidence REAL NOT NULL,
    review_status TEXT NOT NULL, provenance_ref TEXT NOT NULL,
    PRIMARY KEY (build_id, statement_id, topic_id, relation_type, evidence_span)
);
"""


def _entities_rows(rows):
    # entities rows: (id, kind, status, merged_into_id, created_at, updated_at)
    return rows


def build_catalog_db(tmp_path: Path, *, schema_version: int = 6, **row_sets) -> Path:
    """Create a catalog DB at tmp_path/catalog.db with the published DDL and rows.

    Each kwarg name matches a table; values are iterables of tuples in that
    table's column order (see _INSERTS below). Missing kwargs default to no rows.
    """
    path = tmp_path / "catalog.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO catalog_meta(key, value) VALUES ('schema_version', ?)",
            (str(schema_version),),
        )
        _insert_rows(conn, row_sets)
        conn.commit()
    return path


_INSERTS = {
    "graph_builds": (
        "INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,"
        "graph_schema_version,vector_schema_version,settings_json) VALUES (?,?,?,?,?,?,?)"
    ),
    "build_source_tasks": (
        "INSERT INTO build_source_tasks(build_id,university_id,university_name,"
        "abbr,source_path,ordinal,status,updated_at) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "source_documents": (
        "INSERT INTO source_documents(id,university_id,url,content_hash,fetched_at,"
        "first_seen_build,last_seen_build) VALUES (?,?,?,?,?,?,?)"
    ),
    "professor_observations": (
        "INSERT INTO professor_observations(id,university_id,source_snapshot_id,"
        "source_url,source_page_kind,name_raw,name_key,payload_json,row_hash,"
        "provenance_grade,first_seen_build,last_seen_build,active) "
        "VALUES (?,?,?,?,?,'fixture-name','fixture-key',?,?,?,?,?,?)"
    ),
    "quality_findings": (
        "INSERT INTO quality_findings(id,build_id,severity,code,entity_id,"
        "observation_id,details_json,resolved) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "entities": (
        "INSERT INTO entities(id,kind,status,merged_into_id,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?)"
    ),
    "entity_observations": (
        "INSERT INTO entity_observations(entity_id,observation_id,match_method,"
        "build_id) VALUES (?,?,?,?)"
    ),
    "canonical_professors": (
        "INSERT INTO canonical_professors(entity_id,build_id,name,title_raw,"
        "title_family,role_status,role_reason_codes,master_eligibility,"
        "phd_eligibility,research_areas_text,bio,email,phone,profile_url,"
        "external_url,active,completeness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    ),
    "research_statements": (
        "INSERT INTO research_statements(id,build_id,entity_id,observation_id,"
        "raw_text,normalized_text,language,statement_hash) VALUES (?,?,?,?,?,?,?,?)"
    ),
    "publication_mentions": (
        "INSERT INTO publication_mentions(id,build_id,entity_id,observation_id,"
        "raw_text,normalized_text,year,confidence,needs_review) "
        "VALUES (?,?,?,?,?,?,?,?,?)"
    ),
    "professor_profiles": (
        "INSERT INTO professor_profiles(build_id,entity_id,profile_hash,"
        "template_version,tokenizer_identity,normalized_profile,token_count,"
        "payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)"
    ),
    "taxonomy_versions": (
        "INSERT INTO taxonomy_versions(id,status,parent_version,manifest_hash,"
        "created_at) VALUES (?,?,?,?,?)"
    ),
    "topics": (
        "INSERT INTO topics(taxonomy_version,id,canonical_name,normalized_name,"
        "kind,status,created_method) VALUES (?,?,?,?,?,?,?)"
    ),
    "statement_topic_links": (
        "INSERT INTO statement_topic_links(build_id,statement_id,taxonomy_version,"
        "topic_id,relation_type,evidence_span,method,confidence,review_status,"
        "provenance_ref) VALUES (?,?,?,?,?,?,?,?,?,?)"
    ),
}


def _insert_rows(conn: sqlite3.Connection, row_sets: dict) -> None:
    for table, rows in row_sets.items():
        if table == "schema_version":
            continue
        if table not in _INSERTS:
            raise KeyError(f"unknown fixture table: {table}")
        rows = list(rows)
        if not rows:
            continue
        conn.executemany(_INSERTS[table], rows)


def dumps(value) -> str:
    """json.dumps with ensure_ascii=False for fixture payloads."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = ["build_catalog_db", "dumps"]
