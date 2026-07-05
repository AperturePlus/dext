from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from dext_competition.cli import main


def test_competition_index_cli_build_and_verify(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.md").write_text("# 数学建模\n\n规则\n", encoding="utf-8")
    output = tmp_path / "index"
    runner = CliRunner()
    built = runner.invoke(
        main,
        ["index", "build", "--source-root", str(source), "--output-dir", str(output)],
    )
    assert built.exit_code == 0, built.output
    assert json.loads(built.output)["file_count"] == 1
    verified = runner.invoke(main, ["index", "verify", "--output-dir", str(output)])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["valid"] is True


def test_competition_index_cli_reports_classified_source_error(tmp_path: Path):
    result = CliRunner().invoke(
        main,
        [
            "index",
            "build",
            "--source-root",
            str(tmp_path / "missing"),
            "--output-dir",
            str(tmp_path / "index"),
        ],
    )
    assert result.exit_code != 0
    assert "source_root_missing" in result.output
