from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from dext_recommend.api import cli
from dext_recommend.app_state.db import SchemaNotReadyError


def test_recommend_console_script_is_registered() -> None:
    project_root = Path(__file__).parents[3]
    with (project_root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)

    assert project["project"]["scripts"]["recommend"] == "dext_recommend.api.cli:main"


def test_serve_cli_overrides_host_and_port_environment(monkeypatch) -> None:
    monkeypatch.setenv("DEXT_APP_HTTP_HOST", "127.0.0.9")
    monkeypatch.setenv("DEXT_APP_HTTP_PORT", "21000")
    captured: dict[str, object] = {}
    app = object()

    def fake_create(settings):
        captured["settings"] = settings
        return app

    def fake_run_app(created_app, *, host, port, print, access_log, access_log_class):
        captured.update(
            app=created_app,
            host=host,
            port=port,
            print=print,
            access_log=access_log,
            access_log_class=access_log_class,
        )

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)
    monkeypatch.setattr(cli, "_configure_logging", lambda log_level: captured.update(log_level=log_level))

    cli.main(["serve", "--host", "0.0.0.0", "--port", "21531"])

    settings = captured["settings"]
    assert settings.http_host == "0.0.0.0"
    assert settings.http_port == 21531
    assert settings.log_level == "INFO"
    assert settings.access_log_enabled is True
    assert captured["app"] is app
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 21531
    assert captured["print"] is cli._log_startup_banner
    assert captured["access_log"] is cli.access_logger
    assert captured["access_log_class"] is cli.RecommendAccessLogger
    assert captured["log_level"] == "INFO"


def test_serve_cli_logging_options(monkeypatch) -> None:
    captured: dict[str, object] = {}
    app = object()

    def fake_create(settings):
        captured["settings"] = settings
        return app

    def fake_run_app(created_app, *, host, port, print, access_log, access_log_class):
        captured.update(access_log=access_log, access_log_class=access_log_class)

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)
    monkeypatch.setattr(cli, "_configure_logging", lambda log_level: captured.update(log_level=log_level))

    cli.main(["serve", "--log-level", "debug", "--no-access-log"])

    settings = captured["settings"]
    assert settings.log_level == "DEBUG"
    assert settings.access_log_enabled is False
    assert captured["log_level"] == "DEBUG"
    assert captured["access_log"] is None
    assert captured["access_log_class"] is cli.RecommendAccessLogger


def test_serve_cli_uses_logging_environment_defaults(monkeypatch) -> None:
    monkeypatch.setenv("DEXT_APP_LOG_LEVEL", "warning")
    monkeypatch.setenv("DEXT_APP_ACCESS_LOG_ENABLED", "false")
    captured: dict[str, object] = {}
    app = object()

    def fake_create(settings):
        captured["settings"] = settings
        return app

    def fake_run_app(created_app, *, host, port, print, access_log, access_log_class):
        captured.update(access_log=access_log, access_log_class=access_log_class)

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)
    monkeypatch.setattr(cli, "_configure_logging", lambda log_level: captured.update(log_level=log_level))

    cli.main(["serve"])

    settings = captured["settings"]
    assert settings.log_level == "WARNING"
    assert settings.access_log_enabled is False
    assert captured["log_level"] == "WARNING"
    assert captured["access_log"] is None
    assert captured["access_log_class"] is cli.RecommendAccessLogger


def test_serve_cli_summarizes_schema_not_ready(monkeypatch, capsys) -> None:
    app = object()

    def fake_create(settings):
        return app

    def fake_run_app(created_app, *, host, port, print, access_log, access_log_class):
        raise SchemaNotReadyError(
            "recommendation app-state schema is missing tables: "
            "app_favorites, app_history; enable dev schema bootstrap or run the migration follow-up"
        )

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)
    monkeypatch.setattr(cli, "_configure_logging", lambda log_level: None)

    with pytest.raises(SystemExit) as raised:
        cli.main(["serve"])

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert "Error: recommendation app-state schema is not ready" in captured.err
    assert "missing tables: app_favorites, app_history" in captured.err
    assert "Hint: run with --dev-bootstrap-schema for local dev" in captured.err
    assert "Traceback" not in captured.err


def test_serve_cli_summarizes_database_unavailable(monkeypatch, capsys) -> None:
    monkeypatch.setenv(
        "DEXT_APP_DATABASE_URL",
        "postgresql://dext:secret@127.0.0.1:5432/dext_app",
    )
    app = object()

    def fake_create(settings):
        return app

    def fake_run_app(created_app, *, host, port, print, access_log, access_log_class):
        raise ConnectionRefusedError(1225, "connection refused")

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)
    monkeypatch.setattr(cli, "_configure_logging", lambda log_level: None)

    with pytest.raises(SystemExit) as raised:
        cli.main(["serve"])

    captured = capsys.readouterr()
    assert raised.value.code == 1
    assert captured.out == ""
    assert "Error: recommendation app-state database is unavailable at 127.0.0.1:5432." in captured.err
    assert "docker compose -f docker/compose.yaml up -d" in captured.err
    assert "DEXT_APP_DATABASE_URL" in captured.err
    assert "connection refused" in captured.err
    assert "secret" not in captured.err
    assert "Traceback" not in captured.err


def test_serve_cli_propagates_unexpected_startup_error(monkeypatch) -> None:
    app = object()

    def fake_create(settings):
        return app

    def fake_run_app(created_app, *, host, port, print, access_log, access_log_class):
        raise RuntimeError("unexpected startup failure")

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)
    monkeypatch.setattr(cli, "_configure_logging", lambda log_level: None)

    with pytest.raises(RuntimeError, match="unexpected startup failure"):
        cli.main(["serve"])
