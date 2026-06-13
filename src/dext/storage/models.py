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
