"""ORM models, shared enums, and the declarative Base for dext storage.

Schema mirrors source doc §13 (table-by-table) and §5 (graph enums). Enums are
StrEnum so members are plain strings that persist directly into String columns
and compare equal to their value (e.g. NodeStatus.pending == "pending").
"""

from __future__ import annotations

from enum import StrEnum


class NodeType(StrEnum):
    org_listing_url = "org_listing_url"
    org_unit = "org_unit"
    faculty_list_url = "faculty_list_url"
    pagination_url = "pagination_url"
    faculty_followup_url = "faculty_followup_url"
    detail_url = "detail_url"


class EdgeType(StrEnum):
    seeded_from_manifest = "seeded_from_manifest"
    discovered_on_page = "discovered_on_page"
    belongs_to_org_unit = "belongs_to_org_unit"
    pagination_of = "pagination_of"
    detail_candidate_of = "detail_candidate_of"
    blocked_by = "blocked_by"


class NodeStatus(StrEnum):
    pending = "pending"
    in_progress = "in_progress"
    retry = "retry"
    done = "done"
    failed = "failed"
    skipped = "skipped"


from datetime import datetime, timezone

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

SCHEMA_VERSION = 1


def utcnow_iso() -> str:
    """UTC timestamp as ISO-8601 text. Uniform format → lexicographic ordering
    matches chronological ordering (used for next_retry_at comparisons)."""
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    updated_at: Mapped[str] = mapped_column(
        String, default=utcnow_iso, onupdate=utcnow_iso
    )


class UniversityMeta(TimestampMixin, Base):
    __tablename__ = "university_meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    start_url: Mapped[str | None] = mapped_column(String)
    location: Mapped[str | None] = mapped_column(String)
    crawl_status: Mapped[str] = mapped_column(String, default="pending")  # pending|in_progress|completed|failed
    abbr: Mapped[str | None] = mapped_column(String)
    schema_version: Mapped[int] = mapped_column(Integer, default=SCHEMA_VERSION)
    last_run_id: Mapped[int | None] = mapped_column(Integer)


class CrawlRun(TimestampMixin, Base):
    __tablename__ = "crawl_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # fresh|resume
    started_at: Mapped[str | None] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="running")  # running|completed|failed|cancelled
    backup_path: Mapped[str | None] = mapped_column(String)
    settings_json: Mapped[dict | None] = mapped_column(JSON)
    summary_json: Mapped[dict | None] = mapped_column(JSON)


class OrgUnit(TimestampMixin, Base):
    __tablename__ = "org_units"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # synthetic "about:org_unit:<slug>" when the college has no homepage (NOT NULL + unique)
    url: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    kind: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|in_progress|completed|failed|no_faculty_page
    discovered_from_url: Mapped[str | None] = mapped_column(String)


class GraphNode(TimestampMixin, Base):
    __tablename__ = "crawl_graph_nodes"
    __table_args__ = (
        Index("ix_nodes_status_priority", "status", "priority_score"),
        Index("ix_nodes_type", "type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_key: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # NodeType value
    url: Mapped[str] = mapped_column(String, nullable=False)
    org_unit_id: Mapped[int | None] = mapped_column(ForeignKey("org_units.id"))
    org_unit_name: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)  # NodeStatus value
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    base_priority: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float | None] = mapped_column(Float)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(Integer)
    claimed_at: Mapped[str | None] = mapped_column(String)
    completed_at: Mapped[str | None] = mapped_column(String)
    next_retry_at: Mapped[str | None] = mapped_column(String)
    content_hash: Mapped[str | None] = mapped_column(String)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class GraphEdge(TimestampMixin, Base):
    __tablename__ = "crawl_graph_edges"
    __table_args__ = (
        UniqueConstraint("from_node_id", "to_node_id", "edge_type"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"), nullable=False)
    to_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"), nullable=False)
    edge_type: Mapped[str] = mapped_column(String, nullable=False)  # EdgeType value
    confidence: Mapped[float | None] = mapped_column(Float)
    metadata_json: Mapped[dict | None] = mapped_column(JSON)


class PageCache(TimestampMixin, Base):
    __tablename__ = "crawl_page_cache"
    # PK is the identity URL (synthetic URL for form pagination) — upsert by url.
    url: Mapped[str] = mapped_column(String, primary_key=True)
    final_url: Mapped[str | None] = mapped_column(String)
    status_code: Mapped[int | None] = mapped_column(Integer)
    text_snapshot: Mapped[str | None] = mapped_column(Text)
    links_json: Mapped[list | None] = mapped_column(JSON)
    link_signals_json: Mapped[list | None] = mapped_column(JSON)
    block_reason: Mapped[str | None] = mapped_column(String)
    html_snapshot: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String)
    snapshot_encoding: Mapped[str] = mapped_column(String, default="utf-8")
    title: Mapped[str | None] = mapped_column(String)
    fetch_action_json: Mapped[dict | None] = mapped_column(JSON)


class ExtractionAttempt(Base):
    __tablename__ = "crawl_extraction_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_node_id: Mapped[int] = mapped_column(ForeignKey("crawl_graph_nodes.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="running")  # running|succeeded|retry|failed|skipped
    prompt_hash: Mapped[str | None] = mapped_column(String)
    input_cache_url: Mapped[str | None] = mapped_column(String)
    raw_output_preview: Mapped[str | None] = mapped_column(Text)
    failure_type: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
    finished_at: Mapped[str | None] = mapped_column(String)


class ExtractionFailure(Base):
    __tablename__ = "crawl_extraction_failures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    failure_type: Mapped[str | None] = mapped_column(String)
    resolver: Mapped[str | None] = mapped_column(String)  # retry|dropped|manual
    raw_arguments_preview: Mapped[str | None] = mapped_column(Text)
    professor_name_hint: Mapped[str | None] = mapped_column(String)
    source_url: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)


class Professor(TimestampMixin, Base):
    __tablename__ = "professors"
    # No (org_unit_id, name_key) hard constraint by design (spec §3.9): dedup is
    # code-driven (dedup.save_professors); college membership lives in affiliations.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)  # normalized display name
    org_unit_name: Mapped[str | None] = mapped_column(String)  # redundant merged-college display
    title: Mapped[str | None] = mapped_column(String)
    research_areas: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String)
    phone: Mapped[str | None] = mapped_column(String)
    homepage: Mapped[str | None] = mapped_column(String)
    external_link: Mapped[str | None] = mapped_column(String)
    bio: Mapped[str | None] = mapped_column(Text)
    enrollment_pref: Mapped[str | None] = mapped_column(String)
    publications: Mapped[str | None] = mapped_column(Text)


class Academician(TimestampMixin, Base):
    __tablename__ = "academicians"
    __table_args__ = (
        UniqueConstraint("name", "org_unit_id"),
        UniqueConstraint("org_unit_id", "name_key"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    name_key: Mapped[str] = mapped_column(String, nullable=False)
    org_unit_id: Mapped[int] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    homepage: Mapped[str | None] = mapped_column(String)
    external_link: Mapped[str | None] = mapped_column(String)


class ProfessorAffiliation(Base):
    __tablename__ = "professor_affiliations"
    __table_args__ = (
        UniqueConstraint("professor_id", "org_unit_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    professor_id: Mapped[int] = mapped_column(ForeignKey("professors.id"), nullable=False)
    org_unit_id: Mapped[int] = mapped_column(ForeignKey("org_units.id"), nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=utcnow_iso)
