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


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _short(value: Any, *, limit: int = 300) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _run_summary(result: dict[str, Any], key: str) -> dict[str, Any]:
    run = _as_dict(result.get(key)) or {}
    summary = _as_dict(run.get("summary_json"))
    if summary is not None:
        return summary
    build = _as_dict(result.get("build")) or {}
    build_summary = _as_dict(build.get("summary_json")) or {}
    return _as_dict(build_summary.get(key)) or {}


def _checkpoint_rows(
    result: dict[str, Any],
    *,
    sink: str,
    partition_key: str | None = None,
) -> int | None:
    rows = result.get("checkpoints")
    if not isinstance(rows, list):
        return None
    for row in rows:
        checkpoint = _as_dict(row)
        if checkpoint is None or checkpoint.get("sink") != sink:
            continue
        if partition_key is not None and checkpoint.get("partition_key") != partition_key:
            continue
        value = checkpoint.get("rows_written")
        return int(value) if isinstance(value, int | str) and str(value).isdigit() else None
    return None


def _emit_catalog_summary(result: Any) -> None:
    if not isinstance(result, dict):
        _emit(result)
        return
    build = _as_dict(result.get("build"))
    if build is None:
        _emit(result)
        return
    build_id = str(build.get("id") or "unknown")
    status = str(build.get("status") or "unknown")
    lines = [f"Build {build_id}: {status}"]

    if build.get("last_error"):
        lines.append(f"Last error: {_short(build['last_error'])}")

    sources = result.get("sources")
    if isinstance(sources, list) and sources:
        counts: dict[str, int] = {}
        for source in sources:
            task = _as_dict(source)
            source_status = str((task or {}).get("status") or "unknown")
            counts[source_status] = counts.get(source_status, 0) + 1
        parts = [f"{len(sources)} total"]
        parts.extend(f"{key}={counts[key]}" for key in sorted(counts))
        lines.append("Sources: " + " ".join(parts))

    stage_parts: list[str] = []
    for key in ("curation", "graph", "topics", "vector", "validation", "promotion"):
        run = _as_dict(result.get(key))
        if run is not None and run.get("status"):
            stage_parts.append(f"{key}={run['status']}")
    if stage_parts:
        lines.append("Stages: " + " ".join(stage_parts))

    graph_summary = _run_summary(result, "graph")
    graph_parts: list[str] = []
    evidence = _as_dict(graph_summary.get("evidence")) or {}
    if evidence.get("research_statements") is not None:
        graph_parts.append(f"research_statements={evidence['research_statements']}")
    partitions = _as_dict(graph_summary.get("partitions")) or {}
    if partitions:
        graph_parts.append(f"partitions={len(partitions)}")
    if graph_summary.get("export_pruned"):
        graph_parts.append("export_pruned=true")
    if graph_parts:
        lines.append("Graph: " + " ".join(graph_parts))

    topic_summary = _run_summary(result, "topics")
    topic_labels = (
        ("active", "active_topics"),
        ("linked", "linked_statements"),
        ("approved", "approved_links"),
        ("review", "review_links"),
    )
    topic_parts = [
        f"{label}={topic_summary[key]}"
        for label, key in topic_labels
        if topic_summary.get(key) is not None
    ]
    if topic_parts:
        lines.append("Topics: " + " ".join(topic_parts))

    vector_summary = _run_summary(result, "vector")
    vector_labels = (
        ("eligible", "eligible_professors"),
        ("qdrant", "qdrant_count"),
    )
    vector_parts = [
        f"{label}={vector_summary[key]}"
        for label, key in vector_labels
        if vector_summary.get(key) is not None
    ]
    uploaded = _checkpoint_rows(result, sink="qdrant", partition_key="professors")
    if uploaded is not None:
        vector_parts.append(f"uploaded={uploaded}")
    if vector_summary.get("collection_name"):
        vector_parts.append(f"collection={vector_summary['collection_name']}")
    if vector_parts:
        lines.append("Vector: " + " ".join(vector_parts))

    validation = _as_dict(result.get("validation")) or {}
    validation_summary = _run_summary(result, "validation")
    manifest = _as_dict(validation.get("manifest_json")) or {}
    validation_parts: list[str] = []
    if validation.get("status"):
        validation_parts.append(f"status={validation['status']}")
    if manifest.get("passed") is not None:
        validation_parts.append(f"passed={manifest['passed']}")
    elif validation_summary.get("passed") is not None:
        validation_parts.append(f"passed={validation_summary['passed']}")
    checks = manifest.get("checks")
    if isinstance(checks, list) and checks:
        passed = sum(
            1 for check in checks if isinstance(check, dict) and bool(check.get("passed"))
        )
        validation_parts.append(f"checks={passed}/{len(checks)}")
        failed = [
            str(check.get("name"))
            for check in checks
            if isinstance(check, dict) and not bool(check.get("passed"))
        ]
        if failed:
            suffix = "" if len(failed) <= 5 else f",+{len(failed) - 5}"
            validation_parts.append(f"failed={','.join(failed[:5])}{suffix}")
    release_mode = manifest.get("release_mode")
    if release_mode and release_mode != "standard":
        validation_parts.append(f"mode={release_mode}")
    skipped = manifest.get("skipped_checks")
    if isinstance(skipped, list) and skipped:
        skipped_names = [str(name) for name in skipped]
        suffix = "" if len(skipped_names) <= 5 else f",+{len(skipped_names) - 5}"
        validation_parts.append(f"skipped={','.join(skipped_names[:5])}{suffix}")
    if validation_parts:
        lines.append("Validation: " + " ".join(validation_parts))

    repair = _as_dict(result.get("topic_link_repair")) or {}
    if repair:
        repair_parts = [f"downgraded={int(repair.get('downgraded_links') or 0)}"]
        partitions = repair.get("rebuilt_partitions")
        if isinstance(partitions, list) and partitions:
            repair_parts.append("rebuilt=" + ",".join(str(item) for item in partitions))
        if repair.get("vector_reset"):
            repair_parts.append("vector_reset=true")
        lines.append("Topic repair: " + " ".join(repair_parts))

    promotion = _as_dict(result.get("promotion")) or {}
    promotion_parts: list[str] = []
    if promotion.get("status"):
        promotion_parts.append(f"status={promotion['status']}")
    if promotion.get("previous_active_build_id"):
        promotion_parts.append(f"previous={promotion['previous_active_build_id']}")
    for key in ("neo4j_done", "qdrant_done", "readback_done"):
        if promotion.get(key) is not None:
            promotion_parts.append(f"{key}={promotion[key]}")
    if promotion_parts:
        lines.append("Promotion: " + " ".join(promotion_parts))

    findings = _as_dict(result.get("unresolved_findings")) or {}
    finding_parts = [
        f"{key}={findings[key]}"
        for key in sorted(findings)
        if findings.get(key)
    ]
    if finding_parts:
        lines.append("Unresolved findings: " + " ".join(finding_parts))

    if build_id != "unknown":
        lines.append(f"Full details: uv run dext graph status {build_id}")
    click.echo("\n".join(lines))


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


def _failed_build_message(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    build = result.get("build")
    if not isinstance(build, dict) or build.get("status") != "FAILED":
        return None
    build_id = str(build.get("id") or "unknown")
    last_error = str(build.get("last_error") or "unknown error")
    return (
        f"build {build_id} failed: {last_error}\n"
        f"Run 'dext graph status {build_id}' for full JSON details."
    )


def _guard_catalog(
    action: Callable[[], Any],
    *,
    asynchronous: bool = False,
    fail_on_failed_build: bool = False,
    json_output: bool = True,
) -> None:
    from dext_graph.catalog.db import CatalogError

    try:
        result = asyncio.run(action()) if asynchronous else action()
        if fail_on_failed_build and (message := _failed_build_message(result)):
            raise click.ClickException(message)
        if json_output:
            _emit(result)
        else:
            _emit_catalog_summary(result)
    except (CatalogError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None


class _CatalogProgressReporter:
    _IMMEDIATE_ACTIONS = {"started", "completed", "failed"}
    _HIDDEN_PROGRESS_COUNTERS = {"statement_id"}
    _BAR_WIDTH = 30

    def __init__(self, *, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = interval_seconds
        self._last_emit_by_key: dict[tuple[str, str, str], float] = {}
        self._active_key: tuple[str, str, str] | None = None
        self._active_width = 0

    def __call__(self, event: ProgressEvent) -> None:
        if not self._should_emit(event):
            return
        if self._is_bar_event(event):
            self._emit_bar(event)
            return
        self._finish_active_line()
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

    def _emit_bar(self, event: ProgressEvent) -> None:
        line = self._format_bar(event)
        if not self._is_interactive():
            click.echo(line, err=True)
            return

        key = self._event_key(event)
        if self._active_key is not None and self._active_key != key:
            click.echo(err=True)
            self._active_width = 0
        padding = " " * max(self._active_width - len(line), 0)
        click.echo(f"\r{line}{padding}", nl=False, err=True)
        self._active_key = key
        self._active_width = len(line)
        if event.total is not None and event.current == event.total:
            click.echo(err=True)
            self._active_key = None
            self._active_width = 0

    def _finish_active_line(self) -> None:
        if self._active_key is not None and self._is_interactive():
            click.echo(err=True)
        self._active_key = None
        self._active_width = 0

    @staticmethod
    def _event_key(event: ProgressEvent) -> tuple[str, str, str]:
        return (event.stage, event.action, event.build_id or "")

    @staticmethod
    def _is_interactive() -> bool:
        return click.get_text_stream("stderr").isatty()

    @staticmethod
    def _is_bar_event(event: ProgressEvent) -> bool:
        return (
            event.action == "progress"
            and event.current is not None
            and event.total not in (None, 0)
        )

    @classmethod
    def _format_bar(cls, event: ProgressEvent) -> str:
        current = max(int(event.current or 0), 0)
        total = max(int(event.total or 0), 1)
        percent = min((current / total) * 100, 100.0)
        filled = min(int(((percent / 100) * cls._BAR_WIDTH) + 0.5), cls._BAR_WIDTH)
        if current > 0 and filled == 0:
            filled = 1
        bar = "#" * filled + "-" * (cls._BAR_WIDTH - filled)
        parts = [
            f"[{event.stage}]",
            event.message or event.action,
            f"[{bar}]",
            f"{percent:.1f}%",
            f"{current}/{total}",
        ]
        counters = cls._format_progress_counters(event)
        if counters:
            parts.append(counters)
        return " ".join(parts)

    @classmethod
    def _format_progress_counters(cls, event: ProgressEvent) -> str:
        counters: list[str] = []
        for key, value in sorted(event.counters.items()):
            if key in cls._HIDDEN_PROGRESS_COUNTERS or value is None:
                continue
            text = str(value)
            if len(text) > 32:
                continue
            counters.append(f"{key}={text}")
            if len(counters) >= 4:
                break
        return " ".join(counters)

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
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def build_command(
    universities: tuple[str, ...], show_progress: bool, json_output: bool
) -> None:
    """Create a build, snapshot sources, and ingest legacy observations."""
    from dext_graph.catalog.workflow import create_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: create_build(list(universities), settings, progress=progress),
        asynchronous=True,
        fail_on_failed_build=True,
        json_output=json_output,
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
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def resume_command(build_id: str, show_progress: bool, json_output: bool) -> None:
    """Resume a failed or interrupted catalog build."""
    from dext_graph.catalog.workflow import resume_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: resume_build(build_id, settings, progress=progress),
        asynchronous=True,
        fail_on_failed_build=True,
        json_output=json_output,
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
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def vector_command(build_id: str, show_progress: bool, json_output: bool) -> None:
    """Run or resume the stage-4 Qdrant semantic projection."""
    from dext_graph.catalog.vector_workflow import vector_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: vector_build(build_id, settings, progress=progress),
        asynchronous=True,
        fail_on_failed_build=True,
        json_output=json_output,
    )


@graph.command("validate")
@click.argument("build_id")
@click.option(
    "--skip-gold-gates",
    is_flag=True,
    help="Allow release validation to pass without curation/graph/topic gold datasets.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def validate_command(build_id: str, skip_gold_gates: bool, json_output: bool) -> None:
    """Run deterministic release gates and mark a passing build READY."""
    from dext_graph.catalog.lifecycle import validate_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: validate_build(
            build_id, settings, skip_gold_gates=skip_gold_gates
        ),
        asynchronous=True,
        json_output=json_output,
    )


@graph.command("promote")
@click.argument("build_id")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def promote_command(build_id: str, json_output: bool) -> None:
    """Publish one validated READY build to Neo4j and Qdrant."""
    from dext_graph.catalog.lifecycle import promote_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: promote_build(build_id, settings),
        asynchronous=True,
        json_output=json_output,
    )


@graph.command("rollback-promotion")
@click.argument("build_id")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def rollback_promotion_command(build_id: str, json_output: bool) -> None:
    """Restore the previous ACTIVE build after a failed promotion."""
    from dext_graph.catalog.lifecycle import rollback_promotion
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: rollback_promotion(build_id, settings),
        asynchronous=True,
        json_output=json_output,
    )


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
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def topics_build_command(
    build_id: str, show_progress: bool, json_output: bool
) -> None:
    """Run or resume stage-5 Topic linking and graph projection."""
    from dext_graph.catalog.topic_workflow import topic_build
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    progress = _progress_reporter(show_progress)
    _guard_catalog(
        lambda: topic_build(build_id, settings, progress=progress),
        asynchronous=True,
        fail_on_failed_build=True,
        json_output=json_output,
    )


@topics_group.command("suggest-merges")
@click.argument("build_id")
def topics_suggest_merges_command(build_id: str) -> None:
    """Create review-only provisional Topic merge suggestions."""
    from dext_graph.catalog.topic_merge import suggest_topic_merges
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(lambda: suggest_topic_merges(build_id, settings), asynchronous=True)


@topics_group.command("repair-links")
@click.argument("build_id")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit full JSON details instead of the concise summary.",
)
def topics_repair_links_command(build_id: str, json_output: bool) -> None:
    """Downgrade incompatible approved Topic links and reset derived stages."""
    from dext_graph.catalog.topic_repair import repair_incompatible_topic_links
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: repair_incompatible_topic_links(build_id, settings),
        asynchronous=True,
        json_output=json_output,
    )


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


@graph.group("catalog")
def catalog_group() -> None:
    """Inspect and compact catalog staging/cache tables."""


@catalog_group.command("size")
def catalog_size_command() -> None:
    """Show catalog file, backup, snapshot, and export-row size diagnostics."""
    from dext_graph.catalog.maintenance import catalog_size
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(lambda: catalog_size(settings))


@catalog_group.command("compact")
@click.option("--keep-build", required=True, metavar="BUILD_ID")
@click.option("--prune-build", "prune_builds", multiple=True, metavar="BUILD_ID")
@click.option("--yes", is_flag=True, help="Execute deletion and VACUUM. Defaults to dry-run.")
def catalog_compact_command(
    keep_build: str, prune_builds: tuple[str, ...], yes: bool
) -> None:
    """Prune rebuildable graph export staging rows from obsolete builds."""
    from dext_graph.catalog.maintenance import compact_catalog
    from dext_graph.config import GraphSettings

    settings = GraphSettings()
    _guard_catalog(
        lambda: compact_catalog(
            settings,
            keep_build=keep_build,
            prune_builds=prune_builds,
            yes=yes,
        )
    )


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
