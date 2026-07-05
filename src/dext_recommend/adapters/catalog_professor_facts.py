"""CatalogProfessorFactAdapter: implements ProfessorFactPort over the
published catalog SQLite (R4). Two methods: hydrate (batch authority facts)
and get_detail (single-entity full ProfessorDetail + FactBundle).

Never imports dext_graph; reads only the pinned build_id. Contacts are
double-gated and never enter the FactBundle. No cache (R7 decorator if ever).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from dext_grounded import SourceRef
from dext_grounded.fact_bundle import FactBundle

from dext_recommend.adapters._catalog_fact_reader import (
    CatalogProfessorFactReader,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids
from dext_recommend.facts.evidence import (
    build_fact_items, select_publication_rows, select_statement_rows,
)
from dext_recommend.facts.source_urls import (
    canonicalize_source_url, dedupe_source_urls,
)
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactNotFound, ViewerPermissions,
)

if TYPE_CHECKING:
    from dext_recommend.readiness import ActiveBuildSnapshot


def _fact_from_row(row: dict[str, Any]) -> ProfessorFact:
    return ProfessorFact(
        entity_id=str(row["entity_id"]),
        display_name=str(row["display_name"] or ""),
        university=str(row.get("university_name") or row.get("university_id") or ""),
        org_units=tuple(row.get("org_unit_names") or row.get("org_unit_ids") or ()),
        title=str(row.get("title_raw") or row.get("title_family") or ""),
        title_family=str(row.get("title_family") or ""),
        master_eligibility=str(row.get("master_eligibility") or "unknown"),
        phd_eligibility=str(row.get("phd_eligibility") or "unknown"),
        role_status=str(row.get("role_status") or "included"),
        profile_url=row.get("profile_url"),
        profile_hash=row.get("profile_hash"),
        research_summary=(
            str(row["research_areas_text"])
            if row.get("research_areas_text") else None
        ),
        university_id=row.get("university_id"),
        city_name=row.get("city_name"),
        org_unit_ids=tuple(row.get("org_unit_ids") or ()),
        topic_ids=tuple(row.get("topic_ids") or ()),
    )


def _org_unit_names(observations: tuple[Mapping, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for obs in observations:
        payload = obs.get("observation_payload") or {}
        affiliations = payload.get("affiliations") if isinstance(payload, dict) else None
        if isinstance(affiliations, list):
            for item in affiliations:
                if isinstance(item, dict) and item.get("org_unit_name"):
                    name = str(item["org_unit_name"])
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
        elif isinstance(payload, dict) and payload.get("org_unit_name"):
            name = str(payload["org_unit_name"])
            if name not in seen:
                seen.add(name)
                out.append(name)
    return tuple(out)


def _approved_topic_rows(topic_links: tuple[Mapping, ...]) -> tuple[Mapping, ...]:
    out: list[Mapping] = []
    seen: set[str] = set()
    for row in topic_links:
        if str(row.get("review_status")) != "approved":
            continue
        name = str(row.get("canonical_name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(row)
    return tuple(out)


def _finding_codes(findings: tuple[Mapping, ...]) -> tuple[str, ...]:
    return tuple(str(r.get("code")) for r in findings if r.get("code"))


def _source_ref(
    kind: str,
    build_id: str,
    key: str,
    *,
    summary: str = "",
    official_url: str | None = None,
    last_verified: str | None = None,
) -> SourceRef:
    heading_path = (
        f"catalog:entity:{key}:build:{build_id}"
        if kind == "entity"
        else f"catalog:{kind}:{build_id}:{key}"
    )
    return SourceRef(
        doc_path="catalog",
        heading_path=heading_path,
        chunk_hash=f"{kind}:{build_id}:{key}",
        quote_or_summary=summary or heading_path,
        official_url=official_url,
        last_verified=last_verified,
    )


def _source_metadata_by_observation(
    rows: tuple[Mapping, ...],
) -> dict[str, tuple[str | None, str | None]]:
    metadata: dict[str, tuple[str | None, str | None]] = {}
    for row in rows:
        observation_id = str(row.get("observation_id") or "")
        if not observation_id or observation_id in metadata:
            continue
        metadata[observation_id] = (
            canonicalize_source_url(row.get("source_url")),
            str(row["fetched_at"]) if row.get("fetched_at") else None,
        )
    return metadata


def _dedupe_refs_from_facts(facts) -> tuple[SourceRef, ...]:
    refs: list[SourceRef] = []
    seen: set[SourceRef] = set()
    for fact in facts:
        for ref in fact.source_refs:
            if ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
    return tuple(refs)


class CatalogProfessorFactAdapter:
    def __init__(
        self, reader: CatalogProfessorFactReader, *, settings: RecommendSettings,
    ) -> None:
        self._reader = reader
        self._settings = settings

    async def hydrate(
        self, snapshot: ActiveBuildSnapshot, entity_ids: list[str],
    ) -> dict[str, ProfessorFact]:
        if not entity_ids:
            return {}
        ordered = dedupe_entity_ids(entity_ids)
        chunks = chunk_entity_ids(ordered, self._settings.fact_chunk_size)
        out: dict[str, ProfessorFact] = {}
        for chunk in chunks:
            rows = await self._reader.read_fact_rows(snapshot.build_id, list(chunk))
            for row in rows:
                fact = _fact_from_row(dict(row))
                out[fact.entity_id] = fact
        return out

    async def get_detail(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_id: str,
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> ProfessorDetail:
        rows = await self._reader.read_detail_rows(snapshot.build_id, entity_id)
        if rows is None:
            raise ProfessorFactNotFound(entity_id, snapshot.build_id)
        canon = dict(rows.canonical)
        role_status = str(canon.get("role_status") or "included")
        if role_status == "review" and not viewer_permissions.can_view_review:
            raise ProfessorFactNotFound(entity_id, snapshot.build_id)

        build_id = snapshot.build_id
        org_units = _org_unit_names(rows.observations)
        selected_statement_rows = select_statement_rows(rows.statements)
        selected_mention_rows = select_publication_rows(rows.mentions)
        selected_topic_rows = _approved_topic_rows(rows.topic_links)
        statements = tuple(
            str(row.get("normalized_text") or "").strip()
            for row in selected_statement_rows
        )
        mentions = tuple(
            str(row.get("normalized_text") or "").strip()
            for row in selected_mention_rows
        )
        topics = tuple(str(row.get("canonical_name") or "") for row in selected_topic_rows)
        findings = _finding_codes(rows.findings)
        source_urls = dedupe_source_urls(
            str(r.get("source_url")) for r in rows.source_urls
        )
        profile_hash = rows.profile_hash
        role_reasons = tuple(canon.get("role_reason_codes") or ())
        risk_flags = list(role_reasons)
        if profile_hash is None:
            risk_flags.append("profile_hash_missing")
        risk_flags = list(dict.fromkeys(risk_flags))
        bio_snippets = (str(canon.get("bio") or ""),) if canon.get("bio") else ()
        university = str(
            rows.university_name or rows.profile_payload.get("university_id") or ""
        )
        university_id = str(rows.profile_payload.get("university_id") or "")
        title = str(canon.get("title_raw") or canon.get("title_family") or "")

        source_metadata = _source_metadata_by_observation(rows.source_urls)
        first_source = next(iter(source_metadata.values()), (None, None))
        entity_ref = _source_ref(
            "entity", build_id, entity_id,
            summary=str(canon.get("display_name") or entity_id),
            official_url=first_source[0], last_verified=first_source[1],
        )
        university_ref = _source_ref(
            "university", build_id, university_id or university,
            summary=university,
            official_url=first_source[0], last_verified=first_source[1],
        )
        observation_refs: dict[str, SourceRef] = {}
        for observation in rows.observations:
            observation_id = str(observation.get("id") or "")
            if not observation_id:
                continue
            official_url, last_verified = source_metadata.get(
                observation_id,
                (canonicalize_source_url(observation.get("source_url")), None),
            )
            observation_refs[observation_id] = _source_ref(
                "observation", build_id, observation_id,
                summary="; ".join(_org_unit_names((observation,))) or observation_id,
                official_url=official_url, last_verified=last_verified,
            )

        statement_refs = tuple(
            _source_ref(
                "research-statement", build_id, str(row["id"]),
                summary=str(row.get("normalized_text") or ""),
                official_url=source_metadata.get(str(row.get("observation_id") or ""), (None, None))[0],
                last_verified=source_metadata.get(str(row.get("observation_id") or ""), (None, None))[1],
            )
            for row in selected_statement_rows
        )
        publication_refs = tuple(
            _source_ref(
                "publication-mention", build_id, str(row["id"]),
                summary=str(row.get("normalized_text") or ""),
                official_url=source_metadata.get(str(row.get("observation_id") or ""), (None, None))[0],
                last_verified=source_metadata.get(str(row.get("observation_id") or ""), (None, None))[1],
            )
            for row in selected_mention_rows
        )
        statement_observation_ids = {
            str(row["id"]): str(row.get("observation_id") or "")
            for row in rows.statements
        }
        topic_refs = tuple(
            _source_ref(
                "topic-link", build_id,
                f"{row['statement_id']}:{row['topic_id']}",
                summary=str(row.get("evidence_span") or row.get("canonical_name") or ""),
                official_url=source_metadata.get(
                    statement_observation_ids.get(str(row["statement_id"]), ""),
                    (None, None),
                )[0],
                last_verified=source_metadata.get(
                    statement_observation_ids.get(str(row["statement_id"]), ""),
                    (None, None),
                )[1],
            )
            for row in selected_topic_rows
        )
        finding_refs = tuple(
            _source_ref(
                "quality-finding", build_id, str(row["id"]),
                summary=str(row.get("code") or ""),
                official_url=source_metadata.get(
                    str(row.get("observation_id") or ""), (None, None),
                )[0],
                last_verified=source_metadata.get(
                    str(row.get("observation_id") or ""), (None, None),
                )[1],
            )
            for row in rows.findings if row.get("code")
        )
        affiliation_refs = tuple(
            observation_refs[str(observation["id"])]
            for observation in rows.observations
            if observation.get("id")
            and _org_unit_names((observation,))
            and str(observation["id"]) in observation_refs
        )
        source_refs = tuple(
            ref for observation_id, ref in observation_refs.items()
            if source_metadata.get(observation_id, (None, None))[0]
        )
        refs_by_field = {
            "display_name": (entity_ref,),
            "university": (university_ref,),
            "org_units": affiliation_refs,
            "title": (entity_ref,),
            "title_family": (entity_ref,),
            "master_eligibility": (entity_ref,),
            "phd_eligibility": (entity_ref,),
            "role_status": (entity_ref,),
            "profile_url": (entity_ref,),
            "research_statement": statement_refs,
            "approved_topics": topic_refs,
            "publications": publication_refs,
            "bio": (entity_ref,),
            "data_sources": source_refs,
            "quality_findings": finding_refs,
            "risk_flags": (entity_ref,),
        }
        fact_items = build_fact_items(
            identity={"display_name": str(canon.get("display_name") or ""),
                      "entity_id": entity_id},
            eligibility={"master_eligibility": str(canon.get("master_eligibility") or ""),
                         "phd_eligibility": str(canon.get("phd_eligibility") or "")},
            research_statements=statements,
            approved_topics=topics,
            publications=mentions,
            source_refs_by_field=refs_by_field,
            university=university,
            org_units=org_units,
            title=title,
            title_family=str(canon.get("title_family") or ""),
            role_status=role_status,
            profile_url=canon.get("profile_url"),
            bio_snippets=bio_snippets,
            source_urls=source_urls,
            quality_findings=findings,
            risk_flags=risk_flags,
        )
        provenance_refs_tuple = _dedupe_refs_from_facts(fact_items)
        fact_bundle = FactBundle(
            build_id=build_id,
            subject_id=entity_id,
            facts=fact_items,
            source_refs=provenance_refs_tuple,
        )

        # contacts: double gate
        contacts: dict[str, str] = {}
        if include_contacts and viewer_permissions.include_contacts:
            email = canon.get("email")
            phone = canon.get("phone")
            if email:
                contacts["email"] = str(email)
            if phone:
                contacts["phone"] = str(phone)

        return ProfessorDetail(
            build_id=build_id,
            profile_hash=profile_hash,
            entity_id=entity_id,
            display_name=str(canon.get("display_name") or ""),
            university=university,
            org_units=org_units,
            title=title,
            title_family=str(canon.get("title_family") or ""),
            master_eligibility=str(canon.get("master_eligibility") or "unknown"),
            phd_eligibility=str(canon.get("phd_eligibility") or "unknown"),
            role_status=role_status,
            profile_url=canon.get("profile_url"),
            research_statements=statements,
            approved_topics=topics,
            selected_publication_mentions=mentions,
            bio_snippets=bio_snippets,
            source_urls=source_urls,
            provenance_refs=provenance_refs_tuple,
            quality_findings=findings,
            risk_flags=tuple(risk_flags),
            fact_bundle=fact_bundle,
            contacts=contacts,
        )

    async def get_details(
        self,
        snapshot: ActiveBuildSnapshot,
        entity_ids: list[str],
        include_contacts: bool,
        viewer_permissions: ViewerPermissions,
    ) -> dict[str, ProfessorDetail | None]:
        out: dict[str, ProfessorDetail | None] = {}
        for entity_id in dedupe_entity_ids(entity_ids):
            try:
                out[entity_id] = await self.get_detail(
                    snapshot, entity_id, include_contacts, viewer_permissions,
                )
            except ProfessorFactNotFound:
                out[entity_id] = None
        return out


__all__ = ["CatalogProfessorFactAdapter"]
