"""Cross-subproject shared value objects (overview §5).

Enums are DEFINED in dext.storage.models and re-exported here so other layers
(SP3–SP7) import them without depending on the ORM module directly.
ProfessorPayload is the contract SP5's extractor/sanitizer fills and SP2's
save_professors consumes; SP5 may add fields but must not rename these.
"""

from __future__ import annotations

from dataclasses import dataclass

from dext.storage.models import EdgeType, NodeStatus, NodeType

__all__ = [
    "NodeType",
    "EdgeType",
    "NodeStatus",
    "ProfessorPayload",
    "FetchAction",
    "PaginationState",
    "FetchResult",
]


@dataclass
class ProfessorPayload:
    """One extracted professor record (pre-dedup). All fields except name optional."""

    name: str
    title: str | None = None
    research_areas: str | None = None  # multi-value joined by "；"
    email: str | None = None
    phone: str | None = None
    homepage: str | None = None  # on-campus profile URL
    external_link: str | None = None  # external/3rd-party homepage
    bio: str | None = None
    enrollment_pref: str | None = None  # 博导/硕导 etc.
    publications: str | None = None


@dataclass
class FetchAction:
    """Browser action telling the userscript to fill a form field and submit
    (mirrors userscripts/src/types.ts FetchAction). Produced by SP3 form
    pagination, carried on FetchJob/graph node, consumed by SP4/SP6."""

    kind: str  # always "form_submit" for now
    form_name: str | None = None
    fields: dict[str, str] | None = None
    submit: bool | None = None
    synthetic_url: str | None = None
    label: str | None = None
    page_index: int | None = None
    state_id: str | None = None


@dataclass
class PaginationState:
    """One discovered form-pagination state (mirrors userscripts/src/types.ts
    PaginationState). `synthetic_url` is the stable backend identity URL; it must
    be byte-identical to what formPagination.ts computes for the same page."""

    kind: str  # always "form_submit"
    state_id: str  # "form:<NAME>:<FIELD>:<N>"
    label: str
    page_index: int
    form_name: str
    fields: dict[str, str]
    submit: bool
    synthetic_url: str
    url: str
    total_pages: int | None = None


@dataclass
class FetchResult:
    """One browser fetch outcome (SP4 produces → SP6 consumes; overview §5).

    `identity_url` is the cache/node key (= job.identity_url or job.url). `html`/`title`
    are already UTF-8 / mojibake-repaired. `block_reason` is set only on failure
    (waf/timeout/human_failed/human_skip/...)."""

    identity_url: str
    requested_url: str
    final_url: str
    status_code: int | None
    html: str
    title: str
    pagination_states: list[PaginationState]
    block_reason: str | None = None
