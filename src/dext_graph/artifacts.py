"""Atomic local artifacts for temporary value-validation experiments."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dext_graph.models import ValueValidationError


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def slug(value: str, *, limit: int = 48) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return (normalized or "value")[:limit]


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


class JsonlWriter:
    def __init__(self, path: Path, *, append: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._stream = path.open("a" if append else "x", encoding="utf-8", newline="\n")

    def write(self, value: Any) -> None:
        self._stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
        self._stream.write("\n")
        self._stream.flush()

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def read_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def iter_jsonl(path: str | Path):
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueValidationError(
                    f"invalid JSONL at {path}:{line_number}: {exc.msg}"
                ) from None
            if not isinstance(value, dict):
                raise ValueValidationError(f"JSONL row at {path}:{line_number} must be an object")
            yield value


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def vector_checksum(vector: list[float]) -> str:
    digest = hashlib.sha256()
    for value in vector:
        digest.update(struct.pack("!f", float(value)))
    return digest.hexdigest()


def code_version(workdir: Path | None = None) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=workdir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workdir,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"git_commit": commit, "git_dirty": dirty}
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "git_dirty": None}


def collection_name(experiment_id: str, model: str, template: str) -> str:
    name = f"dext_eval__{slug(experiment_id)}__{slug(model)}__{slug(template)}"
    if name.startswith("dext_professors__"):
        raise AssertionError("temporary collection collided with the production namespace")
    return name[:200]


def new_artifact_dir(root: Path, kind: str, identifier: str | None = None) -> Path:
    artifact_id = identifier or timestamp_id()
    path = root / kind / artifact_id
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ValueValidationError(f"artifact directory already exists: {path}") from None
    return path


__all__ = [
    "JsonlWriter",
    "atomic_write_json",
    "atomic_write_text",
    "code_version",
    "collection_name",
    "iter_jsonl",
    "new_artifact_dir",
    "read_json",
    "read_jsonl",
    "slug",
    "timestamp_id",
    "utcnow",
    "vector_checksum",
]
