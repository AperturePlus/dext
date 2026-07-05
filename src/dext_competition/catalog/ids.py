"""Stable competition identity and exact alias resolution."""
from __future__ import annotations

import re
import unicodedata
import uuid


_SPACE_RE = re.compile(r"\s+")


def normalize_competition_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _SPACE_RE.sub(" ", normalized).strip()
    return "".join(ch.lower() if ch.isascii() else ch for ch in normalized)


def competition_id(display_name: str, *, prefix: str = "dext/competition/v1/") -> str:
    key = normalize_competition_name(display_name)
    if not key:
        raise ValueError("competition display name must not be empty")
    return "cmp_" + uuid.uuid5(uuid.NAMESPACE_URL, prefix + key).hex


def build_alias_index(
    names: tuple[str, ...], aliases: dict[str, tuple[str, ...]],
) -> dict[str, str]:
    known = set(names)
    index: dict[str, str] = {}
    for name in names:
        for candidate in (name, *aliases.get(name, ())):
            key = normalize_competition_name(candidate)
            previous = index.get(key)
            if previous is not None and previous != name:
                raise ValueError(f"ambiguous competition alias: {candidate!r}")
            index[key] = name
    unknown = sorted(set(aliases) - known)
    if unknown:
        raise ValueError("aliases reference unknown competitions: " + ", ".join(unknown))
    return index


__all__ = ["build_alias_index", "competition_id", "normalize_competition_name"]
