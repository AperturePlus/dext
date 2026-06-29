"""Catalog schema and small value objects for the stage-1 graph build."""

from __future__ import annotations

from dataclasses import dataclass

CATALOG_SCHEMA_VERSION = 2

BUILD_STATUSES = (
    "CREATED",
    "SNAPSHOTTING",
    "INGESTING",
    "CURATING",
    "EMBEDDING",
    "WRITING_GRAPH",
    "WRITING_VECTOR",
    "VALIDATING",
    "READY",
    "ACTIVE",
    "FAILED",
    "FAILED_VALIDATION",
)

SOURCE_TASK_STATUSES = (
    "PENDING",
    "SNAPSHOTTING",
    "SNAPSHOTTED",
    "INGESTING",
    "COMPLETED",
    "FAILED",
)


@dataclass(frozen=True)
class BuildSource:
    university_id: str
    university_name: str
    abbr: str
    source_path: str
    ordinal: int


SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS graph_builds (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ({','.join(repr(v) for v in BUILD_STATUSES)})),
    source_manifest_hash TEXT,
    curation_version TEXT NOT NULL,
    taxonomy_version TEXT,
    graph_schema_version INTEGER NOT NULL,
    vector_schema_version INTEGER NOT NULL,
    embedding_provider TEXT,
    embedding_base_url TEXT,
    embedding_model TEXT,
    embedding_fingerprint TEXT,
    embedding_dimension INTEGER,
    settings_json TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{{}}',
    started_at TEXT,
    finished_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshots (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    snapshot_path TEXT NOT NULL UNIQUE,
    file_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    row_counts_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (university_id, file_hash)
);

CREATE TABLE IF NOT EXISTS build_source_tasks (
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    university_id TEXT NOT NULL,
    university_name TEXT NOT NULL,
    abbr TEXT NOT NULL,
    source_path TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ({','.join(repr(v) for v in SOURCE_TASK_STATUSES)})),
    source_snapshot_id TEXT REFERENCES source_snapshots(id),
    snapshot_reused INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_reused IN (0, 1)),
    rows_read INTEGER NOT NULL DEFAULT 0,
    observations_written INTEGER NOT NULL DEFAULT 0,
    observations_inserted INTEGER NOT NULL DEFAULT 0,
    observations_reused INTEGER NOT NULL DEFAULT 0,
    documents_seen INTEGER NOT NULL DEFAULT 0,
    documents_inserted INTEGER NOT NULL DEFAULT 0,
    findings INTEGER NOT NULL DEFAULT 0,
    rejected_rows INTEGER NOT NULL DEFAULT 0,
    deactivation_eligible INTEGER NOT NULL DEFAULT 0 CHECK (deactivation_eligible IN (0, 1)),
    observations_inactivated INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (build_id, university_id),
    UNIQUE (build_id, ordinal)
);

CREATE TABLE IF NOT EXISTS build_source_snapshots (
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
    PRIMARY KEY (build_id, source_snapshot_id)
);

CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    url TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    title TEXT,
    text_zstd BLOB,
    fetched_at TEXT,
    first_seen_build TEXT NOT NULL REFERENCES graph_builds(id),
    last_seen_build TEXT NOT NULL REFERENCES graph_builds(id),
    UNIQUE (url, content_hash)
);

CREATE TABLE IF NOT EXISTS professor_observations (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
    source_professor_id INTEGER,
    source_url TEXT,
    source_page_kind TEXT NOT NULL CHECK (source_page_kind IN ('single_profile','multi_profile','unknown')),
    extraction_batch_size INTEGER,
    source_content_hash TEXT,
    source_document_id TEXT REFERENCES source_documents(id),
    org_unit_source_id INTEGER,
    name_raw TEXT NOT NULL,
    name_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    row_hash TEXT NOT NULL,
    provenance_grade TEXT NOT NULL CHECK (provenance_grade IN ('direct','legacy_merged','incomplete')),
    first_seen_build TEXT NOT NULL REFERENCES graph_builds(id),
    last_seen_build TEXT NOT NULL REFERENCES graph_builds(id),
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS sink_checkpoints (
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    sink TEXT NOT NULL,
    partition_key TEXT NOT NULL,
    last_key TEXT,
    last_batch_id TEXT,
    rows_written INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (build_id, sink, partition_key)
);

CREATE TABLE IF NOT EXISTS quality_findings (
    id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
    code TEXT NOT NULL,
    entity_id TEXT,
    observation_id TEXT REFERENCES professor_observations(id),
    details_json TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1))
);

CREATE TABLE IF NOT EXISTS source_snapshot_protections (
    source_snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id),
    protection_kind TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_snapshot_id, protection_kind, reference_id)
);

CREATE INDEX IF NOT EXISTS ix_builds_status_started ON graph_builds(status, started_at);
CREATE INDEX IF NOT EXISTS ix_source_snapshots_university ON source_snapshots(university_id, created_at);
CREATE INDEX IF NOT EXISTS ix_source_tasks_status ON build_source_tasks(build_id, status, ordinal);
CREATE INDEX IF NOT EXISTS ix_source_tasks_trusted ON build_source_tasks(university_id, deactivation_eligible, updated_at);
CREATE INDEX IF NOT EXISTS ix_documents_university_url ON source_documents(university_id, url);
CREATE INDEX IF NOT EXISTS ix_observations_university_active_id ON professor_observations(university_id, active, id);
CREATE INDEX IF NOT EXISTS ix_observations_document_active ON professor_observations(source_document_id, active);
CREATE INDEX IF NOT EXISTS ix_observations_last_seen ON professor_observations(last_seen_build);
CREATE INDEX IF NOT EXISTS ix_findings_build_resolved ON quality_findings(build_id, resolved, code);
"""


CURATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS curation_runs (
    id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL UNIQUE REFERENCES graph_builds(id),
    status TEXT NOT NULL CHECK (status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    curation_version TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    rules_hash TEXT NOT NULL,
    override_manifest_hash TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind='professor'),
    status TEXT NOT NULL CHECK (status IN ('active','review','inactive','merged')),
    merged_into_id TEXT REFERENCES entities(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (merged_into_id IS NULL OR merged_into_id<>id)
);

CREATE TABLE IF NOT EXISTS identity_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    claim_type TEXT NOT NULL CHECK (claim_type IN (
      'profile_name_url','external_identity','email','listing_name_url','weak_org_name'
    )),
    claim_value TEXT NOT NULL,
    strength TEXT NOT NULL CHECK (strength IN ('strong','weak')),
    observation_id TEXT NOT NULL REFERENCES professor_observations(id),
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, claim_type, claim_value, observation_id)
);

CREATE TABLE IF NOT EXISTS entity_observations (
    entity_id TEXT NOT NULL REFERENCES entities(id),
    observation_id TEXT NOT NULL REFERENCES professor_observations(id),
    match_method TEXT NOT NULL CHECK (match_method IN ('strong','weak','new','review','reused')),
    match_score REAL,
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    PRIMARY KEY (build_id, observation_id)
);

CREATE TABLE IF NOT EXISTS field_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    field_name TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    observation_id TEXT NOT NULL REFERENCES professor_observations(id),
    confidence REAL NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0,1)),
    selection_reason TEXT,
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    UNIQUE(build_id, entity_id, field_name, normalized_value, observation_id)
);

CREATE TABLE IF NOT EXISTS curation_overrides (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    kind TEXT NOT NULL CHECK (kind IN ('field','role','merge')),
    field_name TEXT,
    value_json TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_id TEXT REFERENCES curation_overrides(id),
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    created_at TEXT NOT NULL,
    UNIQUE(entity_id, kind, field_name, version)
);

CREATE TABLE IF NOT EXISTS canonical_professors (
    entity_id TEXT NOT NULL REFERENCES entities(id),
    build_id TEXT NOT NULL REFERENCES graph_builds(id),
    name TEXT NOT NULL,
    title_raw TEXT,
    title_family TEXT NOT NULL CHECK (title_family IN (
      'professor','associate','lecturer','researcher','clinical','technical','unknown'
    )),
    role_status TEXT NOT NULL CHECK (role_status IN ('included','review','excluded')),
    role_reason_codes TEXT NOT NULL,
    master_eligibility TEXT NOT NULL CHECK (master_eligibility IN ('confirmed','unknown','conflict')),
    phd_eligibility TEXT NOT NULL CHECK (phd_eligibility IN ('confirmed','unknown','conflict')),
    research_areas_text TEXT,
    bio TEXT,
    email TEXT,
    phone TEXT,
    profile_url TEXT,
    external_url TEXT,
    active INTEGER NOT NULL CHECK (active IN (0,1)),
    completeness REAL NOT NULL,
    PRIMARY KEY (entity_id, build_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_identity_active_strong
ON identity_claims(claim_type, claim_value)
WHERE active=1 AND strength='strong';

CREATE UNIQUE INDEX IF NOT EXISTS ux_field_selected_scalar
ON field_claims(build_id, entity_id, field_name)
WHERE selected=1 AND field_name NOT IN ('research_areas','publications');

CREATE INDEX IF NOT EXISTS ix_identity_entity_active
ON identity_claims(entity_id, active, claim_type);

CREATE INDEX IF NOT EXISTS ix_entity_observations_entity_build
ON entity_observations(entity_id, build_id, observation_id);

CREATE INDEX IF NOT EXISTS ix_field_claims_entity_build
ON field_claims(entity_id, build_id, field_name, selected);

CREATE INDEX IF NOT EXISTS ix_canonical_build_active
ON canonical_professors(build_id, active, entity_id);
"""


__all__ = [
    "BUILD_STATUSES",
    "BuildSource",
    "CATALOG_SCHEMA_VERSION",
    "CURATION_SCHEMA_SQL",
    "SCHEMA_SQL",
    "SOURCE_TASK_STATUSES",
]
