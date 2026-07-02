"""CatalogProfessorFactAdapter: implements ProfessorFactPort over the
published catalog SQLite (R4). Two methods: hydrate (batch authority facts)
and get_detail (single-entity full ProfessorDetail + FactBundle).

Never imports dext_graph; reads only the pinned build_id. Contacts are
double-gated and never enter the FactBundle. No cache (R7 decorator if ever).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from dext_grounded import SourceRef
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.content import ContentClass

from dext_recommend.adapters._catalog_fact_reader import (
    CatalogProfessorFactReader, CatalogProfessorDetailRows,
)
from dext_recommend.config import RecommendSettings
from dext_recommend.facts._ids import chunk_entity_ids, dedupe_entity_ids
from dext_recommend.facts.evidence import (
    build_fact_items, select_publication_snippets, select_statement_snippets,
)
from dext_recommend.facts.source_urls import dedupe_source_urls
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactNotFound, ViewerPermissions,
)

if TYPE_CHECKING:
    from dext_recommend.readiness import ActiveBuildSnapshot


def _fact_from_row(row: dict[str, Any]) -> ProfessorFact:
    return ProfessorFact(
        entity_id=str(row["entity_id"]),
        display_name=str(row["display_name"] or ""),
        university=str(row.get("university_id") or ""),
        org_units=tuple(row.get("org_unit_ids") or ()),
        title=str(row.get("title_family") or ""),
        title_family=str(row.get("title_family") or ""),
        master_eligibility=str(row.get("master_eligibility") or "unknown"),
        phd_eligibility=str(row.get("phd_eligibility") or "unknown"),
        role_status=str(row.get("role_status") or "included"),
        profile_url=row.get("profile_url"),
        profile_hash=row.get("profile_hash"),
        research_summary=None,
        university_id=row.get("university_id"),
        city_name=row.get("city_name"),
        org_unit_ids=tuple(row.get("org_unit_ids") or ()),
        topic_ids=tuple(row.get("topic_ids") or ()),
    )


def _org_unit_names(observations: tuple[Mapping, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for obs in observations:
        try:
            payload = json.loads(obs.get("observation_payload_json") or "{}")
        except (TypeError, ValueError):
            payload = {}
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


def _approved_topic_names(topic_links: tuple[Mapping, ...]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for row in topic_links:
        if str(row.get("review_status")) != "approved":
            continue
        name = str(row.get("canonical_name") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _finding_codes(findings: tuple[Mapping, ...]) -> tuple[str, ...]:
    return tuple(str(r.get("code")) for r in findings if r.get("code"))


def _source_ref(kind: str, build_id: str, key: str, *, summary: str = "") -> SourceRef:
    return SourceRef(
        doc_path="catalog",
        heading_path=f"catalog:{kind}:{build_id}:{key}",
        chunk_hash=f"{build_id}:{key}",
        quote_or_summary=summary or f"catalog:{kind}:{build_id}:{key}",
    )


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
        statements = select_statement_snippets(rows.statements)
        mentions = select_publication_snippets(rows.mentions)
        topics = _approved_topic_names(rows.topic_links)
        findings = _finding_codes(rows.findings)
        source_urls = dedupe_source_urls(
            str(r.get("source_url")) for r in rows.source_urls
        )
        profile_hash = rows.profile_hash

        # provenance refs (R3-compatible projection)
        provenance_refs: list[SourceRef] = [
            _source_ref("entity", build_id, entity_id,
                        summary=str(rows.profile_payload.get("provenance_ref") or "")),
        ]
        for s in rows.statements:
            provenance_refs.append(
                _source_ref("research-statement", build_id, str(s["id"]),
                            summary=str(s.get("normalized_text") or ""))
            )
        for m in rows.mentions:
            provenance_refs.append(
                _source_ref("publication-mention", build_id, str(m["id"]),
                            summary=str(m.get("normalized_text") or ""))
            )
        for tl in rows.topic_links:
            if str(tl.get("review_status")) == "approved":
                provenance_refs.append(
                    SourceRef(
                        doc_path="catalog",
                        heading_path=str(tl.get("provenance_ref") or ""),
                        chunk_hash=f"{build_id}:{tl['statement_id']}:{tl['topic_id']}",
                        quote_or_summary=str(tl.get("evidence_span") or ""),
                    )
                )
        provenance_refs_tuple = tuple(provenance_refs)

        # FactItems: identity/eligibility/statement/topics/publications, each
        # tied to its SourceRef when one exists, else UNCERTAIN.
        refs_by_field = {
            "display_name": (provenance_refs_tuple[0],) if provenance_refs_tuple else (),
            "master_eligibility": (provenance_refs_tuple[0],) if provenance_refs_tuple else (),
            "phd_eligibility": (provenance_refs_tuple[0],) if provenance_refs_tuple else (),
            "research_statement": tuple(
                r for r in provenance_refs_tuple
                if r.heading_path.startswith("catalog:research-statement:")
            ),
            "approved_topics": tuple(
                r for r in provenance_refs_tuple
                if "research-statement" in r.heading_path
            ),
            "publications": tuple(
                r for r in provenance_refs_tuple
                if r.heading_path.startswith("catalog:publication-mention:")
            ),
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
        )
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

        risk_flags: list[str] = []
        if profile_hash is None:
            risk_flags.append("profile_hash_missing")

        return ProfessorDetail(
            build_id=build_id,
            profile_hash=profile_hash,
            entity_id=entity_id,
            display_name=str(canon.get("display_name") or ""),
            university=str(rows.university_name or rows.profile_payload.get("university_id") or ""),
            org_units=org_units,
            title=str(canon.get("title_raw") or canon.get("title_family") or ""),
            title_family=str(canon.get("title_family") or ""),
            master_eligibility=str(canon.get("master_eligibility") or "unknown"),
            phd_eligibility=str(canon.get("phd_eligibility") or "unknown"),
            role_status=role_status,
            profile_url=canon.get("profile_url"),
            research_statements=statements,
            approved_topics=topics,
            selected_publication_mentions=mentions,
            bio_snippets=(str(canon.get("bio") or ""),) if canon.get("bio") else (),
            source_urls=source_urls,
            provenance_refs=provenance_refs_tuple,
            quality_findings=findings,
            risk_flags=tuple(risk_flags),
            fact_bundle=fact_bundle,
            contacts=contacts,
        )


__all__ = ["CatalogProfessorFactAdapter"]
