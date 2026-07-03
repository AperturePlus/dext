"""Lightweight progress events for long-running catalog workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: str
    action: str
    build_id: str | None = None
    message: str = ""
    current: int | None = None
    total: int | None = None
    counters: Mapping[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(
    progress: ProgressCallback | None,
    stage: str,
    action: str,
    *,
    build_id: str | None = None,
    message: str = "",
    current: int | None = None,
    total: int | None = None,
    counters: Mapping[str, Any] | None = None,
) -> None:
    if progress is None:
        return
    progress(
        ProgressEvent(
            stage=stage,
            action=action,
            build_id=build_id,
            message=message,
            current=current,
            total=total,
            counters=dict(counters or {}),
        )
    )


__all__ = ["ProgressCallback", "ProgressEvent", "emit_progress"]
