"""BuildSnapshotPort — read ACTIVE build snapshot (foundations §5)."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from dext_recommend.readiness import ActiveBuildSnapshot


@runtime_checkable
class BuildSnapshotPort(Protocol):
    def get_snapshot(self) -> ActiveBuildSnapshot: ...

    def refresh(self) -> ActiveBuildSnapshot | None:
        """Refresh failure returns None; never pollutes the validated snapshot."""
        ...


__all__ = ["BuildSnapshotPort"]
