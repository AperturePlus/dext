"""Validated read-only C2 catalog repository."""
from __future__ import annotations

from pathlib import Path

from dext_competition.catalog.artifacts import (
    CATALOG_FILE,
    MANIFEST_FILE,
    CatalogArtifactError,
    cards_from_jsonl,
    catalog_content_hash,
    catalog_version_id,
    manifest_from_json,
)
from dext_competition.catalog.ids import competition_id
from dext_competition.catalog.rules import load_catalog_rules
from dext_competition.contracts.catalog import (
    CompetitionCard,
    CompetitionCatalogManifest,
    CompetitionCategory,
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CatalogArtifactError(f"missing catalog artifact: {path.name}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogArtifactError(f"cannot read catalog artifact {path.name}: {exc}") from exc


class FileCompetitionCatalog:
    def __init__(self, artifact_dir: str | Path, *, expected_knowledge_base_version: str | None = None) -> None:
        root = Path(artifact_dir)
        manifest = manifest_from_json(_read(root / MANIFEST_FILE))
        cards = cards_from_jsonl(_read(root / CATALOG_FILE))
        self._validate(manifest, cards, expected_knowledge_base_version)
        self._manifest = manifest
        self._cards = cards
        self._by_id = {card.competition_id: card for card in cards}

    @staticmethod
    def _validate(
        manifest: CompetitionCatalogManifest,
        cards: tuple[CompetitionCard, ...],
        expected_knowledge_base_version: str | None,
    ) -> None:
        rules = load_catalog_rules()
        if manifest.rules_version != rules.version:
            raise CatalogArtifactError("catalog rules version mismatch")
        if expected_knowledge_base_version and manifest.knowledge_base_version != expected_knowledge_base_version:
            raise CatalogArtifactError("knowledge base version mismatch")
        if tuple(sorted(cards, key=lambda card: card.competition_id)) != cards:
            raise CatalogArtifactError("catalog cards are not in canonical ID order")
        ids = [card.competition_id for card in cards]
        if len(ids) != len(set(ids)):
            raise CatalogArtifactError("duplicate competition_id")
        valid_categories = {item.value for item in CompetitionCategory}
        for card in cards:
            if card.competition_id != competition_id(card.display_name, prefix=rules.identity_prefix):
                raise CatalogArtifactError(f"competition_id mismatch: {card.display_name}")
            if card.category not in valid_categories:
                raise CatalogArtifactError(f"invalid category: {card.category}")
            if not card.internal_source_refs:
                raise CatalogArtifactError(f"card has no source refs: {card.display_name}")
            allowed = set(card.internal_source_refs)
            if any(ref not in allowed for item in card.field_evidence for ref in item.source_refs):
                raise CatalogArtifactError(f"field evidence has non-canonical ref: {card.display_name}")
            evidence = {item.field: item for item in card.field_evidence}
            if len(evidence) != len(card.field_evidence):
                raise CatalogArtifactError(f"duplicate field evidence: {card.display_name}")
            required = {
                "display_name", "category", "tags", "summary", "eligibility",
                "schedule", "team_policy", "materials", "ai_compliance",
                "preparation_focus", "risk_flags", "official_links", "in_2024_catalog",
            }
            if required - evidence.keys():
                raise CatalogArtifactError(f"incomplete field evidence: {card.display_name}")
            scalar_projection = {
                "display_name": card.display_name,
                "category": card.category,
                "summary": card.summary,
                "eligibility": card.eligibility,
                "schedule": card.schedule,
                "team_policy": card.team_policy,
                "ai_compliance": card.ai_compliance,
                "in_2024_catalog": str(card.in_2024_catalog).lower(),
            }
            for field, value in scalar_projection.items():
                if " / ".join(evidence[field].values) != value:
                    raise CatalogArtifactError(
                        f"field evidence projection mismatch: {card.display_name}.{field}"
                    )
            tuple_projection = {
                "tags": card.tags,
                "materials": card.materials,
                "preparation_focus": card.preparation_focus,
                "risk_flags": card.risk_flags,
                "official_links": card.official_links,
            }
            for field, value in tuple_projection.items():
                if evidence[field].values != value:
                    raise CatalogArtifactError(
                        f"field evidence projection mismatch: {card.display_name}.{field}"
                    )
        in_2024 = sum(card.in_2024_catalog for card in cards)
        if manifest.card_count != len(cards) or manifest.in_2024_catalog_count != in_2024:
            raise CatalogArtifactError("catalog manifest counts mismatch")
        aggregate = catalog_content_hash(cards, manifest.knowledge_base_version, manifest.rules_version)
        if aggregate != manifest.content_hash:
            raise CatalogArtifactError("catalog content_hash mismatch")
        if manifest.version_id != catalog_version_id(aggregate):
            raise CatalogArtifactError("catalog version_id mismatch")

    def manifest(self) -> CompetitionCatalogManifest:
        return self._manifest

    async def get(self, competition_id: str) -> CompetitionCard | None:
        return self._by_id.get(competition_id)

    async def list_competitions(self) -> tuple[CompetitionCard, ...]:
        return self._cards


def verify_catalog(
    artifact_dir: str | Path, *, expected_knowledge_base_version: str | None = None,
) -> FileCompetitionCatalog:
    return FileCompetitionCatalog(
        artifact_dir, expected_knowledge_base_version=expected_knowledge_base_version,
    )


__all__ = ["FileCompetitionCatalog", "verify_catalog"]
