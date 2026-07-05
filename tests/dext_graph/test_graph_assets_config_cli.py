import json

import pytest
from click.testing import CliRunner

from dext_graph.assets import load_queries, load_sentinels
from dext_graph.catalog.progress import ProgressEvent
from dext_graph.cli import _CatalogProgressReporter, main
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


def test_topic_llm_api_key_uses_dedicated_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEXT_TOPIC_LLM_API_KEY", "topic-secret")

    assert GraphSettings(_env_file=None).topic_llm_api_key == "topic-secret"


def test_topic_llm_api_key_does_not_fallback_to_deepseek(monkeypatch):
    monkeypatch.delenv("DEXT_TOPIC_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "core-secret")

    assert GraphSettings(_env_file=None).topic_llm_api_key == ""


def test_topic_llm_api_key_prefers_dedicated_env_when_both_set(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "core-secret")
    monkeypatch.setenv("DEXT_TOPIC_LLM_API_KEY", "topic-secret")

    assert GraphSettings(_env_file=None).topic_llm_api_key == "topic-secret"


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
        "rollback-promotion",
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


def test_cli_rollback_promotion_invokes_lifecycle(monkeypatch):
    calls = []

    async def fake_rollback(build_id, settings):
        calls.append((build_id, settings.catalog_path))
        return {
            "build": {"id": build_id, "status": "READY"},
            "promotion": {"status": "ROLLED_BACK"},
        }

    import dext_graph.catalog.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "rollback_promotion", fake_rollback)
    runner = CliRunner()
    result = runner.invoke(main, ["graph", "rollback-promotion", "build-1", "--json"])
    assert result.exit_code == 0, result.output
    assert calls and calls[0][0] == "build-1"
    assert json.loads(result.output)["promotion"]["status"] == "ROLLED_BACK"


def test_progress_bar_key_reuses_line_for_same_build_with_different_messages():
    first = ProgressEvent(
        stage="vector",
        action="progress",
        build_id="build-1",
        message="dext_professors__build-1",
        current=1,
        total=10,
    )
    second = ProgressEvent(
        stage="vector",
        action="progress",
        build_id="build-1",
        message="embedding batch ready",
        current=2,
        total=10,
    )
    other_build = ProgressEvent(
        stage="vector",
        action="progress",
        build_id="build-2",
        message="embedding batch ready",
        current=2,
        total=10,
    )

    assert _CatalogProgressReporter._event_key(first) == _CatalogProgressReporter._event_key(second)
    assert _CatalogProgressReporter._event_key(first) != _CatalogProgressReporter._event_key(other_build)


def test_graph_build_progress_uses_stderr_and_keeps_stdout_summary(monkeypatch):
    long_statement_id = (
        "0ee55d931865a5562fe90d4e6085899d"
        "288c6a1baa0ae87ea888250143bf8d70"
    )

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
                    counters={
                        "observations": 1,
                        "statement_id": long_statement_id,
                    },
                )
            )
        return {"build": {"id": "build-1", "status": "CURATING"}}

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Build build-1: CURATING\n"
        "Full details: uv run dext graph status build-1\n"
    )
    assert "[ingest] 测试大学 [" in result.stderr
    assert "###############---------------" in result.stderr
    assert "50.0% 1/2" in result.stderr
    assert "observations=1" in result.stderr
    assert "statement_id" not in result.stderr


def test_graph_build_no_progress_suppresses_stderr(monkeypatch):
    async def fake_create_build(universities, settings, *, progress=None):
        assert progress is None
        return {"build": {"id": "build-1", "status": "CURATING"}}

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build", "--no-progress"])

    assert result.exit_code == 0
    assert result.stdout == (
        "Build build-1: CURATING\n"
        "Full details: uv run dext graph status build-1\n"
    )
    assert result.stderr == ""


def test_graph_build_summary_omits_large_json_details(monkeypatch):
    payload = {
        "build": {"id": "build-1", "status": "VALIDATING"},
        "sources": [
            {
                "status": "completed",
                "source_path": "large-source.json",
            }
        ],
        "checkpoints": [
            {
                "sink": "qdrant",
                "partition_key": "professors",
                "rows_written": 19642,
                "last_batch_id": "5933431-heavy-detail",
            }
        ],
        "vector": {
            "status": "COMPLETED",
            "summary_json": {
                "collection_name": "dext_professors__build-1",
                "eligible_professors": 19642,
                "qdrant_count": 19642,
            },
        },
    }

    async def fake_create_build(universities, settings, *, progress=None):
        assert progress is None
        return payload

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build", "--no-progress"])

    assert result.exit_code == 0
    assert "Build build-1: VALIDATING" in result.stdout
    assert "Sources: 1 total completed=1" in result.stdout
    assert "Stages: vector=COMPLETED" in result.stdout
    assert (
        "Vector: eligible=19642 qdrant=19642 uploaded=19642 "
        "collection=dext_professors__build-1"
    ) in result.stdout
    assert "Full details: uv run dext graph status build-1" in result.stdout
    assert "source_path" not in result.stdout
    assert "large-source.json" not in result.stdout
    assert "last_batch_id" not in result.stdout
    assert "5933431-heavy-detail" not in result.stdout


def test_graph_build_json_flag_preserves_full_stdout_json(monkeypatch):
    payload = {
        "build": {"id": "build-1", "status": "CURATING"},
        "sources": [{"status": "completed", "source_path": "large-source.json"}],
    }

    async def fake_create_build(universities, settings, *, progress=None):
        assert progress is None
        return payload

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build", "--no-progress", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == payload
    assert result.stderr == ""


def test_graph_build_failed_status_suppresses_full_stdout_json(monkeypatch):
    async def fake_create_build(universities, settings, *, progress=None):
        return {
            "build": {
                "id": "build-1",
                "status": "FAILED",
                "last_error": "neo4j boom",
            },
            "sources": [{"source_path": "large-source.json"}],
        }

    monkeypatch.setattr("dext_graph.catalog.workflow.create_build", fake_create_build)
    runner = CliRunner()

    result = runner.invoke(main, ["graph", "build"])

    assert result.exit_code != 0
    assert result.stdout == ""
    assert "build-1" in result.stderr
    assert "neo4j boom" in result.stderr
    assert "graph status build-1" in result.stderr
    assert '"sources"' not in result.stderr
