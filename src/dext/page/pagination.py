"""Pagination & followup discovery (spec §5, source doc §11). Pure functions.

This module has two parts: simple URL/followup heuristics (this task) and the
faithful byte-exact port of userscripts/src/formPagination.ts (Task 7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from dext.page.links import PageSnapshot
from dext.page.urls import has_explicit_port
from dext.types import PaginationState

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


# ── Form pagination — faithful port of userscripts/src/formPagination.ts ──────
_PAGE_ASSIGN_RE = re.compile(
    r"""document\.forms\[['"]([^'"]+)['"]\]\.([A-Za-z0-9_]+)\.value\s*=\s*['"]?(\d+)['"]?""",
    re.I,
)
_GOTO_FIELD_RE = re.compile(r"\b([A-Za-z0-9_]*?)GOPAGE\b", re.I)
_CURRENT_PAGE_PARAMS = ("PAGENUM", "page", "p", "pn", "fromWenNOWPAGE")
# WHATWG application/x-www-form-urlencoded serializer safe set: ASCII alnum + * - . _
_FORM_SAFE = frozenset(
    b"*-._"
    + bytes(range(0x30, 0x3A))  # 0-9
    + bytes(range(0x41, 0x5B))  # A-Z
    + bytes(range(0x61, 0x7B))  # a-z
)


def _quote_form(value: str) -> str:
    out: list[str] = []
    for byte in value.encode("utf-8"):
        if byte == 0x20:
            out.append("+")
        elif byte in _FORM_SAFE:
            out.append(chr(byte))
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def _unquote_form(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value.replace("+", " "), encoding="utf-8", errors="replace")


def _parse_query_pairs(query: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not query:
        return pairs
    for chunk in query.split("&"):
        if chunk == "":
            continue
        if "=" in chunk:
            key, val = chunk.split("=", 1)
        else:
            key, val = chunk, ""
        pairs.append((_unquote_form(key), _unquote_form(val)))
    return pairs


def _build_synthetic_url(url: str, form_name: str, field_name: str, page_index: int) -> str:
    """Byte-identical to formPagination.ts buildSyntheticUrl (see Locked contract)."""
    parts = urlsplit(url)
    pairs = [(k, v) for (k, v) in _parse_query_pairs(parts.query) if not k.startswith("__ycl_")]
    pairs.append(("__ycl_kind", "form"))
    pairs.append(("__ycl_form", form_name))
    pairs.append(("__ycl_field", field_name))
    pairs.append(("__ycl_page", str(page_index)))
    query = "&".join(f"{_quote_form(k)}={_quote_form(v)}" for k, v in pairs)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    netloc = host
    return urlunsplit((scheme, netloc, parts.path, query, ""))


def _detect_current_page(soup: BeautifulSoup, current_url: str) -> int:
    element = soup.select_one(".this-page")
    if element is not None:
        text = element.get_text(strip=True)
        try:
            value = int(text)
            if value > 0:
                return value
        except ValueError:
            pass
    pairs = _parse_query_pairs(urlsplit(current_url).query)
    for key in _CURRENT_PAGE_PARAMS:
        for pair_key, pair_val in pairs:  # searchParams.get → first match
            if pair_key == key:
                try:
                    value = int(pair_val)
                    if value > 0:
                        return value
                except ValueError:
                    pass
                break
    return 1


def _has_goto(soup: BeautifulSoup, form_name: str, field_name: str) -> bool:
    for inp in soup.find_all("input"):
        name = inp.get("name") or ""
        match = _GOTO_FIELD_RE.search(name)
        if not match:
            continue
        prefix = match.group(1) or ""
        owning_form = inp.find_parent("form")
        form_ok = owning_form is None or (owning_form.get("name") or "") == form_name
        prefix_ok = not prefix or field_name.lower().startswith(prefix.lower())
        if form_ok and prefix_ok:
            return True
    return False


def _expand_page_indexes(
    soup: BeautifulSoup, form_name: str, field_name: str, pages: set[int]
) -> list[int]:
    if not _has_goto(soup, form_name, field_name):
        return sorted(pages)
    return list(range(1, max(pages) + 1))


def extract_form_pagination_states(html: str, current_url: str) -> list[PaginationState]:
    """Backend fallback parse of WebPlus/SiteWeaver form pagination (source §11.3).

    Faithful port of formPagination.ts: group javascript: anchors by (form, field),
    optionally expand via a GOPAGE input, skip page 1 / current page, and build a
    byte-exact synthetic identity URL per page.
    """
    if has_explicit_port(current_url):
        return []
    soup = BeautifulSoup(html or "", "html.parser")
    groups: dict[tuple[str, str], set[int]] = {}
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith("javascript:"):  # JS selector a[href^="javascript:"]
            continue
        match = _PAGE_ASSIGN_RE.search(href)
        if not match:
            continue
        page = int(match.group(3))
        if page <= 0:
            continue
        groups.setdefault((match.group(1), match.group(2)), set()).add(page)

    current_page = _detect_current_page(soup, current_url)
    states: list[PaginationState] = []
    seen: set[str] = set()
    for (form_name, field_name), pages in groups.items():
        expanded = _expand_page_indexes(soup, form_name, field_name, pages)
        total_pages = max([*expanded, *pages])
        for page in expanded:
            if page <= 1 or page == current_page:
                continue
            synthetic = _build_synthetic_url(current_url, form_name, field_name, page)
            if synthetic in seen:
                continue
            seen.add(synthetic)
            states.append(
                PaginationState(
                    kind="form_submit",
                    state_id=f"form:{form_name}:{field_name}:{page}",
                    label=f"{form_name} 第 {page} 页",
                    page_index=page,
                    total_pages=total_pages,
                    form_name=form_name,
                    fields={field_name: str(page)},
                    submit=True,
                    synthetic_url=synthetic,
                    url=current_url,
                )
            )
    states.sort(key=lambda s: (s.page_index, s.synthetic_url))
    return states


def merge_pagination_states(
    reported: list[PaginationState], parsed: list[PaginationState]
) -> list[PaginationState]:
    """Union of script-reported and backend-parsed states, de-duplicated by
    synthetic_url (reported wins — it reflects the live DOM). Sorted like
    extract_form_pagination_states output."""
    out: list[PaginationState] = []
    seen: set[str] = set()
    for state in [*reported, *parsed]:
        if state.synthetic_url in seen:
            continue
        seen.add(state.synthetic_url)
        out.append(state)
    out.sort(key=lambda s: (s.page_index, s.synthetic_url))
    return out
