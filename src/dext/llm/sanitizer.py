"""Normalize raw LLM field dicts into ProfessorPayload (spec §5).

The contract between extraction and DB: SP2's save_professors assumes its
input is already sanitized. Never fabricate — unknown fields become None.
name_key is NOT produced here (no payload field; SP2 recomputes it).
"""

from __future__ import annotations

import re

from dext.types import ProfessorPayload

_WS = re.compile(r"\s+")
_SEP = re.compile(r"[;；,，、\n]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HTTP = re.compile(r"^https?://", re.IGNORECASE)
_MULTI_JOIN = "；"


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v)
    s = _WS.sub(" ", str(v)).strip()
    return s or None


def _multi(v) -> str | None:
    if v is None:
        return None
    items = v if isinstance(v, (list, tuple)) else _SEP.split(str(v))
    out: list[str] = []
    seen: set[str] = set()
    for it in items:
        s = _WS.sub(" ", str(it)).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return _MULTI_JOIN.join(out) if out else None


def _email(v) -> str | None:
    s = _text(v)
    return s if s and _EMAIL.match(s) else None


def _url(v) -> str | None:
    s = _text(v)
    return s if s and _HTTP.match(s) else None


def sanitize(raw: dict) -> ProfessorPayload | None:
    """Normalize one raw record. Returns None when there is no usable name."""
    name = _text(raw.get("name"))
    if not name:
        return None
    return ProfessorPayload(
        name=name,
        title=_text(raw.get("title")),
        research_areas=_multi(raw.get("research_areas")),
        email=_email(raw.get("email")),
        phone=_text(raw.get("phone")),
        homepage=_url(raw.get("homepage")),
        external_link=_url(raw.get("external_link")),
        bio=_text(raw.get("bio")),
        enrollment_pref=_text(raw.get("enrollment_pref")),
        publications=_multi(raw.get("publications")),
    )
