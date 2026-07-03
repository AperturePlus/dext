import json

import pytest
from click.testing import CliRunner

from dext_graph.assets import load_queries, load_sentinels
from dext_graph.catalog.progress import ProgressEvent
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


def test_topic_link_concurrency_default(monkeypatch):
    monkeypatch.delenv("DEXT_TOPIC_LINK_CONCURRENCY", raising=False)
    assert GraphSettings(_env_file=None).topic_link_concurrency == 8


def test_topic_link_concurrency_env_overrides(monkeypatch):
    monkeypatch.setenv("DEXT_TOPIC_LINK_CONCURRENCY", "1")
    assert GraphSettings(_env_file=None).topic_link_concurrency == 1

    monkeypatch.setenv("DEXT_TOPIC_LINK_CONCURRENCY", "16")
    assert GraphSettings(_env_file=None).topic_link_concurrency == 16


@pytest.mark.parametrize("value", ["0", "-1"])
def test_topic_link_concurrency_requires_positive(monkeypatch, value):
    monkeypatch.setenv("DEXT_TOPIC_LINK_CONCURRENCY", value)
    with pytest.raises(ValueError, match="configuration value must be positive"):
        GraphSettings(_env_file=None)


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


def test_graph_build_progress_uses_stderr_and_keeps_stdout_json(monkeypatch):
    async def fake_create_build(universities, settings, *, progress=None):
        assert universities == []
        assert isinstance(settings, GraphSettings)
        if progress is not None:
            progress(
                ProgressEvent(
                    stage="ingest",
                    action="progress",
                    build_id="build-1",
                    message="测试大学",
                    current=1,
                    total=2,
                    counters={"observations": 1},
                )
            )
        return {"build": {"id": "build-1", "status": "CURATING"}}

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"build": {"id": "build-1", "status": "CURATING"}}
    assert "[ingest] progress build=build-1 测试大学 1/2" in result.stderr


def test_graph_build_no_progress_suppresses_stderr(monkeypatch):
    async def fake_create_build(universities, settings, *, progress=None):
        assert progress is None
        return {"build": {"id": "build-1", "status": "CURATING"}}

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build", "--no-progress"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"build": {"id": "build-1", "status": "CURATING"}}
    assert result.stderr == ""
