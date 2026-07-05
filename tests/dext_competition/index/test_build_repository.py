from __future__ import annotations

import json
from pathlib import Path

import pytest

from dext_competition import KnowledgeHit
from dext_competition.ports import KnowledgeIndexPort
from dext_competition.index import (
    FileKnowledgeIndex,
    IndexArtifactError,
    build_index,
    verify_index,
)
from dext_competition.index.artifacts import ARTIFACT_FILES

CORPUS_ROOT = Path(__file__).resolve().parents[3] / "data" / "竞赛助手"


def _write_source(root: Path, *, changed: bool = False, verified: bool = True) -> None:
    root.mkdir()
    date = "> 最近核验：2026-06-30。\n\n" if verified else ""
    suffix = "更新" if changed else ""
    (root / "a.md").write_text(
        f"# 数学建模\n\n{date}## 规则\n\n数学建模竞赛{suffix}\n",
        encoding="utf-8",
    )
    (root / "b.md").write_text("# Robot\n\nAI robotics contest\n", encoding="utf-8")


def _artifact_bytes(root: Path) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in ARTIFACT_FILES}


def test_build_is_byte_reproducible_and_generated_at_is_content_derived(tmp_path: Path):
    source = tmp_path / "source"
    _write_source(source)
    first = tmp_path / "first"
    second = tmp_path / "second"
    result_a = build_index(source, first)
    result_b = build_index(source, second)
    assert _artifact_bytes(first) == _artifact_bytes(second)
    assert result_a.manifest == result_b.manifest
    assert result_a.manifest.generated_at.isoformat() == "2026-06-30T00:00:00+00:00"
    assert set(path.name for path in first.iterdir()) == set(ARTIFACT_FILES)


def test_build_uses_epoch_without_verified_date_and_changes_version(tmp_path: Path):
    source = tmp_path / "source"
    _write_source(source, verified=False)
    output = tmp_path / "index"
    first = build_index(source, output)
    assert first.manifest.generated_at.isoformat() == "1970-01-01T00:00:00+00:00"
    (source / "a.md").write_text("# A\n\nchanged\n", encoding="utf-8")
    second = build_index(source, output)
    assert first.manifest.version_id != second.manifest.version_id


async def test_repository_queries_chinese_and_returns_canonical_source_ref(tmp_path: Path):
    output = tmp_path / "index"
    build_index(CORPUS_ROOT, output)
    repository = FileKnowledgeIndex(output)
    assert isinstance(repository, KnowledgeIndexPort)
    hits = await repository.query("数学建模", limit=5)
    assert hits
    assert all(isinstance(hit, KnowledgeHit) for hit in hits)
    assert any("数学建模" in hit.chunk.text or "数学建模" in hit.chunk.heading_path for hit in hits)
    first = hits[0]
    assert first.source_ref.doc_path == first.chunk.doc_path
    assert first.source_ref.chunk_hash == first.chunk.chunk_hash
    assert first.source_ref.last_verified == first.chunk.last_verified
    assert first.score > 0


async def test_repository_empty_query_limit_and_stable_tie_break(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("b.md", "a.md"):
        (source / name).write_text("# Same\n\nidentical token\n", encoding="utf-8")
    output = tmp_path / "index"
    build_index(source, output)
    repository = verify_index(output)
    assert await repository.query("   ") == ()
    with pytest.raises(ValueError):
        await repository.query("token", limit=0)
    hits = await repository.search(("identical", "token"))
    assert [hit.chunk.doc_path for hit in hits] == ["a.md", "b.md"]


@pytest.mark.parametrize("artifact", ["manifest.json", "chunks.jsonl", "bm25.json"])
def test_repository_rejects_corrupt_artifacts(tmp_path: Path, artifact: str):
    source = tmp_path / "source"
    _write_source(source)
    output = tmp_path / "index"
    build_index(source, output)
    path = output / artifact
    if artifact == "manifest.json":
        value = json.loads(path.read_text(encoding="utf-8"))
        value["content_hash"] = "corrupt"
        path.write_text(json.dumps(value), encoding="utf-8")
    elif artifact == "chunks.jsonl":
        lines = path.read_text(encoding="utf-8").splitlines()
        value = json.loads(lines[0])
        value["text"] += "corrupt"
        lines[0] = json.dumps(value, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        value["document_hashes"][0] = "corrupt"
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(IndexArtifactError):
        FileKnowledgeIndex(output)


def test_real_corpus_build_covers_exactly_18_markdown_files(tmp_path: Path):
    result = build_index(CORPUS_ROOT, tmp_path / "index")
    assert result.manifest.file_count == 18
    assert result.manifest.markdown_file_count == 18
    assert result.chunk_count > 0


def test_real_corpus_build_is_byte_reproducible(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    result_a = build_index(CORPUS_ROOT, first)
    result_b = build_index(CORPUS_ROOT, second)
    assert result_a.manifest == result_b.manifest
    assert _artifact_bytes(first) == _artifact_bytes(second)
