"""Request-local execution context + phase guards for RecommendationCore.

The Core instance holds NO request-mutable state. Every recommend() call
creates a fresh RecommendExecutionContext and threads it through
_recommend_inner, recall_loop, and fetch_details so two concurrent
requests on the same Core cannot cross-contaminate diagnostics or snapshot.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from dext_recommend.models import PhaseDiagnostic
from dext_recommend.readiness import ActiveBuildSnapshot
from dext_recommend.core.ranking_profile import RankingProfile


class ClassifiedRecommendError(Exception):
    def __init__(self, code: str, phase: str, cause: Exception) -> None:
        self.code = code
        self.phase = phase
        self.cause = cause
        super().__init__(f"{phase} failed: {code}")


@dataclass(slots=True)
class RecommendExecutionContext:
    snapshot: ActiveBuildSnapshot | None = None
    profile: RankingProfile | None = None
    embedding_fingerprint: str | None = None
    phase_diagnostics: list[PhaseDiagnostic] = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def record(self, phase: str, elapsed_ms: float, error_code: str | None,
               attempt: int | None = None) -> None:
        self.phase_diagnostics.append(PhaseDiagnostic(
            phase=phase, attempt=attempt, elapsed_ms=elapsed_ms, error_code=error_code,
        ))

    def snapshot_phase_diagnostics(self) -> tuple[PhaseDiagnostic, ...]:
        return tuple(self.phase_diagnostics)

    def snapshot_warnings(self) -> tuple:
        """Return accumulated warnings as a stable tuple (insertion order)."""
        return tuple(self.warnings)


async def _guarded_async(
    ctx: RecommendExecutionContext, phase: str, code: str,
    op_factory: Callable[[], Awaitable[Any]],
    *,
    attempt: int | None = None,
) -> Any:
    start = time.perf_counter()
    try:
        result = await op_factory()
    except asyncio.CancelledError:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, "cancelled", attempt=attempt)
        raise
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, code, attempt=attempt)
        raise ClassifiedRecommendError(code, phase, exc) from exc
    elapsed = (time.perf_counter() - start) * 1000
    ctx.record(phase, elapsed, None, attempt=attempt)
    return result


def _guarded_sync(
    ctx: RecommendExecutionContext, phase: str, code: str,
    fn: Callable[[], Any],
) -> Any:
    start = time.perf_counter()
    try:
        result = fn()
    except asyncio.CancelledError:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, "cancelled")
        raise
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        ctx.record(phase, elapsed, code)
        raise ClassifiedRecommendError(code, phase, exc) from exc
    elapsed = (time.perf_counter() - start) * 1000
    ctx.record(phase, elapsed, None)
    return result


__all__ = [
    "ClassifiedRecommendError", "RecommendExecutionContext",
    "_guarded_async", "_guarded_sync",
]
