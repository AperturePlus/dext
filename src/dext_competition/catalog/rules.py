"""Versioned deterministic C2 extraction rules."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class CatalogRules:
    version: str
    identity_prefix: str
    freshness_sensitive_fields: frozenset[str]
    freshness_notice: str
    school_notice: str
    category_keywords: tuple[tuple[str, tuple[str, ...]], ...]
    aliases: dict[str, tuple[str, ...]]
    enrichment_docs: tuple[str, ...]


def parse_catalog_rules(raw: dict[str, Any]) -> CatalogRules:
    required = {
        "version", "identity_prefix", "freshness_sensitive_fields",
        "review_notices", "category_keywords", "aliases", "enrichment_docs",
    }
    missing = required - raw.keys()
    if missing:
        raise ValueError("catalog rules missing keys: " + ", ".join(sorted(missing)))
    notices = raw["review_notices"]
    if not isinstance(notices, dict) or not {"freshness", "school"} <= notices.keys():
        raise ValueError("catalog rules review_notices must contain freshness and school")
    categories = raw["category_keywords"]
    aliases = raw["aliases"]
    if not isinstance(categories, dict) or not isinstance(aliases, dict):
        raise ValueError("catalog rules category_keywords/aliases must be mappings")
    return CatalogRules(
        version=str(raw["version"]),
        identity_prefix=str(raw["identity_prefix"]),
        freshness_sensitive_fields=frozenset(map(str, raw["freshness_sensitive_fields"])),
        freshness_notice=str(notices["freshness"]),
        school_notice=str(notices["school"]),
        category_keywords=tuple(
            (str(category), tuple(map(str, keywords)))
            for category, keywords in categories.items()
        ),
        aliases={str(name): tuple(map(str, values)) for name, values in aliases.items()},
        enrichment_docs=tuple(map(str, raw["enrichment_docs"])),
    )


@lru_cache(maxsize=1)
def load_catalog_rules() -> CatalogRules:
    path = files("dext_competition.catalog").joinpath("rules", "catalog_v1.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("catalog rules must be a mapping")
    return parse_catalog_rules(raw)


__all__ = ["CatalogRules", "load_catalog_rules", "parse_catalog_rules"]
