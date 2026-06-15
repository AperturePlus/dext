import os
import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from dext.config import Settings, get_settings
from dext.engine import CrawlSummary
from dext.seed import UniversitySeed, resolve_abbr
from dext.storage.lifecycle import open_fresh
from dext.storage.models import CrawlRun


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    for key in list(os.environ):
        if key.startswith("DEXT_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@dataclass
class _DummyServer:
    cleaned: bool = False

    async def cleanup(self):
        self.cleaned = True


class _DummyEngine:
    statuses: list[str] = []
    calls: list[str] = []
    exc = None

    def __init__(self, storage, bridge, llm_client, settings, run_id, *, university_name, decision_center=None):
        self.storage = storage
        self.run_id = run_id
        self.university_name = university_name
        self.__class__.calls.append(university_name)

    async def run(self):
        if self.__class__.exc is not None:
            raise self.__class__.exc
        status = self.__class__.statuses.pop(0) if self.__class__.statuses else "completed"
        summary = CrawlSummary(status=status)
        await self.storage.writer.finish_run(self.run_id, status=status, summary=summary.asdict())
        await self.storage.writer.update_university_status("completed" if status == "completed" else "failed")
        return summary


def _seed_file(tmp_path: Path) -> Path:
    path = tmp_path / "entrances.yaml"
    path.write_text(
        "\n".join(
            [
                "version: 1",
                "universities:",
                "  - name: Alpha University",
                "    url: https://alpha.edu.cn/",
                "    org_unit_listing_urls:",
                "      - https://alpha.edu.cn/schools.htm",
                "  - name: Beta University",
                "    url: https://beta.edu.cn/",
                "    org_unit_listing_urls:",
                "      - https://beta.edu.cn/schools.htm",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _settings(tmp_path: Path, *, api_key: str = "sk-test") -> Settings:
    return Settings(
        _env_file=None,
        seed_path=_seed_file(tmp_path),
        data_dir=tmp_path / "universities",
        llm_workers=1,
        bridge_port=25000,
        **{"DEEPSEEK_API_KEY": api_key},
    )


def _install_runtime(monkeypatch, settings: Settings):
    import dext.cli as cli

    get_settings.cache_clear()
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    _DummyEngine.statuses = []
    _DummyEngine.calls = []
    _DummyEngine.exc = None

    monkeypatch.setattr(cli._FACTORIES, "bridge_factory", lambda settings: object())
    monkeypatch.setattr(cli._FACTORIES, "decision_center_factory", lambda: object())
    monkeypatch.setattr(cli._FACTORIES, "create_app", lambda bridge, decision_center: object())
    monkeypatch.setattr(cli._FACTORIES, "run_server", _run_server)
    monkeypatch.setattr(cli._FACTORIES, "llm_client_factory", lambda settings: object())
    monkeypatch.setattr(cli._FACTORIES, "engine_factory", _DummyEngine)
    return cli


async def _run_server(app, host, port):
    return _DummyServer()


def _runner() -> CliRunner:
    return CliRunner()


def _university(name: str, url: str) -> UniversitySeed:
    return UniversitySeed(name=name, url=url, org_unit_listing_urls=[url + "schools.htm"])


async def _run_rows(settings: Settings, university: UniversitySeed) -> list[CrawlRun]:
    from dext.storage.lifecycle import open_resume

    handle = await open_resume(university, resolve_abbr(university), settings)
    try:
        async with handle.session() as sess:
            return (await sess.execute(select(CrawlRun).order_by(CrawlRun.id))).scalars().all()
    finally:
        await handle.close()


def test_unknown_university_fails_before_api_key_check(tmp_path, monkeypatch):
    settings = _settings(tmp_path, api_key="")
    cli = _install_runtime(monkeypatch, settings)

    result = _runner().invoke(cli.main, ["-u", "Missing University"])

    assert result.exit_code != 0
    assert "unknown university" in result.output
    assert "Alpha University" in result.output
    assert "DEEPSEEK_API_KEY" not in result.output


def test_missing_api_key_fails_before_db_or_server(tmp_path, monkeypatch):
    settings = _settings(tmp_path, api_key="")
    cli = _install_runtime(monkeypatch, settings)

    result = _runner().invoke(cli.main, ["-u", "Alpha University"])

    assert result.exit_code != 0
    assert "DEEPSEEK_API_KEY is not set" in result.output
    assert not settings.data_dir.exists()
    assert _DummyEngine.calls == []


def test_fresh_backs_up_existing_db_and_records_run(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    alpha = _university("Alpha University", "https://alpha.edu.cn/")

    async def setup():
        first = await open_fresh(alpha, "alpha", settings)
        await first.close()

    asyncio.run(setup())
    cli = _install_runtime(monkeypatch, settings)

    result = _runner().invoke(cli.main, ["-u", "Alpha University"])

    assert result.exit_code == 0, result.output
    rows = asyncio.run(_run_rows(settings, alpha))
    assert len(rows) == 1
    run = rows[0]
    assert run.mode == "fresh"
    assert run.status == "completed"
    assert run.backup_path is not None
    assert Path(run.backup_path).name.endswith("-alpha")
    assert run.settings_json["seed_path"] == str(settings.seed_path)
    assert "deepseek_api_key" not in run.settings_json


def test_resume_missing_db_reports_error_and_nonzero(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    cli = _install_runtime(monkeypatch, settings)

    result = _runner().invoke(cli.main, ["-u", "Alpha University", "--resume"])

    assert result.exit_code == 1
    assert "run a fresh crawl first" in result.output


def test_completed_resume_quick_check_creates_completed_resume_run(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    alpha = _university("Alpha University", "https://alpha.edu.cn/")

    async def setup():
        first = await open_fresh(alpha, "alpha", settings)
        await first.writer.update_university_status("completed")
        await first.close()

    asyncio.run(setup())
    cli = _install_runtime(monkeypatch, settings)

    result = _runner().invoke(cli.main, ["-u", "Alpha University", "--resume"])

    assert result.exit_code == 0, result.output
    rows = asyncio.run(_run_rows(settings, alpha))
    assert len(rows) == 1
    assert rows[0].mode == "resume"
    assert rows[0].status == "completed"
    assert rows[0].backup_path is None


def test_multi_school_is_sequential_and_aggregates_failures(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    cli = _install_runtime(monkeypatch, settings)
    _DummyEngine.statuses = ["completed", "failed"]

    result = _runner().invoke(cli.main, ["--universities", "Alpha University", "Beta University"])

    assert result.exit_code == 1
    assert _DummyEngine.calls == ["Alpha University", "Beta University"]
    assert "Beta University: finished with status failed" in result.output


def test_cancelled_run_is_marked_cancelled(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    alpha = _university("Alpha University", "https://alpha.edu.cn/")
    cli = _install_runtime(monkeypatch, settings)
    _DummyEngine.exc = __import__("asyncio").CancelledError()

    result = _runner().invoke(cli.main, ["-u", "Alpha University"])

    assert result.exit_code == 130
    rows = asyncio.run(_run_rows(settings, alpha))
    assert len(rows) == 1
    assert rows[0].status == "cancelled"
    assert rows[0].summary_json["status"] == "cancelled"
