"""Reproducible C2 catalog build and atomic publication."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile

from dext_competition.catalog.artifacts import (
    CATALOG_FILE,
    MANIFEST_FILE,
    cards_to_jsonl,
    catalog_content_hash,
    catalog_version_id,
    manifest_to_json,
)
from dext_competition.catalog.extractor import CatalogExtractionReport, extract_catalog
from dext_competition.catalog.repository import FileCompetitionCatalog
from dext_competition.catalog.rules import CatalogRules, load_catalog_rules
from dext_competition.contracts.catalog import CompetitionCatalogManifest
from dext_competition.ports.knowledge import KnowledgeIndexPort


@dataclass(frozen=True, slots=True)
class CatalogBuildResult:
    manifest: CompetitionCatalogManifest
    report: CatalogExtractionReport
    artifact_dir: Path


def _promote(stage: Path, output: Path) -> None:
    backup: Path | None = None
    try:
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{output.name}.backup-", dir=output.parent))
            backup.rmdir()
            output.replace(backup)
        stage.replace(output)
    except Exception:
        if backup is not None and backup.exists() and not output.exists():
            backup.replace(output)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)


async def build_catalog(
    index: KnowledgeIndexPort,
    output_dir: str | Path = "data/competition/catalog/",
    *,
    rules: CatalogRules | None = None,
) -> CatalogBuildResult:
    rules = rules or load_catalog_rules()
    cards, report = await extract_catalog(index, rules)
    knowledge_manifest = index.manifest()
    aggregate = catalog_content_hash(cards, knowledge_manifest.version_id, rules.version)
    manifest = CompetitionCatalogManifest(
        version_id=catalog_version_id(aggregate),
        knowledge_base_version=knowledge_manifest.version_id,
        rules_version=rules.version,
        card_count=len(cards),
        in_2024_catalog_count=sum(card.in_2024_catalog for card in cards),
        content_hash=aggregate,
        generated_at=knowledge_manifest.generated_at,
    )
    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    stage.rmdir()
    try:
        stage.mkdir()
        (stage / CATALOG_FILE).write_text(cards_to_jsonl(cards), encoding="utf-8")
        (stage / MANIFEST_FILE).write_text(manifest_to_json(manifest), encoding="utf-8")
        FileCompetitionCatalog(stage, expected_knowledge_base_version=knowledge_manifest.version_id)
        _promote(stage, output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return CatalogBuildResult(manifest=manifest, report=report, artifact_dir=output)


__all__ = ["CatalogBuildResult", "build_catalog"]
