"""Unified CLI shell for independent graph-build commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import click

from dext_graph.config import GraphSettings
from dext_graph.models import ValueValidationError
from dext_graph.profiles import TEMPLATES
from dext_graph.workflow import (
    cleanup_experiment,
    compare_experiments,
    dry_run,
    finalize_comparison,
    pool_experiments,
    run_live,
)


def _emit(value: dict[str, Any]) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _guard(action: Callable[[], dict[str, Any]]) -> None:
    try:
        _emit(action())
    except ValueValidationError as exc:
        raise click.ClickException(str(exc)) from None


def _guard_async(action: Callable[[], Any]) -> None:
    try:
        _emit(asyncio.run(action()))
    except ValueValidationError as exc:
        raise click.ClickException(str(exc)) from None


@click.group()
def main() -> None:
    """Dext curation and recommendation graph tooling."""


@main.group()
def graph() -> None:
    """Build and validate recommendation graph data."""


@graph.group(name="value-validation")
def value_validation() -> None:
    """Run the temporary stage-0 semantic value experiment."""


@value_validation.command("run")
@click.option(
    "--source-db",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--template", "template_name", required=True, type=click.Choice(sorted(TEMPLATES)))
@click.option("--dry-run", "is_dry_run", is_flag=True, help="Validate and estimate without external writes.")
@click.option("--execute-live", is_flag=True, help="Call the provider and write a temporary Qdrant collection.")
def run_command(
    source_db: Path,
    template_name: str,
    is_dry_run: bool,
    execute_live: bool,
) -> None:
    """Validate the source and run one template experiment."""
    if is_dry_run == execute_live:
        raise click.UsageError("choose exactly one of --dry-run or --execute-live")
    settings = GraphSettings()
    if is_dry_run:
        _guard(lambda: dry_run(source_db, template_name, settings))
    else:
        _guard_async(lambda: run_live(source_db, template_name, settings))


@value_validation.command("pool")
@click.argument(
    "experiment_dirs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
def pool_command(experiment_dirs: tuple[Path, ...]) -> None:
    """Create one editable JSONL judgment pool from completed experiments."""
    settings = GraphSettings()
    _guard(lambda: pool_experiments(list(experiment_dirs), settings))


@value_validation.command("compare")
@click.argument(
    "experiment_dirs",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--judgments",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def compare_command(experiment_dirs: tuple[Path, ...], judgments: Path) -> None:
    """Score completed experiments against a fully annotated pool."""
    settings = GraphSettings()
    _guard(lambda: compare_experiments(list(experiment_dirs), judgments, settings))


@value_validation.command("finalize")
@click.argument(
    "comparison_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--decision", required=True, type=click.Choice(["accept", "iterate", "stop"]))
@click.option(
    "--rationale-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def finalize_command(comparison_dir: Path, decision: str, rationale_file: Path) -> None:
    """Record the product decision and freeze the comparison report."""
    rationale = rationale_file.read_text(encoding="utf-8")
    _guard(lambda: finalize_comparison(comparison_dir, decision, rationale))


@value_validation.command("cleanup")
@click.argument(
    "experiment_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--comparison-dir",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--yes", is_flag=True, help="Confirm deletion of the temporary collection.")
def cleanup_command(experiment_dir: Path, comparison_dir: Path, yes: bool) -> None:
    """Delete a collection only after a referencing comparison is finalized."""
    if not yes:
        raise click.UsageError("cleanup requires --yes")
    settings = GraphSettings()
    _guard_async(lambda: cleanup_experiment(experiment_dir, comparison_dir, settings))


__all__ = ["main"]
