"""DOM → PageSnapshot using BeautifulSoup4 (html.parser backend), spec §2/§3.

bs4 gives robust navigation (find_previous for the nearest heading, find_parent
for ancestor class) on the malformed HTML common to university sites — more
reliable and readable than a hand-rolled tag-stack parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from dext.page.text import content_hash, html_to_text
from dext.page.urls import normalize_url, same_site

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@dataclass
class LinkSignal:
    href: str  # raw href as written in the HTML
    url: str  # normalized absolute identity URL
    anchor_text: str
    heading: str | None  # nearest preceding/ancestor heading text
    parent_class: str | None  # class of the nearest ancestor that has one
    path_segments: list[str]
    same_site: bool  # same host as the page's base URL


@dataclass
class PageSnapshot:
    url: str  # normalized identity URL (from requested_url)
    final_url: str
    title: str
    text_snapshot: str
    links: list[str]  # normalized absolute URLs, de-duplicated, in document order
    link_signals: list[LinkSignal]  # one per usable anchor (duplicates kept)
    content_hash: str


def _nearest_heading(anchor) -> str | None:
    prev = anchor.find_previous(_HEADING_TAGS)
    if prev is None:
        return None
    text = prev.get_text(" ", strip=True)
    return text or None


def _parent_class(anchor) -> str | None:
    node = anchor.parent
    while node is not None and getattr(node, "name", None) is not None:
        classes = node.get("class") if hasattr(node, "get") else None
        if classes:
            return " ".join(classes)
        node = node.parent
    return None


def _path_segments(url: str) -> list[str]:
    return [seg for seg in urlsplit(url).path.split("/") if seg]


def build_snapshot(html: str, requested_url: str, final_url: str, title: str) -> PageSnapshot:
    soup = BeautifulSoup(html or "", "html.parser")
    base = final_url or requested_url
    identity = normalize_url(requested_url, requested_url) or requested_url

    page_title = title
    if not page_title and soup.title and soup.title.string:
        page_title = soup.title.string.strip()

    signals: list[LinkSignal] = []
    links: list[str] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href:
            continue
        url = normalize_url(href, base)
        if url is None:
            continue
        signals.append(
            LinkSignal(
                href=href,
                url=url,
                anchor_text=anchor.get_text(" ", strip=True),
                heading=_nearest_heading(anchor),
                parent_class=_parent_class(anchor),
                path_segments=_path_segments(url),
                same_site=same_site(url, base),
            )
        )
        if url not in seen:
            seen.add(url)
            links.append(url)

    return PageSnapshot(
        url=identity,
        final_url=final_url or requested_url,
        title=page_title or "",
        text_snapshot=html_to_text(html or ""),
        links=links,
        link_signals=signals,
        content_hash=content_hash(html or ""),
    )
