"""City preference normalization shared by recall filters and adapters."""
from __future__ import annotations

_MUNICIPALITIES = {"北京", "上海", "天津", "重庆"}
_ALIASES = {
    "北京市": "北京",
    "上海市": "上海",
    "天津市": "天津",
    "重庆市": "重庆",
}


def normalize_city_name(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    alias = _ALIASES.get(text)
    if alias is not None:
        return alias
    if text.endswith("市") and text[:-1] in _MUNICIPALITIES:
        return text[:-1]
    return text


def normalize_city_names(values: tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        normalized = normalize_city_name(value)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def city_name_variants(value: object) -> tuple[str, ...]:
    normalized = normalize_city_name(value)
    if normalized is None:
        return ()
    variants = [normalized]
    original = str(value or "").strip()
    if original and original not in variants:
        variants.append(original)
    if normalized in _MUNICIPALITIES:
        suffixed = normalized + "市"
        if suffixed not in variants:
            variants.append(suffixed)
    return tuple(variants)


def expand_city_names(values: tuple[str, ...] | list[str] | set[str]) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        for variant in city_name_variants(value):
            if variant not in out:
                out.append(variant)
    return tuple(out)


def city_name_matches(candidate: object, requested: tuple[str, ...]) -> bool | None:
    if not requested:
        return True
    normalized_candidate = normalize_city_name(candidate)
    if normalized_candidate is None:
        return None
    requested_normalized = normalize_city_names(requested)
    return normalized_candidate in requested_normalized


__all__ = [
    "city_name_matches",
    "city_name_variants",
    "expand_city_names",
    "normalize_city_name",
    "normalize_city_names",
]
