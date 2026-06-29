"""Stable response DTO helpers for dext monitor."""

from __future__ import annotations

from typing import Any, TypedDict


JsonDict = dict[str, Any]


class MonitorError(TypedDict):
    error: str
    message: str


class MonitorResponse(TypedDict, total=False):
    data: JsonDict
    error: MonitorError


__all__ = ["JsonDict", "MonitorError", "MonitorResponse"]
