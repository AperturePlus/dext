from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from dext_graph.catalog import workflow
from dext_graph.catalog.db import CatalogError, initialize_catalog
from dext_graph.catalog.models import BuildSource
from dext_graph.catalog.progress import ProgressEvent
from dext_graph.config import GraphSettings

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "import_old_university_sources.py"
SPEC = importlib.util.spec_from_file_location("import_old_university_sources", SCRIPT_PATH)
import_old = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = import_old
assert SPEC.loader is not None
SPEC.loader.exec_module(import_old)


def _settings(tmp_path: Path, *, universities: str | None = None) -> GraphSettings:
    source_dir = tmp_path / "universities"
    source_dir.mkdir(exist_ok=True)
    seed = tmp_path / "entrances.yaml"
    seed.write_text(
        universities
        or (
            "version: 1\n"
            "universities:\n"
            "  - name: 测试大学\n"
            "    abbr: test\n"
            "    url: https://www.test.edu.cn/\n"
            "    org_unit_listing_urls:\n"
            "      - https://www.test.edu.cn/departments\n"
            "  - name: 北京航空航天大学\n"
            "    url: https://www.buaa.edu.cn/\n"
            "    org_unit_listing_urls:\n"
            "      - https://www.buaa.edu.cn/departments\n"
        ),
        encoding="utf-8",
    )
    return GraphSettings(
        catalog_path=tmp_path / "catalog" / "catalog.db",
        source_data_dir=source_dir,
        seed_path=seed,
        build_max_rss_mb=2048,
    )


def _legacy_source_db(
    path: Path,
    *,
    university_name: str,
    abbr: str | None = None,
    crawl_status: str = "completed",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        meta_abbr_column = ", abbr TEXT" if abbr is not None else ""
        meta_abbr_value = ", ?" if abbr is not None else ""
        connection.executescript(
            f"""
            CREATE TABLE university_meta(name TEXT{meta_abbr_column}, crawl_status TEXT);
            CREATE TABLE professors(
              id INTEGER PRIMARY KEY,
              name TEXT,
              org_unit_name TEXT,
              homepage TEXT
            );
            CREATE TABLE org_units(id INTEGER PRIMARY KEY, name TEXT, status TEXT);
            CREATE TABLE professor_affiliations(
              id INTEGER PRIMARY KEY,
              professor_id INTEGER,
              org_unit_id INTEGER
            );
            """
        )
        connection.execute(
            f"INSERT INTO university_meta VALUES (?{meta_abbr_value}, ?)",
            ((university_name, abbr, crawl_status) if abbr is not None else (university_name, crawl_status)),
        )
        connection.execute("INSERT INTO org_units VALUES (1, '计算机学院', 'completed')")
        connection.execute(
            "INSERT INTO professors VALUES (1, '教师1', '计算机学院', 'https://example.edu.cn/p/1')"
        )
        connection.execute("INSERT INTO professor_affiliations VALUES (1, 1, 1)")


def _active_catalog(
    settings: GraphSettings,
    tmp_path: Path,
    *,
    university_name: str = "测试大学",
    abbr: str = "test",
) -> tuple[Path, Path]:
    snapshot = tmp_path / "snapshots" / f"{abbr}-snapshot.db"
    mutable = tmp_path / "mutable" / f"{abbr}.db"
    _legacy_source_db(snapshot, university_name=university_name, abbr=abbr)
    mutable.parent.mkdir(parents=True, exist_ok=True)
    mutable.write_bytes(b"mutable source should not be used")
    initialize_catalog(settings.catalog_path)
    source = BuildSource(
        university_id=f"univ:{abbr}",
        university_name=university_name,
        abbr=abbr,
        source_path=str(mutable),
        ordinal=0,
    )
    with sqlite3.connect(settings.catalog_path) as connection:
        workflow._insert_build(connection, "active-build", [source], settings)
        connection.execute(
            """
            INSERT INTO source_snapshots(
              id, university_id, source_path, snapshot_path, file_hash,
              schema_version, row_counts_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, '{}', ?)
            """,
            (
                f"snapshot-{abbr}",
                f"univ:{abbr}",
                str(mutable),
                str(snapshot),
                f"hash-{abbr}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.execute(
            """
            UPDATE build_source_tasks
            SET status='COMPLETED', source_snapshot_id=?, rows_read=1, updated_at=?
            WHERE build_id='active-build' AND university_id=?
            """,
            (f"snapshot-{abbr}", "2026-01-01T00:00:00+00:00", f"univ:{abbr}"),
        )
        connection.execute(
            "INSERT INTO build_source_snapshots VALUES ('active-build', ?)",
            (f"snapshot-{abbr}",),
        )
        connection.execute(
            """
            UPDATE graph_builds
            SET status='ACTIVE', finished_at='2026-01-01T00:00:00+00:00'
            WHERE id='active-build'
            """
        )
        connection.commit()
    return snapshot, mutable


def _old_dir(tmp_path: Path) -> Path:
    old = tmp_path / "old"
    _legacy_source_db(
        old / "buaa.edu.cn.db",
        university_name="北京航空航天大学",
        crawl_status="failed",
    )
    return old


def test_active_baseline_uses_snapshot_path_not_mutable_source(tmp_path):
    settings = _settings(tmp_path)
    snapshot, mutable = _active_catalog(settings, tmp_path)

    active_id, sources, details = import_old.load_active_baseline_sources(
        settings.catalog_path
    )

    assert active_id == "active-build"
    assert sources[0].source_path == str(snapshot.resolve())
    assert details[0]["original_source_path"] == str(mutable)


def test_old_filename_host_matches_manifest_abbr(tmp_path):
    settings = _settings(tmp_path)
    old_sources = import_old.resolve_old_sources(_old_dir(tmp_path), settings)

    assert len(old_sources) == 1
    assert old_sources[0].source.university_id == "univ:buaa"
    assert old_sources[0].source.abbr == "buaa"
    assert old_sources[0].professor_count == 1


def test_dry_run_does_not_write_catalog(tmp_path):
    settings = _settings(tmp_path)
    _active_catalog(settings, tmp_path)

    payload = asyncio.run(
        import_old.execute_import(
            settings,
            old_dir=_old_dir(tmp_path),
            dry_run=True,
            no_promote=True,
        )
    )

    assert payload["dry_run"] is True
    assert payload["plan"]["counts"] == {
        "baseline_sources": 1,
        "old_sources": 1,
        "combined_sources": 2,
    }
    assert not (settings.catalog_path.parent / "backups").exists()
    with sqlite3.connect(settings.catalog_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM graph_builds").fetchone()[0] == 1


def test_default_run_backs_up_then_builds_validates_and_promotes(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    _active_catalog(settings, tmp_path)
    calls: list[tuple] = []

    async def fake_create_build_from_sources(sources, call_settings, *, progress=None, backup_catalog=True):
        calls.append(("build", [source.university_id for source in sources], backup_catalog))
        assert call_settings is settings
        return {"build": {"id": "new-build", "status": "VALIDATING"}}

    async def fake_validate_build(build_id, call_settings, *, skip_gold_gates=False):
        calls.append(("validate", build_id, skip_gold_gates))
        assert call_settings is settings
        return {"build": {"id": build_id, "status": "READY"}}

    async def fake_promote_build(build_id, call_settings):
        calls.append(("promote", build_id))
        assert call_settings is settings
        return {"build": {"id": build_id, "status": "ACTIVE"}}

    monkeypatch.setattr(import_old, "create_build_from_sources", fake_create_build_from_sources)
    monkeypatch.setattr(import_old, "validate_build", fake_validate_build)
    monkeypatch.setattr(import_old, "promote_build", fake_promote_build)

    payload = asyncio.run(import_old.execute_import(settings, old_dir=_old_dir(tmp_path)))

    assert calls == [
        ("build", ["univ:test", "univ:buaa"], False),
        ("validate", "new-build", True),
        ("promote", "new-build"),
    ]
    backup = payload["backup"]
    assert Path(backup["backup_path"]).is_file()
    assert Path(backup["checksum_path"]).is_file()
    manifest = json.loads(Path(backup["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["active_build_id"] == "active-build"
    assert manifest["new_build_id"] == "new-build"
    assert manifest["plan"]["counts"]["combined_sources"] == 2


def test_duplicate_old_source_requires_replace_existing(tmp_path):
    settings = _settings(
        tmp_path,
        universities=(
            "version: 1\n"
            "universities:\n"
            "  - name: 北京航空航天大学\n"
            "    url: https://www.buaa.edu.cn/\n"
            "    org_unit_listing_urls:\n"
            "      - https://www.buaa.edu.cn/departments\n"
        ),
    )
    _active_catalog(settings, tmp_path, university_name="北京航空航天大学", abbr="buaa")
    old = _old_dir(tmp_path)

    with pytest.raises(CatalogError, match="already exist"):
        import_old.prepare_import_plan(settings, old_dir=old)

    plan = import_old.prepare_import_plan(
        settings,
        old_dir=old,
        replace_existing=True,
    )

    assert [source.university_id for source in plan.combined_sources] == ["univ:buaa"]
    assert plan.combined_sources[0].source_path.endswith("buaa.edu.cn.db")


def test_progress_reporter_disabled_returns_none():
    assert import_old._progress_reporter(False) is None


def test_compact_progress_reporter_prints_immediate_events(capsys):
    reporter = import_old._CompactProgressReporter(clock=lambda: 0.0)

    reporter(
        ProgressEvent(
            stage="ingest",
            action="started",
            build_id="build-1",
            message="univ:test",
            counters={"ignored": "value"},
        )
    )
    reporter(
        ProgressEvent(
            stage="ingest",
            action="failed",
            build_id="build-1",
            message="univ:test",
            counters={"error": "boom", "ignored": "value"},
        )
    )
    reporter(
        ProgressEvent(
            stage="ingest",
            action="completed",
            build_id="build-1",
            message="univ:test",
            counters={"ignored": "value"},
        )
    )

    assert capsys.readouterr().err.splitlines() == [
        "[ingest] univ:test started",
        "[ingest] univ:test failed error=boom",
        "[ingest] univ:test completed",
    ]


def test_compact_progress_reporter_throttles_progress(capsys):
    now = 0.0

    def clock() -> float:
        return now

    reporter = import_old._CompactProgressReporter(clock=clock)
    event_kwargs = {
        "stage": "ingest",
        "action": "progress",
        "build_id": "build-1",
        "message": "univ:test",
        "total": 10,
    }

    reporter(ProgressEvent(**event_kwargs, current=1))
    now = 0.2
    reporter(ProgressEvent(**event_kwargs, current=2))
    now = 0.9
    reporter(ProgressEvent(**event_kwargs, current=3))
    now = 1.0
    reporter(ProgressEvent(**event_kwargs, current=4))

    assert capsys.readouterr().err.splitlines() == [
        "[ingest] univ:test 10.0% 1/10",
        "[ingest] univ:test 40.0% 4/10",
    ]
