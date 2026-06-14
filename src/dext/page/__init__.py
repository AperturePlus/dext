"""dext page processing — pure HTML/URL → structured signals.

No network, no DB, no LLM, no global state (spec §1, overview §7).
Public interface (spec §7) re-exported below.
"""

from dext.page.candidates import FilterContext, FilterResult, filter_detail_candidates
from dext.page.links import LinkSignal, PageSnapshot, build_snapshot
from dext.page.pagination import (
    FollowupCandidate,
    PaginationCandidate,
    extract_form_pagination_states,
    find_followup_links,
    find_url_pagination,
    merge_pagination_states,
)
from dext.page.text import content_hash, html_to_text
from dext.page.urls import is_offsite, normalize_url, same_site

__all__ = [
    "build_snapshot",
    "html_to_text",
    "content_hash",
    "normalize_url",
    "same_site",
    "is_offsite",
    "PageSnapshot",
    "LinkSignal",
    "find_url_pagination",
    "find_followup_links",
    "extract_form_pagination_states",
    "merge_pagination_states",
    "PaginationCandidate",
    "FollowupCandidate",
    "filter_detail_candidates",
    "FilterContext",
    "FilterResult",
]
