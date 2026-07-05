#!/usr/bin/env python
"""Import old university SQLite sources into a new graph build."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from dext.seed import load_manifest, resolve_abbr
from dext_graph.catalog.db import (
    CatalogError,
    backup_existing_catalog,
    connect_catalog_read_only,
    utcnow_iso,
)
from dext_graph.catalog.lifecycle import promote_build, validate_build
from dext_graph.catalog.models import BuildSource
from dext_graph.catalog.progress import ProgressCallback
from dext_graph.catalog.source import inspect_snapshot
from dext_graph.catalog.workflow import create_build_from_sources
from dext_graph.config import GraphSettings


@dataclass(frozen=True)
class OldSourceCandidate:
    path: Path
    source: BuildSource
    crawl_status: str
    schema_version: int
    professor_count: int
    org_unit_count: int | None
    missing_optional_tables: tuple[str, ...]
    incomplete_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ImportPlan:
    active_build_id: str
    baseline_sources: tuple[BuildSource, ...]
    baseline_details: tuple[dict[str, Any], ...]
    old_sources: tuple[OldSourceCandidate, ...]
    combined_sources: tuple[BuildSource, ...]


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]", "-", value.strip().lower())
    return re.sub(r"-+", "-", value).strip("-")


def _abbr_from_old_filename(path: Path) -> str:
    stem = path.name[:-3] if path.name.lower().endswith(".db") else path.stem
    host_or_abbr = stem.lower()
    if host_or_abbr.startswith("www."):
        host_or_abbr = host_or_abbr[4:]
    if "." in host_or_abbr:
        host_or_abbr = host_or_abbr.split(".", 1)[0]
    return _slugify(host_or_abbr)


def _source_to_dict(source: BuildSource) -> dict[str, Any]:
    return {
        "university_id": source.university_id,
        "university_name": source.university_name,
        "abbr": source.abbr,
        "source_path": source.source_path,
        "ordinal": source.ordinal,
    }


def _old_source_to_dict(candidate: OldSourceCandidate) -> dict[str, Any]:
    return {
        **_source_to_dict(candidate.source),
        "path": str(candidate.path),
        "crawl_status": candidate.crawl_status,
        "schema_version": candidate.schema_version,
        "professor_count": candidate.professor_count,
        "org_unit_count": candidate.org_unit_count,
        "missing_optional_tables": list(candidate.missing_optional_tables),
        "incomplete_capabilities": list(candidate.incomplete_capabilities),
    }


def plan_to_dict(plan: ImportPlan) -> dict[str, Any]:
    return {
        "active_build_id": plan.active_build_id,
        "baseline_sources": [_source_to_dict(source) for source in plan.baseline_sources],
        "baseline_details": list(plan.baseline_details),
        "old_sources": [_old_source_to_dict(source) for source in plan.old_sources],
        "combined_sources": [_source_to_dict(source) for source in plan.combined_sources],
        "counts": {
            "baseline_sources": len(plan.baseline_sources),
            "old_sources": len(plan.old_sources),
            "combined_sources": len(plan.combined_sources),
        },
    }


def _reindex_sources(sources: list[BuildSource]) -> tuple[BuildSource, ...]:
    result: list[BuildSource] = []
    for ordinal, source in enumerate(sources):
        result.append(
            BuildSource(
                university_id=source.university_id,
                university_name=source.university_name,
                abbr=source.abbr,
                source_path=source.source_path,
                ordinal=ordinal,
            )
        )
    return tuple(result)


def load_active_baseline_sources(
    catalog_path: str | Path,
) -> tuple[str, tuple[BuildSource, ...], tuple[dict[str, Any], ...]]:
    with closing(connect_catalog_read_only(catalog_path)) as connection:
        active = connection.execute(
            """
            SELECT id FROM graph_builds
            WHERE status='ACTIVE'
            ORDER BY COALESCE(finished_at, started_at) DESC, started_at DESC
            LIMIT 1
            """
        ).fetchone()
        if active is None:
            raise CatalogError("catalog has no ACTIVE build to use as a baseline")
        active_build_id = str(active["id"])
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT
                  t.university_id,
                  t.university_name,
                  t.abbr,
                  t.source_path AS original_source_path,
                  t.source_snapshot_id,
                  s.snapshot_path
                FROM build_source_tasks t
                JOIN source_snapshots s ON s.id=t.source_snapshot_id
                WHERE t.build_id=? AND t.status='COMPLETED'
                ORDER BY t.ordinal
                """,
                (active_build_id,),
            )
        ]
    if not rows:
        raise CatalogError(f"ACTIVE build {active_build_id} has no completed sources")

    sources: list[BuildSource] = []
    details: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows):
        snapshot_path = Path(str(row["snapshot_path"])).expanduser().resolve()
        if not snapshot_path.is_file():
            raise CatalogError(f"ACTIVE source snapshot is missing: {snapshot_path}")
        source = BuildSource(
            university_id=str(row["university_id"]),
            university_name=str(row["university_name"]),
            abbr=str(row["abbr"]),
            source_path=str(snapshot_path),
            ordinal=ordinal,
        )
        sources.append(source)
        details.append(
            {
                **_source_to_dict(source),
                "original_source_path": row["original_source_path"],
                "source_snapshot_id": row["source_snapshot_id"],
            }
        )
    return active_build_id, tuple(sources), tuple(details)


def resolve_old_sources(
    old_dir: str | Path,
    settings: GraphSettings,
) -> tuple[OldSourceCandidate, ...]:
    root = Path(old_dir).expanduser().resolve()
    if not root.is_dir():
        raise CatalogError(f"old source directory does not exist: {root}")
    paths = sorted(path for path in root.glob("*.db") if path.is_file())
    if not paths:
        raise CatalogError(f"old source directory contains no .db files: {root}")

    manifest = load_manifest(settings.seed_path)
    universities_by_name = {university.name: university for university in manifest.universities}
    candidates: list[OldSourceCandidate] = []
    seen_universities: set[str] = set()
    for path in paths:
        inspection = inspect_snapshot(path)
        university = universities_by_name.get(inspection.university_name)
        if university is None:
            raise CatalogError(
                f"old source {path} university {inspection.university_name!r} "
                "is not present in the seed manifest"
            )
        abbr = resolve_abbr(university)
        file_abbr = _abbr_from_old_filename(path)
        if file_abbr != abbr:
            raise CatalogError(
                f"old source {path.name} resolves to abbr {file_abbr!r}, "
                f"but manifest university {university.name!r} resolves to {abbr!r}"
            )
        if inspection.abbreviation is not None and inspection.abbreviation != abbr:
            raise CatalogError(
                f"old source {path.name} metadata abbreviation "
                f"{inspection.abbreviation!r} does not match {abbr!r}"
            )
        university_id = f"univ:{abbr}"
        if university_id in seen_universities:
            raise CatalogError(f"duplicate old source for {university_id}")
        seen_universities.add(university_id)
        source = BuildSource(
            university_id=university_id,
            university_name=university.name,
            abbr=abbr,
            source_path=str(path.resolve()),
            ordinal=len(candidates),
        )
        candidates.append(
            OldSourceCandidate(
                path=path.resolve(),
                source=source,
                crawl_status=inspection.crawl_status,
                schema_version=inspection.schema_version,
                professor_count=int(inspection.row_counts.get("professors") or 0),
                org_unit_count=(
                    int(inspection.row_counts["org_units"])
                    if inspection.row_counts.get("org_units") is not None
                    else None
                ),
                missing_optional_tables=inspection.missing_optional_tables,
                incomplete_capabilities=inspection.incomplete_capabilities,
            )
        )
    return tuple(candidates)


def merge_sources(
    baseline_sources: tuple[BuildSource, ...],
    old_sources: tuple[OldSourceCandidate, ...],
    *,
    replace_existing: bool = False,
) -> tuple[BuildSource, ...]:
    old_ids = {candidate.source.university_id for candidate in old_sources}
    baseline_ids = {source.university_id for source in baseline_sources}
    duplicates = sorted(old_ids & baseline_ids)
    if duplicates and not replace_existing:
        raise CatalogError(
            "old sources already exist in the ACTIVE baseline: "
            + ", ".join(duplicates)
            + " (use --replace-existing to replace them)"
        )
    baseline = [
        source
        for source in baseline_sources
        if replace_existing is False or source.university_id not in old_ids
    ]
    combined = [*baseline, *(candidate.source for candidate in old_sources)]
    return _reindex_sources(combined)


def prepare_import_plan(
    settings: GraphSettings,
    *,
    old_dir: str | Path,
    replace_existing: bool = False,
) -> ImportPlan:
    active_build_id, baseline_sources, baseline_details = load_active_baseline_sources(
        settings.catalog_path
    )
    old_sources = resolve_old_sources(old_dir, settings)
    combined_sources = merge_sources(
        baseline_sources, old_sources, replace_existing=replace_existing
    )
    return ImportPlan(
        active_build_id=active_build_id,
        baseline_sources=baseline_sources,
        baseline_details=baseline_details,
        old_sources=old_sources,
        combined_sources=combined_sources,
    )


def backup_active_catalog(catalog_path: str | Path, plan: ImportPlan) -> dict[str, Any]:
    backup_path = backup_existing_catalog(catalog_path, retention=None)
    if backup_path is None:
        raise CatalogError(f"catalog backup was skipped because catalog is empty: {catalog_path}")
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".json")
    payload = {
        "kind": "dext_active_build_catalog_backup",
        "created_at": utcnow_iso(),
        "catalog_path": str(Path(catalog_path).expanduser().resolve()),
        "backup_path": str(backup_path),
        "checksum_path": str(checksum_path),
        "active_build_id": plan.active_build_id,
        "plan": plan_to_dict(plan),
    }
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "backup_path": str(backup_path),
        "checksum_path": str(checksum_path),
        "manifest_path": str(manifest_path),
    }


def _update_backup_manifest(
    backup: dict[str, Any] | None, updates: dict[str, Any]
) -> None:
    if not backup:
        return
    manifest_path = Path(str(backup["manifest_path"]))
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.update(updates)
    payload["updated_at"] = utcnow_iso()
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class _CompactProgressReporter:
    _IMMEDIATE_ACTIONS = {"started", "completed", "failed"}

    def __init__(
        self,
        *,
        interval_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        stream: TextIO | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self._clock = clock
        self._stream = stream
        self._last_emit_by_key: dict[tuple[str, str, str], float] = {}
        self._active_key: tuple[str, str, str] | None = None
        self._active_width = 0

    def __call__(self, event) -> None:  # noqa: ANN001 - ProgressEvent is intentionally light.
        if not self._should_emit(event):
            return
        if event.action == "progress":
            self._emit_progress(event)
            return
        self._finish_active_line()
        self._write_line(self._format_event(event))

    def _should_emit(self, event) -> bool:  # noqa: ANN001 - ProgressEvent is intentionally light.
        if event.action in self._IMMEDIATE_ACTIONS:
            return True
        if event.action != "progress":
            return True
        if event.total not in (None, 0) and event.current == event.total:
            return True
        now = self._clock()
        key = self._event_key(event)
        previous = self._last_emit_by_key.get(key)
        if previous is not None and now - previous < self.interval_seconds:
            return False
        self._last_emit_by_key[key] = now
        return True

    def _emit_progress(self, event) -> None:  # noqa: ANN001 - ProgressEvent is intentionally light.
        line = self._format_progress(event)
        if not self._is_interactive():
            self._write_line(line)
            return

        key = self._event_key(event)
        if self._active_key is not None and self._active_key != key:
            self._write_line("")
            self._active_width = 0
        padding = " " * max(self._active_width - len(line), 0)
        self._stream_or_stderr().write(f"\r{line}{padding}")
        self._stream_or_stderr().flush()
        self._active_key = key
        self._active_width = len(line)
        if event.total not in (None, 0) and event.current == event.total:
            self._write_line("")
            self._active_key = None
            self._active_width = 0

    def _finish_active_line(self) -> None:
        if self._active_key is not None and self._is_interactive():
            self._write_line("")
        self._active_key = None
        self._active_width = 0

    def _write_line(self, line: str) -> None:
        print(line, file=self._stream_or_stderr())

    def _stream_or_stderr(self) -> TextIO:
        return self._stream if self._stream is not None else sys.stderr

    def _is_interactive(self) -> bool:
        return self._stream_or_stderr().isatty()

    @staticmethod
    def _event_key(event) -> tuple[str, str, str]:  # noqa: ANN001 - ProgressEvent is intentionally light.
        return (str(event.stage), str(event.message), str(event.build_id or ""))

    @classmethod
    def _format_event(cls, event) -> str:  # noqa: ANN001 - ProgressEvent is intentionally light.
        parts = [f"[{event.stage}]", str(event.message or event.action)]
        if event.action != "progress":
            parts.append(str(event.action))
        if event.action == "failed" and event.counters.get("error"):
            parts.append(f"error={cls._short(event.counters['error'])}")
        return " ".join(parts)

    @classmethod
    def _format_progress(cls, event) -> str:  # noqa: ANN001 - ProgressEvent is intentionally light.
        parts = [f"[{event.stage}]", str(event.message or event.action)]
        if event.current is not None:
            if event.total not in (None, 0):
                percent = min(max((event.current / event.total) * 100, 0.0), 100.0)
                parts.append(f"{percent:.1f}%")
                parts.append(f"{event.current}/{event.total}")
            else:
                parts.append(str(event.current))
        return " ".join(parts)

    @staticmethod
    def _short(value: object, limit: int = 160) -> str:
        text = str(value).replace("\n", " ")
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."


def _progress_reporter(enabled: bool) -> ProgressCallback | None:
    return _CompactProgressReporter() if enabled else None


async def execute_import(
    settings: GraphSettings,
    *,
    old_dir: str | Path,
    dry_run: bool = False,
    no_promote: bool = False,
    no_backup_active: bool = False,
    strict_gold: bool = False,
    replace_existing: bool = False,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    plan = prepare_import_plan(
        settings, old_dir=old_dir, replace_existing=replace_existing
    )
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "plan": plan_to_dict(plan),
    }
    if dry_run:
        return result

    backup: dict[str, Any] | None = None
    if not no_backup_active:
        backup = backup_active_catalog(settings.catalog_path, plan)
        result["backup"] = backup

    build_result = await create_build_from_sources(
        list(plan.combined_sources),
        settings,
        progress=progress,
        backup_catalog=no_backup_active,
    )
    result["build_result"] = build_result
    build_id = str(build_result["build"]["id"])
    _update_backup_manifest(backup, {"new_build_id": build_id, "build_result": build_result})

    if no_promote:
        return result

    validation_result = await validate_build(
        build_id,
        settings,
        skip_gold_gates=not strict_gold,
    )
    result["validation_result"] = validation_result
    _update_backup_manifest(backup, {"validation_result": validation_result})
    if validation_result["build"]["status"] != "READY":
        raise CatalogError(f"build {build_id} did not pass validation")

    promotion_result = await promote_build(build_id, settings)
    result["promotion_result"] = promotion_result
    _update_backup_manifest(backup, {"promotion_result": promotion_result})
    return result


def _print_summary(payload: dict[str, Any]) -> None:
    plan = payload["plan"]
    counts = plan["counts"]
    print(f"Active baseline: {plan['active_build_id']}")
    print(
        "Sources: "
        f"baseline={counts['baseline_sources']} "
        f"old={counts['old_sources']} "
        f"combined={counts['combined_sources']}"
    )
    for source in plan["old_sources"]:
        warnings = len(source["missing_optional_tables"]) + len(
            source["incomplete_capabilities"]
        )
        print(
            f"Old source: {source['abbr']} professors={source['professor_count']} "
            f"org_units={source['org_unit_count']} crawl_status={source['crawl_status']} "
            f"warnings={warnings}"
        )
    if payload.get("dry_run"):
        print("Dry run: no catalog changes were made.")
        return
    if backup := payload.get("backup"):
        print(f"Backup: {backup['backup_path']}")
        print(f"Backup manifest: {backup['manifest_path']}")
    if build := payload.get("build_result"):
        print(f"Build: {build['build']['id']} status={build['build']['status']}")
    if validation := payload.get("validation_result"):
        print(
            f"Validation: {validation['build']['id']} "
            f"status={validation['build']['status']}"
        )
    if promotion := payload.get("promotion_result"):
        print(
            f"Promotion: {promotion['build']['id']} "
            f"status={promotion['build']['status']}"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import old university DBs into a new graph build."
    )
    parser.add_argument("--old-dir", type=Path, default=Path("data/old"))
    parser.add_argument("--catalog-path", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-promote", action="store_true")
    parser.add_argument("--no-backup-active", action="store_true")
    parser.add_argument("--strict-gold", action="store_true")
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--no-progress", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    values: dict[str, Any] = {}
    if args.catalog_path is not None:
        values["catalog_path"] = args.catalog_path
    settings = GraphSettings(**values)
    try:
        payload = asyncio.run(
            execute_import(
                settings,
                old_dir=args.old_dir,
                dry_run=args.dry_run,
                no_promote=args.no_promote,
                no_backup_active=args.no_backup_active,
                strict_gold=args.strict_gold,
                replace_existing=args.replace_existing,
                progress=_progress_reporter(not args.no_progress),
            )
        )
    except (CatalogError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
