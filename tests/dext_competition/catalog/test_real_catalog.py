from __future__ import annotations

from pathlib import Path

from dext_competition.catalog import FileCompetitionCatalog, build_catalog
from dext_competition.contracts.catalog import CompetitionCategory
from dext_competition.index import FileKnowledgeIndex, build_index


CORPUS_ROOT = Path(__file__).resolve().parents[3] / "data" / "竞赛助手"


async def test_real_corpus_extracts_exact_g2_inventory(tmp_path: Path):
    index_dir = tmp_path / "index"
    build_index(CORPUS_ROOT, index_dir)
    knowledge = FileKnowledgeIndex(index_dir)
    result = await build_catalog(knowledge, tmp_path / "catalog")
    repository = FileCompetitionCatalog(
        result.artifact_dir,
        expected_knowledge_base_version=knowledge.manifest().version_id,
    )
    cards = await repository.list_competitions()
    assert len(cards) == 90
    assert sum(card.in_2024_catalog for card in cards) == 84
    assert len({card.competition_id for card in cards}) == 90
    assert all(card.internal_source_refs for card in cards)
    assert all(card.field_evidence for card in cards)
    assert {card.category for card in cards} <= {item.value for item in CompetitionCategory}
    categories = {card.display_name: card.category for card in cards}
    assert categories["全国大学生机械创新设计大赛"] == CompetitionCategory.ENGINEERING.value
    assert categories["全国大学生电子商务“创新、创意及创业”挑战赛"] == CompetitionCategory.MANAGEMENT.value
    assert categories["全国大学生化工设计竞赛"] == CompetitionCategory.ENGINEERING.value
    assert categories["全国大学生工业设计大赛"] == CompetitionCategory.LANGUAGE_ART.value
    assert repository.manifest().card_count == 90
    assert repository.manifest().in_2024_catalog_count == 84


async def test_real_catalog_build_is_byte_reproducible(tmp_path: Path):
    index_dir = tmp_path / "index"
    build_index(CORPUS_ROOT, index_dir)
    knowledge = FileKnowledgeIndex(index_dir)
    first = tmp_path / "first"
    second = tmp_path / "second"
    result_a = await build_catalog(knowledge, first)
    result_b = await build_catalog(knowledge, second)
    assert result_a.manifest == result_b.manifest
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
