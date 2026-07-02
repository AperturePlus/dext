"""Normalize readiness dependency calls into structured errors."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from dext_recommend.errors import ErrorSeverity, RecommendationError, RecommendationErrorCode
from dext_recommend.ports.release_readback import ReadinessSourceError


@dataclass(frozen=True, slots=True)
class ReadbackCall:
    source: str
    failure_code: RecommendationErrorCode
    coro: Awaitable[Any]


async def gather_safe(
    *calls: ReadbackCall, timeout: float,
) -> tuple[Any | RecommendationError, ...]:
    async def _run(coro: Awaitable[Any]) -> Any:
        return await asyncio.wait_for(coro, timeout)

    results = await asyncio.gather(
        *(_run(call.coro) for call in calls), return_exceptions=True,
    )
    out: list[Any | RecommendationError] = []
    for call, result in zip(calls, results, strict=True):
        if isinstance(result, RecommendationError):
            out.append(result)
        elif isinstance(result, ReadinessSourceError):
            out.append(RecommendationError(
                code=call.failure_code,
                severity=ErrorSeverity.ERROR,
                message=result.reason,
                retryable=result.retryable,
            ))
        elif isinstance(result, (asyncio.TimeoutError, TimeoutError)):
            out.append(RecommendationError(
                code=call.failure_code,
                severity=ErrorSeverity.ERROR,
                message=f"{call.source} readback timed out",
                retryable=True,
            ))
        elif isinstance(result, BaseException):
            out.append(RecommendationError(
                code=call.failure_code,
                severity=ErrorSeverity.ERROR,
                message=f"{call.source} readback failed",
                retryable=False,
                operator_action="check adapter logs",
            ))
        else:
            out.append(result)
    return tuple(out)


__all__ = ["ReadbackCall", "gather_safe"]
