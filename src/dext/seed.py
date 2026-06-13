"""Seed manifest models + loading.

Mirrors the two entry shapes in entrances.yaml (source doc §2). Pure: reads
YAML/env only, no normalization (URL normalization belongs to SP3).
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, model_validator


class SeedError(Exception):
    """Seed manifest is missing/malformed, or a requested university is absent."""


class OrgUnitSeed(BaseModel):
    name: str
    # Known kinds: college/department/institute/hospital; any string allowed.
    kind: str = "college"
    url: str | None = None
    faculty_urls: list[str] = []


class UniversitySeed(BaseModel):
    name: str
    url: str  # official site; abbr is derived from this host
    location: str | None = None
    abbr: str | None = None  # optional explicit override
    org_unit_listing_urls: list[str] = []
    org_units: list[OrgUnitSeed] = []

    @model_validator(mode="after")
    def _at_least_one_entry(self) -> "UniversitySeed":
        has_listing = bool(self.org_unit_listing_urls)
        has_unit_url = any(u.url for u in self.org_units)
        has_faculty = any(u.faculty_urls for u in self.org_units)
        if not (has_listing or has_unit_url or has_faculty):
            raise ValueError(
                f"university {self.name!r} has no entry point: needs at least one of "
                "org_unit_listing_urls, org_units[].url, or org_units[].faculty_urls"
            )
        return self


class Manifest(BaseModel):
    version: int = 1
    universities: list[UniversitySeed]


_ASSETS_FALLBACK = Path("assets/entrances.yaml")


def load_manifest(path: Path | None = None) -> Manifest:
    """Load and validate the seed manifest.

    path=None -> settings.seed_path, falling back to assets/entrances.yaml.
    Raises SeedError (with an actionable message) on missing file, bad YAML,
    or schema violation.
    """
    if path is None:
        from dext.config import get_settings

        configured = get_settings().seed_path
        path = configured if configured.exists() else _ASSETS_FALLBACK

    path = Path(path)
    if not path.exists():
        raise SeedError(
            f"seed manifest not found at {path} (and no {_ASSETS_FALLBACK} fallback)"
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SeedError(f"failed to parse seed YAML at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise SeedError(
            f"seed manifest at {path} must be a mapping with a 'universities' list"
        )

    try:
        return Manifest.model_validate(raw)
    except ValidationError as exc:
        raise SeedError(f"invalid seed manifest at {path}:\n{exc}") from exc


def get_university(manifest: Manifest, name: str) -> UniversitySeed:
    """Return the university with an exact name match, else raise SeedError."""
    for university in manifest.universities:
        if university.name == name:
            return university
    available = ", ".join(u.name for u in manifest.universities)
    raise SeedError(f"university {name!r} not found in seed. Available: {available}")


# Longest-first public-suffix set. We deliberately avoid tldextract (YAGNI):
# 38 universities resolve uniquely; on a future collision, set seed `abbr`.
_PUBLIC_SUFFIXES = sorted(
    ["edu.cn", "ac.cn", "edu", "com", "org", "net", "cn"],
    key=lambda s: s.count(".") + 1,
    reverse=True,
)


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    return re.sub(r"-+", "-", value).strip("-")


def _label_left_of_suffix(host: str) -> str:
    """Return the label immediately left of the longest matching public suffix.

    'buaa.edu.cn' -> 'buaa'; 'pku.edu.cn' -> 'pku'. Falls back to the first
    label if no known suffix matches.
    """
    labels = host.split(".")
    for suffix in _PUBLIC_SUFFIXES:
        suffix_labels = suffix.split(".")
        n = len(suffix_labels)
        if len(labels) > n and labels[-n:] == suffix_labels:
            return labels[-(n + 1)]
    return labels[0] if labels else host


def resolve_abbr(university: UniversitySeed) -> str:
    """Stable, readable, lowercase English abbr for the DB filename.

    Uses an explicit seed `abbr` when present; otherwise derives it from the
    official URL's host (strip leading 'www.', take the label left of the
    public suffix). Result is slugified to [a-z0-9-].
    """
    if university.abbr:
        return _slugify(university.abbr)
    host = (urllib.parse.urlsplit(university.url).hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www.") :]
    return _slugify(_label_left_of_suffix(host))


def db_filename(abbr: str) -> str:
    """DB file name for an abbr (path assembly lives in SP2)."""
    return f"{abbr}.db"
