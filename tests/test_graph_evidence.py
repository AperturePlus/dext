import hashlib
import sqlite3

import pytest

from dext_graph.catalog.evidence import (
    PARTITIONS,
    detect_language,
    split_publication_mentions,
    split_research_statements,
)
from dext_graph.catalog.workflow import create_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


def test_evidence_parsers_preserve_source_contract_boundaries():
    statements = split_research_statements("研究方向：图学习；(2) 药物发现、AI Ethics")
    assert [item.normalized_text for item in statements] == ["图学习", "药物发现", "AI Ethics"]
    assert [item.language for item in statements] == ["zh", "zh", "en"]
    assert detect_language("图神经网络 GNN") == "mixed"

    mentions = split_publication_mentions(
        "Wang, A.; Li, B.; Title. 2024；Second work. doi:10.1000/XYZ.1 (2023)"
    )
    assert len(mentions) == 2
    assert "A.; Li" in mentions[0].normalized_text
    assert mentions[0].year == 2024
    assert mentions[1].doi == "10.1000/xyz.1"
    assert mentions[1].year == 2023


@pytest.mark.asyncio
async def test_stage3_materializes_evidence_and_frozen_exports(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_SKIP_NEO4J", "1")
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=3)
    result = await create_build(["测试大学"], settings)
    assert result["build"]["status"] == "WRITING_VECTOR"
    assert result["graph"]["status"] == "COMPLETED"

    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute(
            "SELECT COUNT(*) FROM research_statements WHERE build_id=?",
            (result["build"]["id"],),
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM publication_mentions WHERE build_id=?",
            (result["build"]["id"],),
        ).fetchone()[0] == 3
        manifests = list(
            connection.execute(
                "SELECT * FROM graph_export_partitions WHERE build_id=? ORDER BY partition_key",
                (result["build"]["id"],),
            )
        )
        assert {row["partition_key"] for row in manifests} == set(PARTITIONS)
        for manifest in manifests:
            digest = hashlib.sha256()
            rows = connection.execute(
                "SELECT row_checksum FROM graph_export_rows "
                "WHERE build_id=? AND partition_key=? ORDER BY row_key",
                (result["build"]["id"], manifest["partition_key"]),
            )
            count = 0
            for row in rows:
                digest.update(row[0].encode("ascii"))
                digest.update(b"\n")
                count += 1
            assert count == manifest["row_count"]
            assert digest.hexdigest() == manifest["checksum"]


@pytest.mark.asyncio
async def test_publication_ascii_semicolon_is_one_graph_mention(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_SKIP_NEO4J", "1")
    settings = _settings(tmp_path, batch=1)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=1)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE professors SET publications=? WHERE id=1",
            ("A; B; One title. 2024；Second title. 2023",),
        )
    result = await create_build(["测试大学"], settings)
    with sqlite3.connect(settings.catalog_path) as connection:
        rows = connection.execute(
            "SELECT normalized_text FROM publication_mentions WHERE build_id=? ORDER BY normalized_text",
            (result["build"]["id"],),
        ).fetchall()
    assert len(rows) == 2
    assert any("A; B;" in row[0] for row in rows)
