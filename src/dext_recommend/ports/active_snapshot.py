"""Validated snapshot provider consumed by recommendation request paths."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from dext_recommend.readiness import ActiveBuildSnapshot


@runtime_checkable
class ActiveSnapshotProvider(Protocol):
    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        """Return the last fully validated snapshot; never merge partial refreshes."""
        ...


__all__ = ["ActiveSnapshotProvider"]
