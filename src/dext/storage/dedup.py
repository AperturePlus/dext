"""Dedup / upsert: stable node_key generation, conservative name_key, and the
save_professors merge logic (source doc §9/§14, spec §6).

Known, accepted limitations (recorded, not fixed): same-college same-name
records may falsely merge; the same person written differently (English name,
former name, traditional/simplified, ethnic-name separator dots) may fail to
merge. name_key is a normalized DISPLAY name, not a strong identity key.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from dext.storage.models import NodeType

# Decorative separator dots removed before comparison (ethnic-name middots etc.).
_DECORATIVE_DOTS = dict.fromkeys(map(ord, "·•∙・･‧"), None)


def node_key_for(
    node_type: NodeType,
    *,
    normalized_url: str | None = None,
    org_unit_id: int | str | None = None,
    normalized_name: str | None = None,
) -> str:
    """Stable, globally-unique work-unit key (source doc §13.3).

    - org_unit:  ``org_unit:id:<id>`` (preferred) else ``org_unit:name:<normalized_name>``
    - URL nodes: ``<type>:org:<org_unit_id|none>:url:<normalized_url>``

    `normalized_url` is produced by SP3 (fragment-stripped, param-sorted; the
    synthetic/identity URL for form pagination, which makes each page distinct).
    """
    if node_type == NodeType.org_unit:
        if org_unit_id is not None:
            return f"org_unit:id:{org_unit_id}"
        if normalized_name:
            return f"org_unit:name:{normalized_name}"
        raise ValueError("org_unit node_key needs org_unit_id or normalized_name")
    if not normalized_url:
        raise ValueError(f"{node_type} node_key needs normalized_url")
    owner = org_unit_id if org_unit_id is not None else "none"
    return f"{node_type.value}:org:{owner}:url:{normalized_url}"


def name_key(name: str) -> str:
    """Conservative, explainable comparison key (spec §6): strip ends → NFKC
    (full-width → half-width) → drop decorative dots → casefold. No pinyin /
    English-name mapping, and internal whitespace is preserved by design."""
    s = name.strip()
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_DECORATIVE_DOTS)
    return s.casefold()


def looks_like_academician(title: str | None) -> bool:
    """Route to the academicians table when the title marks an academician."""
    return bool(title) and "院士" in title


@dataclass
class SaveResult:
    inserted: int = 0
    updated: int = 0
    affiliations_added: int = 0
    academicians: int = 0
    save_errors: int = 0
