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

from sqlalchemy import select

from dext.storage.models import (
    Academician,
    ExtractionFailure,
    NodeType,
    Professor,
    ProfessorAffiliation,
)
from dext.types import ProfessorPayload

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


# --- save_professors (session-based dedup; source doc 14.1) ---

# Fields back-filled on merge (only when the existing value is empty).
_FILLABLE = (
    "title", "research_areas", "email", "phone",
    "homepage", "external_link", "bio", "enrollment_pref", "publications",
)


def _merge_fill(prof: Professor, p: ProfessorPayload) -> bool:
    changed = False
    for f in _FILLABLE:
        cur = getattr(prof, f)
        new = getattr(p, f, None)
        if (cur is None or cur == "") and new:
            setattr(prof, f, new)
            changed = True
    return changed


async def _ensure_affiliation(session, professor_id: int, org_unit_id: int, result: SaveResult) -> None:
    exists = (
        await session.execute(
            select(ProfessorAffiliation).where(
                ProfessorAffiliation.professor_id == professor_id,
                ProfessorAffiliation.org_unit_id == org_unit_id,
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(ProfessorAffiliation(professor_id=professor_id, org_unit_id=org_unit_id))
        await session.flush()
        result.affiliations_added += 1


async def _save_academician(session, p, nk, org_unit_id, result: SaveResult) -> None:
    existing = (
        await session.execute(
            select(Academician).where(Academician.org_unit_id == org_unit_id, Academician.name_key == nk)
        )
    ).scalar_one_or_none()
    if existing is None and p.homepage:
        existing = (
            await session.execute(
                select(Academician).where(Academician.org_unit_id == org_unit_id, Academician.homepage == p.homepage)
            )
        ).scalar_one_or_none()
    if existing is None and p.external_link:
        existing = (
            await session.execute(
                select(Academician).where(
                    Academician.org_unit_id == org_unit_id, Academician.external_link == p.external_link
                )
            )
        ).scalar_one_or_none()
    if existing is not None:
        if not existing.title and p.title:
            existing.title = p.title
        if not existing.homepage and p.homepage:
            existing.homepage = p.homepage
        if not existing.external_link and p.external_link:
            existing.external_link = p.external_link
        await session.flush()
        result.updated += 1
        return
    session.add(Academician(
        name=p.name, name_key=nk, org_unit_id=org_unit_id,
        title=p.title, homepage=p.homepage, external_link=p.external_link,
    ))
    await session.flush()
    result.academicians += 1


async def save_professors(session, payloads, *, org_unit_id: int, org_unit_name: str) -> SaveResult:
    """Upsert a batch of extracted professors with dedup. Same-college same
    name_key -> merge; else cross-college email->homepage->external_link match ->
    add affiliation; else insert. Academicians (title contains '院士') route to the
    academicians table. Each payload runs in a SAVEPOINT so one bad record is
    recorded + counted (save_errors) without aborting the batch."""
    result = SaveResult()
    for p in payloads:
        try:
            async with session.begin_nested():
                nk = name_key(p.name)
                if looks_like_academician(p.title):
                    await _save_academician(session, p, nk, org_unit_id, result)
                    continue

                # 1. same college, same name_key (professors carry no name_key column).
                affiliated = (
                    await session.execute(
                        select(Professor)
                        .join(ProfessorAffiliation, ProfessorAffiliation.professor_id == Professor.id)
                        .where(ProfessorAffiliation.org_unit_id == org_unit_id)
                    )
                ).scalars().all()
                match = next((c for c in affiliated if name_key(c.name) == nk), None)

                # 2. cross-college exact match: email -> homepage -> external_link.
                if match is None and p.email:
                    match = (await session.execute(select(Professor).where(Professor.email == p.email))).scalars().first()
                if match is None and p.homepage:
                    match = (await session.execute(select(Professor).where(Professor.homepage == p.homepage))).scalars().first()
                if match is None and p.external_link:
                    match = (await session.execute(select(Professor).where(Professor.external_link == p.external_link))).scalars().first()

                if match is not None:
                    await _ensure_affiliation(session, match.id, org_unit_id, result)
                    if _merge_fill(match, p):
                        await session.flush()
                        result.updated += 1
                    continue

                # 3. new professor + first affiliation.
                prof = Professor(
                    name=p.name, org_unit_name=org_unit_name, title=p.title,
                    research_areas=p.research_areas, email=p.email, phone=p.phone,
                    homepage=p.homepage, external_link=p.external_link, bio=p.bio,
                    enrollment_pref=p.enrollment_pref, publications=p.publications,
                )
                session.add(prof)
                await session.flush()
                await _ensure_affiliation(session, prof.id, org_unit_id, result)
                result.inserted += 1
        except Exception:  # noqa: BLE001 -- record + count, never abort the batch
            result.save_errors += 1
            session.add(ExtractionFailure(
                failure_type="save_error", resolver="dropped",
                raw_arguments_preview=repr(p)[:500],
                professor_name_hint=getattr(p, "name", None),
                source_url=getattr(p, "homepage", None),
            ))
            await session.flush()
    return result
