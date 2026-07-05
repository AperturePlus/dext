"""Command-line interface for competition knowledge artifacts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click

from dext_competition.catalog import (
    CatalogArtifactError,
    CatalogExtractionError,
    build_catalog,
    verify_catalog,
)
from dext_competition.config import CompetitionSettings
from dext_competition.index import (
    IndexArtifactError,
    KnowledgeSourceReadError,
    build_index,
    verify_index,
)


def _emit(value: dict[str, object]) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


@click.group()
def main() -> None:
    """Build and verify the dext competition knowledge index."""


@main.group()
def index() -> None:
    """Manage deterministic C1 knowledge-index artifacts."""


@index.command("build")
@click.option(
    "--source-root",
    type=click.Path(exists=False, file_okay=False, path_type=Path),
    default=CompetitionSettings().knowledge_source_root,
    show_default=True,
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=CompetitionSettings().index_artifact_dir,
    show_default=True,
)
def index_build(source_root: Path, output_dir: Path) -> None:
    """Build manifest.json, chunks.jsonl, and bm25.json."""

    try:
        result = build_index(source_root, output_dir)
    except (IndexArtifactError, KnowledgeSourceReadError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    _emit(
        {
            "artifact_dir": str(result.artifact_dir),
            "chunk_count": result.chunk_count,
            "content_hash": result.manifest.content_hash,
            "file_count": result.manifest.file_count,
            "knowledge_base_version": result.manifest.version_id,
        }
    )


@index.command("verify")
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=CompetitionSettings().index_artifact_dir,
    show_default=True,
)
def index_verify(output_dir: Path) -> None:
    """Validate artifact schema, hashes, counts, and BM25 document order."""

    try:
        repository = verify_index(output_dir)
    except IndexArtifactError as exc:
        raise click.ClickException(str(exc)) from None
    manifest = repository.manifest()
    _emit(
        {
            "artifact_dir": str(output_dir),
            "content_hash": manifest.content_hash,
            "file_count": manifest.file_count,
            "knowledge_base_version": manifest.version_id,
            "valid": True,
        }
    )


@main.group()
def catalog() -> None:
    """Manage deterministic C2 competition-catalog artifacts."""


@catalog.command("build")
@click.option(
    "--index-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=CompetitionSettings().index_artifact_dir,
    show_default=True,
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=CompetitionSettings().catalog_artifact_dir,
    show_default=True,
)
def catalog_build(index_dir: Path, output_dir: Path) -> None:
    """Build catalog.jsonl and manifest.json from a verified C1 index."""

    try:
        index_repository = verify_index(index_dir)
        result = asyncio.run(build_catalog(index_repository, output_dir))
    except (IndexArtifactError, CatalogArtifactError, CatalogExtractionError, OSError) as exc:
        raise click.ClickException(str(exc)) from None
    _emit({
        "artifact_dir": str(result.artifact_dir),
        "card_count": result.manifest.card_count,
        "catalog_version": result.manifest.version_id,
        "content_hash": result.manifest.content_hash,
        "in_2024_catalog_count": result.manifest.in_2024_catalog_count,
        "knowledge_base_version": result.manifest.knowledge_base_version,
    })


@catalog.command("verify")
@click.option(
    "--index-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=CompetitionSettings().index_artifact_dir,
    show_default=True,
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=CompetitionSettings().catalog_artifact_dir,
    show_default=True,
)
def catalog_verify(index_dir: Path, output_dir: Path) -> None:
    """Validate catalog hashes, identities, citations, and C1 version pin."""

    try:
        knowledge = verify_index(index_dir)
        repository = verify_catalog(
            output_dir,
            expected_knowledge_base_version=knowledge.manifest().version_id,
        )
    except (IndexArtifactError, CatalogArtifactError) as exc:
        raise click.ClickException(str(exc)) from None
    manifest = repository.manifest()
    _emit({
        "artifact_dir": str(output_dir),
        "card_count": manifest.card_count,
        "catalog_version": manifest.version_id,
        "knowledge_base_version": manifest.knowledge_base_version,
        "valid": True,
    })


if __name__ == "__main__":
    main()


__all__ = ["main"]
