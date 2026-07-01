from click.testing import CliRunner

from dext_graph.assets import load_queries, load_sentinels
from dext_graph.cli import main
from dext_graph.config import GraphSettings
from dext_graph.catalog.rules import load_curation_rules


def test_versioned_assets_have_required_sizes():
    query_version, queries, query_hash = load_queries()
    sentinel_version, sentinels, sentinel_hash = load_sentinels()
    assert query_version == "research-interests-zh-en-v1"
    assert len(queries) == 40
    assert len(query_hash) == 64
    assert sentinel_version == "sentinels-v1"
    assert len(sentinels) == 5
    assert len(sentinel_hash) == 64
    rules = load_curation_rules()
    assert rules.version == "curation-v1"
    assert rules.normalization_version == "normalization-v1"
    assert len(rules.manifest_hash) == 64


def test_safe_settings_snapshot_excludes_key():
    settings = GraphSettings(
        embedding_api_key="must-not-appear",
        topic_llm_api_key="topic-secret",
        neo4j_password="also-secret",
    )
    snapshot = settings.safe_snapshot()
    assert "embedding_api_key" not in snapshot
    assert "neo4j_password" not in snapshot
    assert "topic_llm_api_key" not in snapshot
    assert "must-not-appear" not in repr(settings)
    assert "also-secret" not in repr(settings)
    assert "topic-secret" not in repr(settings)
    assert settings.catalog_path.as_posix() == "data/catalog/catalog.db"
    assert settings.build_read_batch == 100
    assert settings.build_write_queue == 2
    assert settings.build_max_rss_mb == 1024
    assert settings.build_min_source_retention_ratio == 0.8
    assert settings.curation_queue == 16
    assert settings.build_neo4j_batch == 200
    assert settings.embedding_queue_maxsize == 8
    assert settings.bm25_tokenizer_version == "bm25-simple-v1"


def test_cli_exposes_nested_value_validation_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["graph", "value-validation", "--help"])
    assert result.exit_code == 0
    for command in ("run", "pool", "compare", "finalize", "cleanup"):
        assert command in result.output


def test_cli_exposes_catalog_build_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["graph", "--help"])
    assert result.exit_code == 0
    for command in (
        "build", "resume", "status", "vector", "validate", "promote",
        "topics", "gold", "curation-gold", "value-validation"
    ):
        assert command in result.output
    build_help = runner.invoke(main, ["graph", "build", "--help"])
    assert build_help.exit_code == 0
    assert "--university" in build_help.output
    gold_help = runner.invoke(main, ["graph", "gold", "--help"])
    assert gold_help.exit_code == 0
    assert "generate" in gold_help.output
    assert "evaluate" in gold_help.output
    topics_help = runner.invoke(main, ["graph", "topics", "--help"])
    assert topics_help.exit_code == 0
    for command in ("build", "suggest-merges", "gold-generate", "gold-evaluate"):
        assert command in topics_help.output
