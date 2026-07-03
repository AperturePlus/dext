"""Unified CLI shell for independent graph-build commands."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

import click

from dext_graph.catalog.progress import ProgressEvent


def _emit(value: dict[str, Any]) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _guard(action: Callable[[], dict[str, Any]]) -> None:
    from dext_graph.models import ValueValidationError

    try:
        _emit(action())
    except ValueValidationError as exc:
        raise click.ClickException(str(exc)) from None


def _guard_async(action: Callable[[], Any]) -> None:
    from dext_graph.models import ValueValidationError

    try:
        _emit(asyncio.run(action()))
    except ValueValidationError as exc:
        raise click.ClickException(str(exc)) from None


def _guard_catalog(action: Callable[[], Any], *, asynchronous: bool = False) -> None:
    from dext_graph.catalog.db import CatalogError

    try:
        result = asyncio.run(action()) if asynchronous else action()
        _emit(result)
    except (CatalogError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None


class _CatalogProgressReporter:
    _IMMEDIATE_ACTIONS = {"started", "completed", "failed"}

    def __init__(self, *, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = interval_seconds
        self._last_emit_by_key: dict[tuple[str, str, str], float] = {}

    def __call__(self, event: ProgressEvent) -> None:
        if not self._should_emit(event):
            return
        click.echo(self._format(event), err=True)

    def _should_emit(self, event: ProgressEvent) -> bool:
        if event.action in self._IMMEDIATE_ACTIONS:
            return True
        if event.total is not None and event.current == event.total:
            return True
        now = time.monotonic()
        key = (event.stage, event.action, event.message)
        previous = self._last_emit_by_key.get(key)
        if previous is not None and now - previous < self.interval_seconds:
            return False
        self._last_emit_by_key[key] = now
        return True

    @staticmethod
    def _format(event: ProgressEvent) -> str:
        parts = [f"[{event.stage}]", event.action]
        if event.build_id:
            parts.append(f"build={event.build_id}")
        if event.message:
            parts.append(event.message)
        if event.current is not None:
            if event.total not in (None, 0):
                percent = (event.current / event.total) * 100
                parts.append(f"{event.current}/{event.total} ({percent:.1f}%)")
            else:
                parts.append(str(event.current))
        counters = [
            f"{key}={value}"
            for key, value in sorted(event.counters.items())
            if value is not None
        ]
        if counters:
            parts.append(" ".join(counters))
        return " ".join(parts)


def _progress_reporter(enabled: bool) -> _CatalogProgressReporter | None:
    return _CatalogProgressReporter() if enabled else None


@click.group()
def main() -> None:
    """Dext curation and recommendation graph tooling."""


@main.group()
def graph() -> None:
    """Build and validate recommendation graph data."""


@main.group()
def monitor() -> None:
    """Serve the read-only dext monitor WebUI."""


@monitor.command("serve")
@click.option(
    "--host",
    default=None,
    help="Override host to bind. Defaults to localhost or DEXT_MONITOR_HOST.",
)
@click.option(
    "--port",
    default=None,
    type=int,
    help="Override port to bind. Defaults to 21530 or DEXT_MONITOR_PORT.",
)
@click.option(
    "--static-dir",
    default=None,
    type=click.Path(file_okay=False, path_type=Path),
    help="Built WebUI directory to serve. Defaults to webui/dist or DEXT_MONITOR_STATIC_DIR.",
)
@click.option(
    "--catalog-path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Catalog SQLite database path. Defaults to DEXT_CATALOG_PATH.",
)
def monitor_serve_command(
    host: str | None,
    port: int | None,
    static_dir: Path | None,
    catalog_path: Path | None,
) -> None:
    """Start the decoupled read-only monitor service."""
    from dext_monitor.server import run_server
    from dext_monitor.settings import MonitorSettings

    settings = MonitorSettings()
    updates: dict[str, Any] = {}
    if host is not None:
        updates["monitor_host"] = host
    if port is not None:
        updates["monitor_port"] = port
    if static_dir is not None:
        updates["monitor_static_dir"] = static_dir
    if catalog_path is not None:
        updates["catalog_path"] = catalog_path
    if updates:
        settings = settings.model_copy(update=updates)
    run_server(settings)


@graph.group(name="value-validation")
def value_validation() -> None:
    """Run the temporary stage-0 semantic value experiment."""


@graph.command("build")
@click.option(
    "--university",
    "universities",
    multiple=True,
    metavar="NAME",
    help="University name from entrances.yaml. Repeatable; omit for all existing canonical DBs.",
)
@click.option(
    "--progress/--no-progress",
    "show_progress",
    default=True,
    show_default=True,
    help="Emit human-readable progress to stderr.",
)
def build_command(universities: tuple[str, ...], show_progress: bool) -> None:
    """Create a build, snapshot sources, and ingest legacy observations."""
    from dext_graph.catalog.workflow import create_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: create_build(list(universities), settings, progress=progress),
        asynchronous=True,
    )


@graph.command("resume")
@click.argument("build_id")
@click.option(
    "--progress/--no-progress",
    "show_progress",
    default=True,
    show_default=True,
    help="Emit human-readable progress to stderr.",
)
def resume_command(build_id: str, show_progress: bool) -> None:
    """Resume a failed or interrupted catalog build."""
    from dext_graph.catalog.workflow import resume_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: resume_build(build_id, settings, progress=progress),
        asynchronous=True,
    )


@graph.command("vector")
@click.argument("build_id")
@click.option(
    "--progress/--no-progress",
    "show_progress",
    default=True,
    show_default=True,
    help="Emit human-readable progress to stderr.",
)
def vector_command(build_id: str, show_progress: bool) -> None:
    """Run or resume the stage-4 Qdrant semantic projection."""
    from dext_graph.catalog.vector_workflow import vector_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: vector_build(build_id, settings, progress=progress),
        asynchronous=True,
    )


@graph.command("validate")
@click.argument("build_id")
def validate_command(build_id: str) -> None:
    """Run deterministic release gates and mark a passing build READY."""
    from dext_graph.catalog.lifecycle import validate_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(lambda: validate_build(build_id, settings), asynchronous=True)


@graph.command("promote")
@click.argument("build_id")
def promote_command(build_id: str) -> None:
    """Publish one validated READY build to Neo4j and Qdrant."""
    from dext_graph.catalog.lifecycle import promote_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(lambda: promote_build(build_id, settings), asynchronous=True)


@graph.group("topics")
def topics_group() -> None:
    """Build, review, and evaluate the versioned Topic taxonomy."""


@topics_group.command("build")
@click.argument("build_id")
@click.option(
    "--progress/--no-progress",
    "show_progress",
    default=True,
    show_default=True,
    help="Emit human-readable progress to stderr.",
)
def topics_build_command(build_id: str, show_progress: bool) -> None:
    """Run or resume stage-5 Topic linking and graph projection."""
    from dext_graph.catalog.topic_workflow import topic_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: topic_build(build_id, settings, progress=progress),
        asynchronous=True,
    )


@topics_group.command("suggest-merges")
@click.argument("build_id")
def topics_suggest_merges_command(build_id: str) -> None:
    """Create review-only provisional Topic merge suggestions."""
    from dext_graph.catalog.topic_merge import suggest_topic_merges
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(lambda: suggest_topic_merges(build_id, settings), asynchronous=True)


@topics_group.command("gold-generate")
@click.argument("build_id")
@click.option("--size", default=400, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--output-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def topics_gold_generate_command(
    build_id: str, size: int, output_root: Path | None
) -> None:
    """Generate a stratified, unlabelled Topic gold template."""
    from dext_graph.catalog.topic_gold import generate_topic_gold
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: generate_topic_gold(
            settings.catalog_path, build_id, size=size, output_root=output_root
        )
    )


@topics_group.command("gold-evaluate")
@click.argument("build_id")
@click.option(
    "--dataset",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def topics_gold_evaluate_command(build_id: str, dataset: Path) -> None:
    """Evaluate approved Topic links against completed human annotations."""
    from dext_graph.catalog.topic_gold import evaluate_topic_gold
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: evaluate_topic_gold(settings.catalog_path, build_id, dataset)
    )


@graph.command("status")
@click.argument("build_id", required=False)
def status_command(build_id: str | None) -> None:
    """Show one build in detail, or list the latest builds."""
    from dext_graph.catalog.workflow import get_status
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(lambda: get_status(build_id, settings))


@graph.group("curation-gold")
def curation_gold_group() -> None:
    """Evaluate human-labelled identity and role decisions."""


@curation_gold_group.command("evaluate")
@click.argument("build_id")
@click.option(
    "--dataset",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def curation_gold_evaluate_command(build_id: str, dataset: Path) -> None:
    """Persist curation gold metrics for release validation."""
    from dext_graph.catalog.gold import evaluate_curation_gold
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: evaluate_curation_gold(settings.catalog_path, build_id, dataset)
    )


@graph.group("gold")
def gold_group() -> None:
    """Generate and evaluate evidence-backed graph gold sets."""


@gold_group.command("generate")
@click.argument("build_id")
@click.option("--size", default=400, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--output-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
)
def gold_generate_command(build_id: str, size: int, output_root: Path | None) -> None:
    """Generate deterministic reference facts from immutable source snapshots."""
    from dext_graph.catalog.graph_gold import generate_graph_gold
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: generate_graph_gold(
            settings.catalog_path,
            build_id,
            size=size,
            output_root=output_root,
        )
    )


@gold_group.command("evaluate")
@click.argument("build_id")
@click.option(
    "--dataset",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def gold_evaluate_command(build_id: str, dataset: Path) -> None:
    """Evaluate frozen catalog and Neo4j exports against a graph gold set."""
    from dext_graph.catalog.graph_gold import evaluate_graph_gold
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: evaluate_graph_gold(
            settings.catalog_path, build_id, dataset, settings
        ),
        asynchronous=True,
    )


@value_validation.command("run")
@click.option(
    "--source-db",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--template", "template_name", required=True, metavar="NAME")
@click.option("--dry-run", "is_dry_run", is_flag=True, help="Validate and estimate without external writes.")
@click.option("--execute-live", is_flag=True, help="Call the provider and write a temporary Qdrant collection.")
def run_command(
    source_db: Path,
    template_name: str,
    is_dry_run: bool,
    execute_live: bool,
) -> None:
    """Validate the source and run one template experiment."""
    from dext_graph.config import GraphSettings
    from dext_graph.profiles import TEMPLATES
    from dext_graph.workflow import dry_run, run_live

    if is_dry_run == execute_live:
        raise click.UsageError("choose exactly one of --dry-run or --execute-live")
    if template_name not in TEMPLATES:
        choices = ", ".join(sorted(TEMPLATES))
        raise click.UsageError(f"invalid template {template_name!r}; choose one of: {choices}")
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
    from dext_graph.config import GraphSettings
    from dext_graph.workflow import pool_experiments

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
    from dext_graph.config import GraphSettings
    from dext_graph.workflow import compare_experiments

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
    from dext_graph.workflow import finalize_comparison

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
    from dext_graph.config import GraphSettings
    from dext_graph.workflow import cleanup_experiment

    if not yes:
        raise click.UsageError("cleanup requires --yes")
    settings = GraphSettings()
    _guard_async(lambda: cleanup_experiment(experiment_dir, comparison_dir, settings))


__all__ = ["main"]
