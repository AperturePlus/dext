from __future__ import annotations

import tomllib
from pathlib import Path

from dext_recommend.api import cli


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

    def fake_run_app(created_app, *, host, port, print):
        captured.update(app=created_app, host=host, port=port, print=print)

    monkeypatch.setattr(cli, "create_recommendation_app", fake_create)
    monkeypatch.setattr(cli.web, "run_app", fake_run_app)

    cli.main(["serve", "--host", "0.0.0.0", "--port", "21531"])

    settings = captured["settings"]
    assert settings.http_host == "0.0.0.0"
    assert settings.http_port == 21531
    assert captured["app"] is app
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 21531
