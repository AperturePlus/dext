"""Pagination & followup discovery (spec §5, source doc §11). Pure functions.

This module has two parts: simple URL/followup heuristics (this task) and the
faithful byte-exact port of userscripts/src/formPagination.ts (Task 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from dext.page.links import PageSnapshot

# Numeric-page patterns (first match wins). Examples: szdw/2.htm, index_3.html,
# list.htm?page=2.
_PAGE_INDEX_PATTERNS = (
    re.compile(r"[?&](?:page|p|pn|pageno|pagenum|curpage)=(\d+)\b", re.I),
    re.compile(r"index_(\d+)\.html?$", re.I),
    re.compile(r"_(\d+)\.html?$", re.I),
    re.compile(r"/(\d+)\.html?$", re.I),
)

# Conservative faculty-category keywords (anchor text) for followup pages.
_FOLLOWUP_KEYWORDS = (
    "教授", "副教授", "讲师", "助理教授", "研究员", "副研究员",
    "博士生导师", "硕士生导师", "博导", "硕导", "院士",
    "杰出人才", "特聘", "讲席", "专职教师", "兼职教师", "青年教师",
)

# Category anchors that are really the list page header, not a sub-page.
_FOLLOWUP_NEGATIVE = ("师资队伍", "教师名单", "师资力量", "全部")


@dataclass
class PaginationCandidate:
    url: str
    page_index: int | None
    label: str | None


@dataclass
class FollowupCandidate:
    url: str
    label: str


def _page_index_of(url: str) -> int | None:
    for pattern in _PAGE_INDEX_PATTERNS:
        match = pattern.search(url)
        if match:
            return int(match.group(1))
    return None


def _column_dir(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    directory = parts.path.rsplit("/", 1)[0] + "/"
    return (parts.hostname or "").lower(), directory


def find_url_pagination(snapshot: PageSnapshot, faculty_list_url: str) -> list[PaginationCandidate]:
    """Same-site, same-column links that look like numbered pages (source §11.1)."""
    base_host, base_dir = _column_dir(faculty_list_url)
    out: list[PaginationCandidate] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        parts = urlsplit(sig.url)
        if (parts.hostname or "").lower() != base_host:
            continue
        if not parts.path.startswith(base_dir):
            continue
        index = _page_index_of(sig.url)
        if index is None:
            continue
        if sig.url in seen:
            continue
        seen.add(sig.url)
        out.append(PaginationCandidate(url=sig.url, page_index=index, label=sig.anchor_text or None))
    return out


def find_followup_links(
    snapshot: PageSnapshot, faculty_list_url: str, limit: int = 36
) -> list[FollowupCandidate]:
    """Same-site faculty-category entries (source §11.2); capped at `limit`."""
    base_host = (urlsplit(faculty_list_url).hostname or "").lower()
    out: list[FollowupCandidate] = []
    seen: set[str] = set()
    for sig in snapshot.link_signals:
        if (urlsplit(sig.url).hostname or "").lower() != base_host:
            continue
        if sig.url == faculty_list_url:
            continue
        text = sig.anchor_text or ""
        if any(neg in text for neg in _FOLLOWUP_NEGATIVE):
            continue
        if not any(kw in text for kw in _FOLLOWUP_KEYWORDS):
            continue
        if sig.url in seen:
            continue
        seen.add(sig.url)
        out.append(FollowupCandidate(url=sig.url, label=text))
        if len(out) >= limit:
            break
    return out
