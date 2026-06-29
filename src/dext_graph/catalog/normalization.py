"""Pure, versioned normalization for catalog curation."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache

from dext_graph.catalog.ids import canonical_source_url

NORMALIZATION_VERSION = "normalization-v1"
_DOTS = dict.fromkeys(map(ord, "·•∙・･‧"), None)
_SPACE = re.compile(r"\s+")
_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MULTI = re.compile(r"[；;、\n]+")
DEFAULT_EMPTY_VALUES = frozenset(
    {"unknown", "未知", "暂无", "无", "null", "none", "n/a", "-", "--"}
)


@lru_cache(maxsize=8)
def _empty_keys(values: tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        unicodedata.normalize("NFKC", item).strip().casefold() for item in values
    )


def normalize_text(value: object, *, empty_values: Iterable[str] = DEFAULT_EMPTY_VALUES) -> str | None:
    if value is None:
        return None
    normalized = _SPACE.sub(" ", unicodedata.normalize("NFKC", str(value)).strip())
    if not normalized or _PUNCT_ONLY.fullmatch(normalized):
        return None
    empties = _empty_keys(tuple(empty_values))
    if normalized.casefold() in empties:
        return None
    return normalized


def normalize_name(value: object, *, empty_values: Iterable[str] = DEFAULT_EMPTY_VALUES) -> str | None:
    text = normalize_text(value, empty_values=empty_values)
    if text is None:
        return None
    key = text.translate(_DOTS).casefold()
    return key or None


def normalize_url(value: object) -> str | None:
    return canonical_source_url(value)


def normalize_email(value: object, *, empty_values: Iterable[str] = DEFAULT_EMPTY_VALUES) -> str | None:
    text = normalize_text(value, empty_values=empty_values)
    if text is None or not _EMAIL.fullmatch(text):
        return None
    local, domain = text.rsplit("@", 1)
    return f"{local}@{domain.casefold()}"


def split_multivalue(value: object, *, empty_values: Iterable[str] = DEFAULT_EMPTY_VALUES) -> tuple[str, ...]:
    text = normalize_text(value, empty_values=empty_values)
    if text is None:
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for part in _MULTI.split(text):
        normalized = normalize_text(part, empty_values=empty_values)
        if normalized is None:
            continue
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


__all__ = [
    "DEFAULT_EMPTY_VALUES",
    "NORMALIZATION_VERSION",
    "normalize_email",
    "normalize_name",
    "normalize_text",
    "normalize_url",
    "split_multivalue",
]
