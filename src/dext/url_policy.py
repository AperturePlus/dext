"""Shared URL policy helpers with no crawler-layer dependencies."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


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
