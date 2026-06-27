"""SP7 command-line entrypoint for dext crawls."""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import click
from sqlalchemy import select

from dext.bridge import DecisionCenter, HumanFetcherBridge, RedirectGuard, create_app, run_server
from dext.config import Settings, get_settings
from dext.engine import CrawlEngine, CrawlSummary, load_seed_nodes
from dext.llm import LLMClient
from dext.seed import Manifest, SeedError, get_university, load_manifest, resolve_abbr
from dext.storage.lifecycle import open_fresh, open_resume
from dext.storage.models import OrgUnit

logger = logging.getLogger("dext.cli")


@dataclass
class RuntimeFactories:
    open_fresh: Callable[..., Any] = open_fresh
    open_resume: Callable[..., Any] = open_resume
    bridge_factory: Callable[..., Any] = HumanFetcherBridge
    decision_center_factory: Callable[..., Any] = DecisionCenter
    create_app: Callable[..., Any] = create_app
    run_server: Callable[..., Any] = run_server
    llm_client_factory: Callable[..., Any] = LLMClient
    load_seed_nodes: Callable[..., Any] = load_seed_nodes
    redirect_guard_factory: Callable[..., Any] = RedirectGuard
    engine_factory: Callable[..., Any] = field(default=CrawlEngine)


_FACTORIES = RuntimeFactories()


def _settings_snapshot(settings: Settings) -> dict:
    return settings.model_dump(mode="json", exclude={"deepseek_api_key"})


def _configure_logging(settings: Settings, log_file: str | None) -> None:
    dext_logger = logging.getLogger("dext")
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    dext_logger.setLevel(level)
    dext_logger.propagate = False

    for handler in list(dext_logger.handlers):
        dext_logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    dext_logger.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        dext_logger.addHandler(file_handler)


def _load_manifest(settings: Settings) -> Manifest:
    try:
        return load_manifest(settings.seed_path)
    except SeedError as exc:
        raise click.ClickException(str(exc)) from exc


def _validate_universities(manifest: Manifest, names: list[str]) -> None:
    available = [u.name for u in manifest.universities]
    available_set = set(available)
    missing = [name for name in names if name not in available_set]
    if missing:
        raise click.ClickException(
            "unknown university: "
            + ", ".join(repr(name) for name in missing)
            + "\nAvailable universities: "
            + ", ".join(available)
        )


def _validate_api_key(settings: Settings) -> None:
    if not settings.deepseek_api_key:
        raise click.ClickException(
            "DEEPSEEK_API_KEY is not set; set it in .env before running crawl."
        )


async def _safe_finish_run(
    storage,
    run_id: int | None,
    *,
    status: str,
    summary: dict,
    update_university_status: bool = True,
) -> None:
    if storage is None or run_id is None:
        return
    try:
        await storage.writer.finish_run(run_id, status=status, summary=summary)
        if update_university_status:
            await storage.writer.update_university_status("failed")
    except Exception:  # noqa: BLE001 -- preserve the original crawl failure
        logger.exception("failed to mark run %s as %s", run_id, status)


async def _cleanup_server(server) -> None:
    if server is not None and hasattr(server, "cleanup"):
        try:
            await server.cleanup()
        except Exception:  # noqa: BLE001 -- cleanup must not mask the crawl result
            logger.exception("failed to cleanup bridge server")


async def _validate_org_unit_ids(storage, org_unit_ids: set[int]) -> None:
    if not org_unit_ids:
        return
    ordered = sorted(org_unit_ids)
    async with storage.session() as session:
        rows = (
            await session.execute(select(OrgUnit.id).where(OrgUnit.id.in_(ordered)))
        ).scalars().all()
    missing = sorted(set(ordered) - set(rows))
    if missing:
        raise click.ClickException(
            "unknown org_units_id: " + ", ".join(str(org_id) for org_id in missing)
        )


async def run_university(
    name: str,
    *,
    resume: bool,
    settings: Settings,
    org_unit_ids: set[int] | None = None,
    reset: bool = False,
) -> CrawlSummary:
    factories = _FACTORIES
    manifest = load_manifest(settings.seed_path)
    university = get_university(manifest, name)
    abbr = resolve_abbr(university)
    target_org_unit_ids = set(org_unit_ids or set())
    effective_resume = resume or bool(target_org_unit_ids)
    targeted = bool(target_org_unit_ids)

    storage = None
    server = None
    run_id: int | None = None
    mode = "resume" if effective_resume else "fresh"
    try:
        opener = factories.open_resume if effective_resume else factories.open_fresh
        storage = await opener(university, abbr, settings)
        await _validate_org_unit_ids(storage, target_org_unit_ids)
        run_id = await storage.writer.start_run(
            mode=mode,
            settings=_settings_snapshot(settings),
            backup_path=storage.backup_path,
        )

        bridge = factories.bridge_factory(settings)
        decision_center = factories.decision_center_factory()
        # The RedirectGuard is an off-channel aiohttp side-probe (overview §7) that
        # pre-drops wechat-redirect traps and normalizes offsite redirects. Its failures
        # are diagnostic-only metadata (never defer), so it cannot block the crawl. The
        # status probe was removed — a human browser does the fetching, so "backend can't
        # connect" is not a signal to defer; dead/5xx URLs are handled post-fetch.
        redirect_guard = factories.redirect_guard_factory() if settings.probe_redirect_enabled else None
        app = factories.create_app(bridge, decision_center)
        server = await factories.run_server(app, settings.bridge_host, settings.bridge_port)
        llm_client = factories.llm_client_factory(settings)

        if reset and target_org_unit_ids:
            # Rebuild mode: nuke each targeted org_unit's discovered subtree BEFORE
            # (re)seeding so entry-point nodes are reset and re-crawled fresh.
            for org_id in sorted(target_org_unit_ids):
                counts = await storage.writer.reset_org_unit_subtree(org_id)
                logger.info("reset(rebuild) org_unit_id=%s %s", org_id, counts)

        await factories.load_seed_nodes(
            university,
            storage,
            settings,
            run_id,
            redirect_guard=redirect_guard,
        )

        if reset and not target_org_unit_ids:
            # Bad-snapshot mode: reset detail leaf nodes whose cached snapshot is
            # empty (the capture regression) so they are re-fetched and re-extracted.
            counts = await storage.writer.reset_bad_detail_snapshots()
            logger.info("reset(bad-snapshots) %s", counts)

        logger.info(
            "bridge server ready at http://%s:%s/api; ensure the Tampermonkey "
            "browser tab is visible and has owner",
            settings.bridge_host,
            settings.bridge_port,
        )

        engine = factories.engine_factory(
            storage,
            bridge,
            llm_client,
            settings,
            run_id,
            university_name=university.name,
            decision_center=decision_center,
            redirect_guard=redirect_guard,
            org_unit_ids=target_org_unit_ids,
        )
        return await engine.run()
    except asyncio.CancelledError:
        await _safe_finish_run(
            storage,
            run_id,
            status="cancelled",
            summary={"status": "cancelled", "university": name},
            update_university_status=not targeted,
        )
        raise
    except Exception as exc:
        await _safe_finish_run(
            storage,
            run_id,
            status="failed",
            summary={"status": "failed", "university": name, "error": repr(exc)},
            update_university_status=not targeted,
        )
        raise
    finally:
        await _cleanup_server(server)
        if storage is not None:
            await storage.close()


async def _run_all(
    universities: list[str],
    *,
    resume: bool,
    settings: Settings,
    org_unit_ids: set[int] | None = None,
    reset: bool = False,
) -> int:
    failed = False
    target_org_unit_ids = set(org_unit_ids or set())
    effective_resume = resume or bool(target_org_unit_ids)
    for name in universities:
        logger.info("starting crawl university=%s mode=%s", name, "resume" if effective_resume else "fresh")
        try:
            summary = await run_university(
                name,
                resume=effective_resume,
                settings=settings,
                org_unit_ids=target_org_unit_ids,
                reset=reset,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- aggregate per-university failures
            failed = True
            logger.exception("crawl failed university=%s", name)
            click.echo(f"{name}: failed: {exc}", err=True)
            continue

        logger.info("finished crawl university=%s status=%s summary=%s", name, summary.status, summary.asdict())
        if summary.status != "completed":
            failed = True
            click.echo(f"{name}: finished with status {summary.status}", err=True)

    return 1 if failed else 0


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "-u",
    "--universities",
    "universities",
    multiple=True,
    metavar="NAME",
    help="University name from entrances.yaml. Repeatable.",
)
@click.option("-r", "--resume", is_flag=True, help="Resume an existing university DB instead of fresh rebuild.")
@click.option(
    "--reset",
    is_flag=True,
    help="Requires --resume (or -oid). With -oid: rebuild each targeted college's subtree "
    "(delete detail/followup/pagination nodes, reset entry-point nodes) and re-crawl. "
    "Without -oid: reset all detail leaf nodes whose cached snapshot is empty/empty_page, "
    "then re-crawl.",
)
@click.option(
    "-oid",
    "--org_units_id",
    "org_units_id",
    multiple=True,
    type=click.IntRange(min=1),
    metavar="ID",
    help="Resume only the selected org_units.id. Repeatable; implies --resume.",
)
@click.option("--log-file", type=click.Path(dir_okay=False, path_type=Path), help="Optional UTF-8 log file.")
@click.argument("extra_universities", nargs=-1)
def main(
    universities: tuple[str, ...],
    resume: bool,
    org_units_id: tuple[int, ...],
    log_file: Path | None,
    extra_universities: tuple[str, ...],
    reset: bool,
) -> None:
    """Run the graph-driven human-assisted crawler."""
    settings = get_settings()
    _configure_logging(settings, str(log_file) if log_file else None)

    if not universities:
        raise click.UsageError("Missing option '-u' / '--universities'.")
    if reset and not (resume or org_units_id):
        raise click.UsageError("--reset requires --resume (or -oid, which implies --resume).")
    names = [*universities, *extra_universities]

    manifest = _load_manifest(settings)
    _validate_universities(manifest, names)
    _validate_api_key(settings)

    try:
        exit_code = asyncio.run(
            _run_all(
                names,
                resume=resume,
                settings=settings,
                org_unit_ids=set(org_units_id),
                reset=reset,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        click.echo("Interrupted.", err=True)
        raise click.exceptions.Exit(130) from None
    raise click.exceptions.Exit(exit_code)


__all__ = ["RuntimeFactories", "main", "run_university"]
