"""Shared URL policy helpers with no crawler-layer dependencies."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit

ALLOWED_FETCH_HOST_SUFFIXES = ("edu.cn", "github.io")


def is_allowed_fetch_host(url: str) -> bool:
    """True when an http(s) URL's host is on the fetch allowlist.

    Non-http schemes (e.g. ``about:org_unit:...`` and bare test strings) are
    never browser fetches, so they pass through unchanged. Dot-boundary checks
    ensure ``notedu.cn`` / ``evilgithub.io`` are rejected.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return True
    host = (parts.hostname or "").lower()
    if not host:
        return False
    return (
        host == "edu.cn" or host.endswith(".edu.cn")
        or host == "github.io" or host.endswith(".github.io")
    )


def has_explicit_port(url: str) -> bool:
    """True when the URL authority contains an explicit host port.

    Default ports (:80/:443) and malformed ports are treated the same way: if a
    port marker is present in the authority, the crawler should reject the URL.
    """
    try:
        return has_explicit_port_parts(urlsplit(url))
    except ValueError:
        return False


def has_explicit_port_parts(parts: SplitResult) -> bool:
    hostport = parts.netloc.rsplit("@", 1)[-1]
    if not hostport:
        return False
    if hostport.startswith("["):
        closing = hostport.find("]")
        return closing != -1 and hostport[closing + 1 :].startswith(":")
    return ":" in hostport
