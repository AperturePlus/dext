"""Canonical C2 catalog artifact serialization."""
from __future__ import annotations

import datetime
import hashlib
import json

from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionCard,
    CompetitionCatalogManifest,
    CompetitionFieldEvidence,
)
from dext_grounded import SourceRef


CATALOG_FILE = "catalog.jsonl"
MANIFEST_FILE = "manifest.json"
ARTIFACT_FILES = (CATALOG_FILE, MANIFEST_FILE)


class CatalogArtifactError(ValueError):
    pass


def _ref_to_dict(ref: SourceRef) -> dict[str, object]:
    return {
        "doc_path": ref.doc_path,
        "heading_path": ref.heading_path,
        "chunk_hash": ref.chunk_hash,
        "quote_or_summary": ref.quote_or_summary,
        "official_url": ref.official_url,
        "last_verified": ref.last_verified,
    }


def _ref_from_dict(raw: dict[str, object]) -> SourceRef:
    return SourceRef(
        doc_path=str(raw["doc_path"]),
        heading_path=str(raw["heading_path"]),
        chunk_hash=str(raw["chunk_hash"]),
        quote_or_summary=str(raw["quote_or_summary"]),
        official_url=None if raw.get("official_url") is None else str(raw["official_url"]),
        last_verified=None if raw.get("last_verified") is None else str(raw["last_verified"]),
    )


def card_to_dict(card: CompetitionCard) -> dict[str, object]:
    return {
        "competition_id": card.competition_id,
        "display_name": card.display_name,
        "category": card.category,
        "tags": list(card.tags),
        "summary": card.summary,
        "eligibility": card.eligibility,
        "schedule": card.schedule,
        "team_policy": card.team_policy,
        "materials": list(card.materials),
        "ai_compliance": card.ai_compliance,
        "preparation_focus": list(card.preparation_focus),
        "risk_flags": list(card.risk_flags),
        "official_links": list(card.official_links),
        "internal_source_refs": [_ref_to_dict(ref) for ref in card.internal_source_refs],
        "in_2024_catalog": card.in_2024_catalog,
        "last_verified": card.last_verified,
        "field_evidence": [
            {
                "field": item.field,
                "values": list(item.values),
                "status": item.status.value,
                "source_refs": [_ref_to_dict(ref) for ref in item.source_refs],
                "review_notice": item.review_notice,
            }
            for item in card.field_evidence
        ],
    }


def card_from_dict(raw: dict[str, object]) -> CompetitionCard:
    evidence = tuple(
        CompetitionFieldEvidence(
            field=str(item["field"]),
            values=tuple(map(str, item.get("values", []))),
            status=CatalogEvidenceStatus(str(item["status"])),
            source_refs=tuple(_ref_from_dict(ref) for ref in item.get("source_refs", [])),
            review_notice=None if item.get("review_notice") is None else str(item["review_notice"]),
        )
        for item in raw.get("field_evidence", [])
    )
    return CompetitionCard(
        competition_id=str(raw["competition_id"]),
        display_name=str(raw["display_name"]),
        category=str(raw["category"]),
        tags=tuple(map(str, raw.get("tags", []))),
        summary=str(raw.get("summary", "")),
        eligibility=str(raw.get("eligibility", "")),
        schedule=str(raw.get("schedule", "")),
        team_policy=str(raw.get("team_policy", "")),
        materials=tuple(map(str, raw.get("materials", []))),
        ai_compliance=str(raw.get("ai_compliance", "")),
        preparation_focus=tuple(map(str, raw.get("preparation_focus", []))),
        risk_flags=tuple(map(str, raw.get("risk_flags", []))),
        official_links=tuple(map(str, raw.get("official_links", []))),
        internal_source_refs=tuple(_ref_from_dict(ref) for ref in raw.get("internal_source_refs", [])),
        in_2024_catalog=bool(raw.get("in_2024_catalog", False)),
        last_verified=None if raw.get("last_verified") is None else str(raw["last_verified"]),
        field_evidence=evidence,
    )


def cards_to_jsonl(cards: tuple[CompetitionCard, ...]) -> str:
    return "".join(
        json.dumps(card_to_dict(card), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for card in cards
    )


def cards_from_jsonl(value: str) -> tuple[CompetitionCard, ...]:
    try:
        return tuple(card_from_dict(json.loads(line)) for line in value.splitlines() if line)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogArtifactError(f"invalid catalog.jsonl: {exc}") from exc


def catalog_content_hash(
    cards: tuple[CompetitionCard, ...], knowledge_base_version: str, rules_version: str,
) -> str:
    payload = knowledge_base_version + "\n" + rules_version + "\n" + cards_to_jsonl(cards)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def catalog_version_id(content_hash: str) -> str:
    return "catalog-v1-" + content_hash[:12]


def manifest_to_json(manifest: CompetitionCatalogManifest) -> str:
    raw = {
        "version_id": manifest.version_id,
        "knowledge_base_version": manifest.knowledge_base_version,
        "rules_version": manifest.rules_version,
        "card_count": manifest.card_count,
        "in_2024_catalog_count": manifest.in_2024_catalog_count,
        "content_hash": manifest.content_hash,
        "generated_at": manifest.generated_at.isoformat(),
    }
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def manifest_from_json(value: str) -> CompetitionCatalogManifest:
    try:
        raw = json.loads(value)
        return CompetitionCatalogManifest(
            version_id=str(raw["version_id"]),
            knowledge_base_version=str(raw["knowledge_base_version"]),
            rules_version=str(raw["rules_version"]),
            card_count=int(raw["card_count"]),
            in_2024_catalog_count=int(raw["in_2024_catalog_count"]),
            content_hash=str(raw["content_hash"]),
            generated_at=datetime.datetime.fromisoformat(str(raw["generated_at"])),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CatalogArtifactError(f"invalid catalog manifest: {exc}") from exc


__all__ = [
    "ARTIFACT_FILES", "CATALOG_FILE", "MANIFEST_FILE", "CatalogArtifactError",
    "card_from_dict", "card_to_dict", "cards_from_jsonl", "cards_to_jsonl",
    "catalog_content_hash", "catalog_version_id", "manifest_from_json", "manifest_to_json",
]
