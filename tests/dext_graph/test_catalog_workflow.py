import sqlite3
from pathlib import Path

import pytest

from dext_graph.catalog import workflow
from dext_graph.catalog.db import CatalogError, snapshot_protection_reasons
from dext_graph.catalog.evidence import set_graph_export_pruned
from dext_graph.catalog.workflow import (
    create_build,
    get_status,
    resolve_build_sources,
    resume_build,
)
from dext_graph.config import GraphSettings


def _source_db(path: Path, *, count: int = 3, status: str = "completed") -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE university_meta(
              id INTEGER PRIMARY KEY, name TEXT, abbr TEXT, crawl_status TEXT,
              schema_version INTEGER
            );
            CREATE TABLE professors(
              id INTEGER PRIMARY KEY, name TEXT, org_unit_name TEXT, title TEXT,
              research_areas TEXT, email TEXT, phone TEXT, homepage TEXT,
              external_link TEXT, bio TEXT, enrollment_pref TEXT, publications TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE org_units(id INTEGER PRIMARY KEY, name TEXT, status TEXT);
            CREATE TABLE professor_affiliations(
              id INTEGER PRIMARY KEY, professor_id INTEGER, org_unit_id INTEGER
            );
            CREATE TABLE crawl_page_cache(
              url TEXT PRIMARY KEY, content_hash TEXT, title TEXT,
              text_snapshot TEXT, updated_at TEXT
            );
            CREATE TABLE crawl_graph_nodes(id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE crawl_extraction_attempts(id INTEGER PRIMARY KEY, status TEXT);
            """
        )
        connection.execute(
            "INSERT INTO university_meta VALUES (1, '测试大学', 'test', ?, 1)",
            (status,),
        )
        connection.execute("INSERT INTO org_units VALUES (1, '计算机学院', 'completed')")
        connection.execute("INSERT INTO crawl_graph_nodes VALUES (1, 'done')")
        for source_id in range(1, count + 1):
            url = f"https://cs.example.edu.cn/prof/{source_id}"
            connection.execute(
                "INSERT INTO professors VALUES (?, ?, '计算机学院', '教授', ?, ?, NULL, ?, "
                "NULL, ?, '博导', ?, 'created', 'updated')",
                (
                    source_id,
                    f"教师{source_id}",
                    f"方向{source_id}",
                    f"p{source_id}@example.edu.cn",
                    url,
                    f"简介{source_id}",
                    f"论文{source_id}",
                ),
            )
            connection.execute(
                "INSERT INTO professor_affiliations VALUES (?, ?, 1)",
                (source_id, source_id),
            )
            connection.execute(
                "INSERT INTO crawl_page_cache VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
                (url, f"hash-{source_id}", f"教师{source_id}", f"正文{source_id}\n第二行"),
            )


def _settings(tmp_path: Path, *, batch: int = 2, ratio: float = 0.8) -> GraphSettings:
    source_dir = tmp_path / "universities"
    source_dir.mkdir(exist_ok=True)
    seed = tmp_path / "entrances.yaml"
    seed.write_text(
        "version: 1\nuniversities:\n  - name: 测试大学\n    abbr: test\n"
        "    url: https://www.test.edu.cn/\n    org_unit_listing_urls:\n"
        "      - https://www.test.edu.cn/departments\n",
        encoding="utf-8",
    )
    return GraphSettings(
        catalog_path=tmp_path / "catalog" / "catalog.db",
        source_data_dir=source_dir,
        seed_path=seed,
        build_read_batch=batch,
        build_write_queue=2,
        build_max_rss_mb=2048,
        build_min_source_retention_ratio=ratio,
    )


def _patch_runtime(monkeypatch) -> None:
    monkeypatch.setenv("DEXT_TEST_SKIP_NEO4J", "1")
    monkeypatch.setenv("DEXT_TEST_SKIP_VECTOR", "1")
    monkeypatch.setattr(workflow, "_check_rss", lambda settings: 123456)
    monkeypatch.setattr(
        workflow,
        "_compress_text",
        lambda value: None if value is None else b"test-zstd:" + value.encode("utf-8"),
    )


def _counts(catalog: Path) -> dict[str, int]:
    with sqlite3.connect(catalog) as connection:
        return {
            "snapshots": connection.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0],
            "documents": connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0],
            "observations": connection.execute(
                "SELECT COUNT(*) FROM professor_observations"
            ).fetchone()[0],
            "active": connection.execute(
                "SELECT COUNT(*) FROM professor_observations WHERE active=1"
            ).fetchone()[0],
        }


def _backup_paths(settings: GraphSettings) -> set[Path]:
    return set((settings.catalog_path.parent / "backups").glob("catalog-*.db"))


def _prune_exports_for_resume_test(
    catalog: Path,
    build_id: str,
    *,
    build_status: str | None = None,
    graph_status: str = "COMPLETED",
    stale_checkpoint: bool = False,
) -> None:
    with sqlite3.connect(catalog) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DELETE FROM graph_export_rows WHERE build_id=?", (build_id,))
        connection.execute("DELETE FROM graph_export_partitions WHERE build_id=?", (build_id,))
        connection.execute(
            "DELETE FROM sink_checkpoints WHERE build_id=? AND sink IN ('graph_export','neo4j')",
            (build_id,),
        )
        if stale_checkpoint:
            connection.execute(
                """
                INSERT INTO sink_checkpoints(
                  build_id,sink,partition_key,last_key,last_batch_id,rows_written,updated_at
                ) VALUES (?, 'graph_export', 'node:Professor', 'zzzz', NULL, 999, 'now')
                """,
                (build_id,),
            )
            connection.execute(
                """
                INSERT INTO sink_checkpoints(
                  build_id,sink,partition_key,last_key,last_batch_id,rows_written,updated_at
                ) VALUES (?, 'neo4j', 'node:Professor', 'zzzz', NULL, 999, 'now')
                """,
                (build_id,),
            )
        connection.execute(
            "UPDATE graph_runs SET status=?, last_error=NULL WHERE build_id=?",
            (graph_status, build_id),
        )
        if build_status is not None:
            connection.execute(
                "UPDATE graph_builds SET status=?, last_error=NULL WHERE id=?",
                (build_status, build_id),
            )
        set_graph_export_pruned(connection, build_id, True)
        connection.commit()


def test_default_source_selection_uses_only_manifest_canonical_db(tmp_path):
    settings = _settings(tmp_path)
    (settings.source_data_dir / "test.db").write_bytes(b"canonical")
    (settings.source_data_dir / "test.merged.work.db").write_bytes(b"work")
    selected = resolve_build_sources([], settings)
    assert [Path(item.source_path).name for item in selected] == ["test.db"]


async def test_legacy_build_is_idempotent_and_reaches_curating(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=3)
    _patch_runtime(monkeypatch)
    first = await create_build(["测试大学"], settings)
    assert first["build"]["status"] == "WRITING_VECTOR"
    assert first["build"]["summary_json"]["observations_inserted"] == 3
    assert first["sources"][0]["deactivation_eligible"] is True
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        observation = connection.execute(
            "SELECT * FROM professor_observations ORDER BY source_professor_id LIMIT 1"
        ).fetchone()
        assert observation["provenance_grade"] == "legacy_merged"
        assert observation["source_page_kind"] == "unknown"
        assert "教师1" in observation["payload_json"]
        assert {
            row[0]
            for row in connection.execute(
                "SELECT sink FROM sink_checkpoints WHERE build_id=?",
                (first["build"]["id"],),
            )
        } == {
            "observations",
            "curation_identity",
            "curation_fields",
            "curation_canonical",
            "graph_evidence",
            "graph_export",
        }
        checkpoint = connection.execute(
            "SELECT last_batch_id FROM sink_checkpoints WHERE build_id=? AND sink='observations'",
            (first["build"]["id"],),
        ).fetchone()[0]
        assert len(checkpoint) == 64
        assert connection.execute(
            "SELECT fetched_at FROM source_documents ORDER BY id LIMIT 1"
        ).fetchone()[0].endswith("+00:00")
    second = await create_build(["测试大学"], settings)
    assert second["build"]["status"] == "WRITING_VECTOR"
    assert second["build"]["summary_json"]["observations_inserted"] == 0
    assert second["build"]["summary_json"]["observations_reused"] == 3
    assert second["build"]["summary_json"]["source_snapshots_reused"] == 1
    assert _counts(settings.catalog_path) == {
        "snapshots": 1,
        "documents": 3,
        "observations": 3,
        "active": 3,
    }
    assert list((settings.catalog_path.parent / "backups").glob("catalog-*.db"))
    assert list((settings.catalog_path.parent / "backups").glob("catalog-*.db.sha256"))
    listing = get_status(settings=settings)
    assert [item["id"] for item in listing["builds"]][:2] == [
        second["build"]["id"],
        first["build"]["id"],
    ]
    assert (await resume_build(second["build"]["id"], settings))["build"]["status"] == "WRITING_VECTOR"


async def test_create_build_emits_snapshot_and_ingest_progress(tmp_path, monkeypatch):
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=2)
    _patch_runtime(monkeypatch)
    monkeypatch.setenv("DEXT_TEST_STOP_AFTER_INGEST", "1")
    events = []

    result = await create_build(["测试大学"], settings, progress=events.append)

    assert result["build"]["status"] == "CURATING"
    assert any(event.stage == "snapshot" and event.action == "started" for event in events)
    assert any(event.stage == "snapshot" and event.action == "completed" for event in events)
    ingest_progress = [
        event
        for event in events
        if event.stage == "ingest" and event.action == "progress"
    ]
    assert ingest_progress
    assert ingest_progress[-1].current == 2
    assert ingest_progress[-1].total == 2


async def test_resume_rejects_incompatible_frozen_settings(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=1)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    changed = settings.model_copy(update={"build_read_batch": settings.build_read_batch + 1})
    with pytest.raises(CatalogError, match="build_read_batch"):
        await resume_build(result["build"]["id"], changed)


async def test_resume_allows_topic_llm_base_url_change(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=1)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    changed = settings.model_copy(
        update={"topic_llm_base_url": "https://topic-llm.example.test"}
    )

    resumed = await resume_build(result["build"]["id"], changed)

    assert resumed["build"]["status"] == "WRITING_VECTOR"


async def test_resume_allows_topic_llm_model_change(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=1)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    changed = settings.model_copy(update={"topic_llm_model": "topic-runtime-model"})

    resumed = await resume_build(result["build"]["id"], changed)

    assert resumed["build"]["status"] == "WRITING_VECTOR"


async def test_resume_allows_embedding_max_concurrency_change(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=1)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    changed = settings.model_copy(
        update={"embedding_max_concurrency": settings.embedding_max_concurrency + 1}
    )

    resumed = await resume_build(result["build"]["id"], changed)

    assert resumed["build"]["status"] == "WRITING_VECTOR"


async def test_catalog_never_persists_embedding_api_key(tmp_path, monkeypatch):
    settings = _settings(tmp_path).model_copy(
        update={"embedding_api_key": "stage-one-secret-must-not-persist"}
    )
    _source_db(settings.source_data_dir / "test.db", count=1)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    assert "stage-one-secret-must-not-persist" not in repr(result)
    assert b"stage-one-secret-must-not-persist" not in settings.catalog_path.read_bytes()


async def test_resume_after_batch_failure_uses_checkpoint(tmp_path, monkeypatch):
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=3)
    _patch_runtime(monkeypatch)
    original = workflow._commit_batch
    calls = 0

    def fail_second(connection, build_id, university_id, batch):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected batch failure")
        return original(connection, build_id, university_id, batch)

    monkeypatch.setattr(workflow, "_commit_batch", fail_second)
    failed = await create_build(["测试大学"], settings)
    assert failed["build"]["status"] == "FAILED"
    assert failed["checkpoints"][0]["last_key"] == "1"
    backups_before = _backup_paths(settings)
    monkeypatch.setattr(workflow, "_commit_batch", original)
    resumed = await resume_build(failed["build"]["id"], settings)
    assert resumed["build"]["status"] == "WRITING_VECTOR"
    assert _backup_paths(settings) == backups_before
    assert _counts(settings.catalog_path)["observations"] == 3
    observation_checkpoint = next(
        item for item in resumed["checkpoints"] if item["sink"] == "observations"
    )
    assert observation_checkpoint["last_key"] == "3"


async def test_resume_after_snapshot_failure(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=2)
    _patch_runtime(monkeypatch)
    original = workflow.create_source_snapshot

    async def fail_snapshot(*args, **kwargs):
        raise RuntimeError("injected snapshot failure")

    monkeypatch.setattr(workflow, "create_source_snapshot", fail_snapshot)
    failed = await create_build(["测试大学"], settings)
    assert failed["build"]["status"] == "FAILED"
    assert failed["sources"][0]["source_snapshot_id"] is None
    monkeypatch.setattr(workflow, "create_source_snapshot", original)
    resumed = await resume_build(failed["build"]["id"], settings)
    assert resumed["build"]["status"] == "WRITING_VECTOR"
    assert _counts(settings.catalog_path)["observations"] == 2


async def test_resume_rebuilds_pruned_exports_before_vector_skip(tmp_path, monkeypatch):
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=2)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=?", (build_id,)
        ).fetchone()[0] > 0

    _prune_exports_for_resume_test(
        settings.catalog_path, build_id, stale_checkpoint=True
    )
    resumed = await resume_build(build_id, settings)

    assert resumed["build"]["status"] == "WRITING_VECTOR"
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=?", (build_id,)
        ).fetchone()[0] > 0
        stale = connection.execute(
            "SELECT COUNT(*) FROM sink_checkpoints WHERE build_id=? AND last_key='zzzz'",
            (build_id,),
        ).fetchone()[0]
        assert stale == 0
        graph_export_rows = connection.execute(
            "SELECT COALESCE(SUM(rows_written), 0) FROM sink_checkpoints "
            "WHERE build_id=? AND sink='graph_export'",
            (build_id,),
        ).fetchone()[0]
        assert graph_export_rows > 0
        summary = connection.execute(
            "SELECT summary_json FROM graph_runs WHERE build_id=?", (build_id,)
        ).fetchone()["summary_json"]
        assert "export_pruned" not in summary


async def test_resume_pruned_failed_graph_stage_rebuilds_exports(tmp_path, monkeypatch):
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=2)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    _prune_exports_for_resume_test(
        settings.catalog_path,
        build_id,
        build_status="FAILED",
        graph_status="FAILED",
    )

    resumed = await resume_build(build_id, settings)

    assert resumed["build"]["status"] == "WRITING_VECTOR"
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=?", (build_id,)
        ).fetchone()[0] > 0
        graph = connection.execute(
            "SELECT status,summary_json FROM graph_runs WHERE build_id=?", (build_id,)
        ).fetchone()
        assert graph["status"] == "COMPLETED"
        assert "export_pruned" not in graph["summary_json"]


async def test_resume_pruned_failed_late_stage_rebuilds_exports_before_return(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=2)
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    _prune_exports_for_resume_test(
        settings.catalog_path,
        build_id,
        build_status="FAILED",
        graph_status="COMPLETED",
        stale_checkpoint=True,
    )
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.execute(
            """
            INSERT INTO vector_runs(
              id,build_id,status,profile_template_version,tokenizer_identity,
              sparse_tokenizer_version,collection_name,summary_json
            ) VALUES ('vector-failed', ?, 'FAILED', 'template', 'tokenizer', 'bm25', 'collection', '{}')
            """,
            (build_id,),
        )

    resumed = await resume_build(build_id, settings)

    assert resumed["build"]["status"] == "FAILED"
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM graph_export_rows WHERE build_id=?", (build_id,)
        ).fetchone()[0] > 0
        assert connection.execute(
            "SELECT COUNT(*) FROM sink_checkpoints WHERE build_id=? AND last_key='zzzz'",
            (build_id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("remaining", "eligible", "expected_active"),
    [(80, True, 80), (79, False, 100)],
)
async def test_retention_gate_boundary(
    tmp_path, monkeypatch, remaining, eligible, expected_active
):
    settings = _settings(tmp_path, batch=25)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=100)
    _patch_runtime(monkeypatch)
    first = await create_build(["测试大学"], settings)
    assert first["sources"][0]["deactivation_eligible"] is True
    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM professor_affiliations WHERE professor_id > ?", (remaining,))
        connection.execute("DELETE FROM professors WHERE id > ?", (remaining,))
    second = await create_build(["测试大学"], settings)
    assert second["sources"][0]["deactivation_eligible"] is eligible
    assert _counts(settings.catalog_path)["active"] == expected_active
    if not eligible:
        assert second["unresolved_findings"]["warning"] >= 1


async def test_incomplete_source_never_deactivates_and_protects_finding(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=5)
    _patch_runtime(monkeypatch)
    await create_build(["测试大学"], settings)
    with sqlite3.connect(source) as connection:
        connection.execute("DELETE FROM professor_affiliations WHERE professor_id=5")
        connection.execute("DELETE FROM professors WHERE id=5")
        connection.execute("UPDATE university_meta SET crawl_status='failed'")
    second = await create_build(["测试大学"], settings)
    assert second["sources"][0]["deactivation_eligible"] is False
    assert _counts(settings.catalog_path)["active"] == 5
    snapshot_id = second["sources"][0]["source_snapshot_id"]
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.execute(
            "UPDATE graph_builds SET status='READY' WHERE id=?",
            (second["build"]["id"],),
        )
        connection.execute(
            "INSERT INTO source_snapshot_protections VALUES (?, 'gold_set', 'gold-v1', ?)",
            (snapshot_id, "2026-01-01T00:00:00+00:00"),
        )
    reasons = snapshot_protection_reasons(settings.catalog_path, snapshot_id)
    assert any(reason["kind"] == "unresolved_finding" for reason in reasons)
    assert {reason["kind"] for reason in reasons} >= {"build:ready", "gold_set"}


async def test_page_mismatch_and_empty_text_still_import_observations(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE professors SET homepage='https://cs.example.edu.cn/not-cached' WHERE id=1"
        )
        connection.execute(
            "UPDATE crawl_page_cache SET text_snapshot=NULL "
            "WHERE url='https://cs.example.edu.cn/prof/2'"
        )
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    assert result["build"]["status"] == "WRITING_VECTOR"
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT source_professor_id, source_document_id FROM professor_observations "
            "ORDER BY source_professor_id"
        ).fetchall()
        assert rows[0]["source_document_id"] is None
        assert rows[1]["source_document_id"] is not None
        assert connection.execute(
            "SELECT text_zstd FROM source_documents"
        ).fetchone()[0] is None


async def test_shared_legacy_document_is_conservatively_multi_profile(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE professors SET homepage='https://cs.example.edu.cn/prof/1' WHERE id=2"
        )
    _patch_runtime(monkeypatch)
    await create_build(["测试大学"], settings)
    with sqlite3.connect(settings.catalog_path) as connection:
        kinds = connection.execute(
            "SELECT DISTINCT source_page_kind FROM professor_observations"
        ).fetchall()
        assert kinds == [("multi_profile",)]
        assert connection.execute("SELECT COUNT(*) FROM source_documents").fetchone()[0] == 1


async def test_row_rejection_is_finding_and_blocks_deactivation(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE professors SET name='' WHERE id=2")
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    assert result["build"]["status"] == "WRITING_VECTOR"
    assert result["sources"][0]["rejected_rows"] == 1
    assert result["sources"][0]["deactivation_eligible"] is False
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM quality_findings WHERE code='legacy_row_unimportable'"
        ).fetchone()[0] == 1


async def test_minimal_legacy_source_builds_with_capability_findings(
    tmp_path, monkeypatch
):
    settings = _settings(tmp_path)
    source = settings.source_data_dir / "test.db"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE university_meta(name TEXT, abbr TEXT, crawl_status TEXT);
            CREATE TABLE professors(id INTEGER PRIMARY KEY, name TEXT);
            INSERT INTO university_meta VALUES ('测试大学', 'test', 'completed');
            INSERT INTO professors VALUES (1, '李四');
            """
        )
    _patch_runtime(monkeypatch)
    result = await create_build(["测试大学"], settings)
    assert result["build"]["status"] == "WRITING_VECTOR"
    assert result["build"]["summary_json"]["observations_inserted"] == 1
    assert result["sources"][0]["deactivation_eligible"] is False
    assert result["unresolved_findings"]["warning"] >= 1


def test_real_zstd_roundtrip_when_dependency_is_available():
    import io

    zstandard = pytest.importorskip("zstandard")
    value = "第一行\n第二行"
    compressed = workflow._compress_text(value)
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(compressed)) as reader:
        assert reader.read() == value.encode("utf-8")
