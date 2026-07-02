"""Pure source-URL canonicalization for R4 detail provenance (R4b §2, §4).

Strip tracking params + fragment, lowercase scheme/host, dedupe stably. The
canonical URL is the value surfaced in ProfessorDetail.source_urls; the
original raw URL is never used as a dedup key.
"""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "from",
})


def canonicalize_source_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(str(url))
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, parts.path, query, ""))  # drop fragment


def dedupe_source_urls(urls: Iterable[str | None]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        canon = canonicalize_source_url(raw)
        if canon is None or canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return tuple(out)


__all__ = ["canonicalize_source_url", "dedupe_source_urls"]
