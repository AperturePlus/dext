"""Versioned, validated experiment assets."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from typing import Any

from dext_graph.models import QueryRecord, ValueValidationError

_REQUIRED_QUERY_CATEGORIES = {"semantic", "bilingual", "abbreviation", "hierarchy", "near_miss"}
_REQUIRED_DISCIPLINES = {"computer_science", "medicine", "engineering"}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(relative_path: str) -> dict[str, Any]:
    path = files("dext_graph").joinpath("assets", *relative_path.split("/"))
    return json.loads(path.read_text(encoding="utf-8"))


def load_queries() -> tuple[str, list[QueryRecord], str]:
    raw = _load_json("query_sets/research_interests_zh_en_v1.json")
    items = raw.get("queries")
    if not isinstance(items, list) or not 30 <= len(items) <= 50:
        raise ValueValidationError("query set must contain 30-50 queries")
    queries: list[QueryRecord] = []
    seen: set[str] = set()
    categories: set[str] = set()
    disciplines: set[str] = set()
    for item in items:
        query_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not query_id or not text or query_id in seen:
            raise ValueValidationError("query IDs and texts must be non-empty and IDs unique")
        seen.add(query_id)
        item_categories = tuple(str(x) for x in item.get("categories", []))
        item_disciplines = tuple(str(x) for x in item.get("disciplines", []))
        categories.update(item_categories)
        disciplines.update(item_disciplines)
        queries.append(QueryRecord(query_id, text, item_categories, item_disciplines))
    if not _REQUIRED_QUERY_CATEGORIES <= categories:
        missing = sorted(_REQUIRED_QUERY_CATEGORIES - categories)
        raise ValueValidationError("query set is missing categories: " + ", ".join(missing))
    if not _REQUIRED_DISCIPLINES <= disciplines:
        missing = sorted(_REQUIRED_DISCIPLINES - disciplines)
        raise ValueValidationError("query set is missing disciplines: " + ", ".join(missing))
    return str(raw["version"]), queries, canonical_hash(raw)


def load_sentinels() -> tuple[str, list[dict[str, str]], str]:
    raw = _load_json("sentinels_v1.json")
    items = raw.get("texts")
    if not isinstance(items, list) or not 3 <= len(items) <= 5:
        raise ValueValidationError("sentinel set must contain 3-5 texts")
    normalized = [{"id": str(item["id"]), "text": str(item["text"])} for item in items]
    return str(raw["version"]), normalized, canonical_hash(raw)


__all__ = ["canonical_hash", "load_queries", "load_sentinels"]
