from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from dext_competition.catalog import (
    CatalogArtifactError,
    FileCompetitionCatalog,
    build_catalog,
)
from dext_competition.cli import main
from dext_competition.index import FileKnowledgeIndex, build_index
from dext_competition.ports import CompetitionCatalogPort


CORPUS_ROOT = Path(__file__).resolve().parents[3] / "data" / "竞赛助手"


def _build(tmp_path: Path) -> tuple[Path, Path, FileKnowledgeIndex]:
    index_dir = tmp_path / "index"
    catalog_dir = tmp_path / "catalog"
    build_index(CORPUS_ROOT, index_dir)
    knowledge = FileKnowledgeIndex(index_dir)
    asyncio.run(build_catalog(knowledge, catalog_dir))
    return index_dir, catalog_dir, knowledge


def test_repository_satisfies_port_and_rejects_version_mismatch(tmp_path: Path):
    _, catalog_dir, knowledge = _build(tmp_path)
    repository = FileCompetitionCatalog(
        catalog_dir,
        expected_knowledge_base_version=knowledge.manifest().version_id,
    )
    assert isinstance(repository, CompetitionCatalogPort)
    with pytest.raises(CatalogArtifactError, match="knowledge base version mismatch"):
        FileCompetitionCatalog(catalog_dir, expected_knowledge_base_version="kb-other")


def test_repository_rejects_tampered_card(tmp_path: Path):
    _, catalog_dir, _ = _build(tmp_path)
    path = catalog_dir / "catalog.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["display_name"] = "tampered"
    lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CatalogArtifactError):
        FileCompetitionCatalog(catalog_dir)


def test_repository_rejects_tampered_manifest(tmp_path: Path):
    _, catalog_dir, _ = _build(tmp_path)
    path = catalog_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["card_count"] += 1
    path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(CatalogArtifactError, match="counts mismatch"):
        FileCompetitionCatalog(catalog_dir)


def test_repository_rejects_tampered_evidence(tmp_path: Path):
    _, catalog_dir, _ = _build(tmp_path)
    path = catalog_dir / "catalog.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["field_evidence"][0]["values"] = ["tampered"]
    lines[0] = json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(CatalogArtifactError, match="projection mismatch"):
        FileCompetitionCatalog(catalog_dir)


def test_catalog_cli_build_and_verify(tmp_path: Path):
    runner = CliRunner()
    index_dir = tmp_path / "index"
    catalog_dir = tmp_path / "catalog"
    built_index = runner.invoke(main, [
        "index", "build", "--source-root", str(CORPUS_ROOT), "--output-dir", str(index_dir),
    ])
    assert built_index.exit_code == 0, built_index.output
    built = runner.invoke(main, [
        "catalog", "build", "--index-dir", str(index_dir), "--output-dir", str(catalog_dir),
    ])
    assert built.exit_code == 0, built.output
    payload = json.loads(built.output)
    assert payload["card_count"] == 90
    assert payload["in_2024_catalog_count"] == 84
    verified = runner.invoke(main, [
        "catalog", "verify", "--index-dir", str(index_dir), "--output-dir", str(catalog_dir),
    ])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["valid"] is True
