"""URL normalization and site comparison (spec §4). Pure stdlib.

normalize_url preserves the query string VERBATIM (no param reordering): this is
deliberate, because form-pagination synthetic URLs (__ycl_* params, see SP3 spec
§5.3) must round-trip byte-identically so they map to one stable graph node.
(SP2's dedup.py docstring mentions "param-sorted"; that was aspirational and is
intentionally NOT implemented here.)
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit, urlunsplit

from dext.url_policy import has_explicit_port, has_explicit_port_parts

# Common multi-label public suffixes (KISS, no public-suffix-list dependency).
_MULTI_SUFFIXES = frozenset(
    {
        "edu.cn", "com.cn", "org.cn", "net.cn", "gov.cn", "ac.cn",
        "edu.hk", "edu.tw", "edu.mo", "com.hk", "com.tw",
    }
)


def normalize_url(href: str | None, base_url: str) -> str | None:
    """Resolve `href` against `base_url` into a normalized identity URL, or None.

    Steps: strip ends → urljoin to absolute → reject non-http(s) and any explicit
    host port → lowercase host → strip a trailing path slash (root "/" kept) →
    drop fragment → keep query verbatim.
    """
    if href is None:
        return None
    raw = href.strip()
    if not raw:
        return None
    absolute = urljoin(base_url, raw)
    parts = urlsplit(absolute)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    if has_explicit_port_parts(parts):
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    netloc = host
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _registrable_domain(host: str) -> str:
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return last_two


def same_site(a: str, b: str, *, loose: bool = False) -> bool:
    """Same host (default), or same registrable domain when `loose=True`
    (so subdomains of one university collapse together — spec §4 "宽松可配")."""
    if has_explicit_port(a) or has_explicit_port(b):
        return False
    ha, hb = _host(a), _host(b)
    if not ha or not hb:
        return False
    if loose:
        return _registrable_domain(ha) == _registrable_domain(hb)
    return ha == hb


def is_offsite(url: str, base: str) -> bool:
    """True if `url` is on a different university/site than `base`
    (loose registrable-domain comparison)."""
    return not same_site(url, base, loose=True)
