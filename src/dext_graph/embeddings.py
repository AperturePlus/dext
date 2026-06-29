"""Auditable SiliconFlow OpenAI-compatible embeddings adapter."""

from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from dext_graph.config import GraphSettings
from dext_graph.models import EmbeddingResult, RequestMetric, ValueValidationError

MetricSink = Callable[[RequestMetric], None]
Sleep = Callable[[float], Awaitable[None]]


def _retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (when - current).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


class EmbeddingClient:
    def __init__(
        self,
        settings: GraphSettings,
        metric_sink: MetricSink,
        *,
        client: Any | None = None,
        sleep: Sleep = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if not settings.embedding_api_key:
            raise ValueValidationError("DEXT_EMBEDDING_API_KEY is not set")
        self.settings = settings
        self._metric_sink = metric_sink
        self._client = client or AsyncOpenAI(
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            timeout=settings.embedding_timeout_seconds,
            max_retries=0,
        )
        self._owns_client = client is None
        self._sleep = sleep
        self._random = random_value

    async def close(self) -> None:
        if self._owns_client:
            close = getattr(self._client, "close", None)
            if close is None:
                close = getattr(self._client, "aclose", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    async def __aenter__(self) -> "EmbeddingClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def embed(self, texts: list[str], *, purpose: str) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult([])
        request_id = str(uuid.uuid4())
        attempts = self.settings.embedding_max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._client.embeddings.with_raw_response.create(
                    model=self.settings.embedding_model,
                    input=texts,
                    encoding_format="float",
                )
            except (APITimeoutError, APIConnectionError, httpx.RequestError) as exc:
                latency = (time.perf_counter() - started) * 1000
                metric = RequestMetric(
                    request_id=request_id,
                    purpose=purpose,
                    attempt=attempt,
                    item_count=len(texts),
                    status_code=None,
                    latency_ms=latency,
                    error_kind=type(exc).__name__,
                )
                self._metric_sink(metric)
                if attempt >= attempts:
                    raise ValueValidationError(
                        f"embedding request failed after {attempt} attempts: {type(exc).__name__}"
                    ) from None
                await self._sleep(self._backoff(attempt))
                continue
            except APIStatusError as exc:
                latency = (time.perf_counter() - started) * 1000
                trace_id = exc.response.headers.get("x-siliconcloud-trace-id")
                retry_after = _retry_after_seconds(exc.response.headers.get("Retry-After"))
                status_code = int(exc.status_code)
                self._metric_sink(
                    RequestMetric(
                        request_id=request_id,
                        purpose=purpose,
                        attempt=attempt,
                        item_count=len(texts),
                        status_code=status_code,
                        latency_ms=latency,
                        trace_id=trace_id,
                        retry_after_seconds=retry_after,
                        error_kind=f"http_{status_code}",
                    )
                )
                retryable = status_code in {429, 503, 504}
                if retryable and attempt < attempts:
                    await self._sleep(
                        retry_after if retry_after is not None else self._backoff(attempt)
                    )
                    continue
                raise ValueValidationError(
                    f"embedding provider returned HTTP {status_code} "
                    f"after {attempt} attempt(s); trace_id={trace_id or 'unavailable'}"
                ) from None

            latency = (time.perf_counter() - started) * 1000
            trace_id = response.headers.get("x-siliconcloud-trace-id")
            try:
                body = self._response_to_dict(response.parse())
                vectors = self._validated_vectors(body, len(texts))
                usage = self._usage(body)
            except (ValueError, TypeError, KeyError) as exc:
                self._metric_sink(
                    RequestMetric(
                        request_id=request_id,
                        purpose=purpose,
                        attempt=attempt,
                        item_count=len(texts),
                        status_code=200,
                        latency_ms=latency,
                        trace_id=trace_id,
                        error_kind="invalid_response",
                    )
                )
                raise ValueValidationError(
                    f"embedding provider returned an invalid response: {type(exc).__name__}; "
                    f"trace_id={trace_id or 'unavailable'}"
                ) from None
            self._metric_sink(
                RequestMetric(
                    request_id=request_id,
                    purpose=purpose,
                    attempt=attempt,
                    item_count=len(texts),
                    status_code=200,
                    latency_ms=latency,
                    trace_id=trace_id,
                    prompt_tokens=usage.get("prompt_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    succeeded=True,
                )
            )
            return EmbeddingResult(vectors=vectors, usage=usage)
        raise AssertionError("embedding retry loop ended unexpectedly")

    def _backoff(self, attempt: int) -> float:
        return min(30.0, (2 ** (attempt - 1)) * max(0.01, self._random()))

    def _validated_vectors(self, body: Any, expected: int) -> list[list[float]]:
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise TypeError("response data must be a list")
        data = body["data"]
        if len(data) != expected:
            raise ValueError("embedding result count mismatch")
        ordered: list[list[float] | None] = [None] * expected
        for item in data:
            if not isinstance(item, dict):
                raise TypeError("embedding item must be an object")
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < expected or ordered[index] is not None:
                raise ValueError("embedding indexes must be unique and contiguous")
            if not isinstance(vector, list) or len(vector) != self.settings.embedding_dimension:
                raise ValueError("embedding dimension mismatch")
            converted = [float(value) for value in vector]
            if not all(math.isfinite(value) for value in converted):
                raise ValueError("embedding contains a non-finite value")
            ordered[index] = converted
        if any(vector is None for vector in ordered):
            raise ValueError("embedding index is missing")
        return [vector for vector in ordered if vector is not None]

    @staticmethod
    def _usage(body: dict[str, Any]) -> dict[str, int]:
        raw = body.get("usage") or {}
        if not isinstance(raw, dict):
            return {}
        usage: dict[str, int] = {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = raw.get(key)
            if isinstance(value, int) and value >= 0:
                usage[key] = value
        return usage

    @staticmethod
    def _response_to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        data = []
        for item in getattr(value, "data", []) or []:
            if isinstance(item, dict):
                data.append(item)
            else:
                data.append(
                    {
                        "index": getattr(item, "index", None),
                        "embedding": getattr(item, "embedding", None),
                    }
                )
        usage_obj = getattr(value, "usage", None)
        usage: dict[str, Any]
        if isinstance(usage_obj, dict):
            usage = usage_obj
        elif usage_obj is None:
            usage = {}
        else:
            usage = {
                key: getattr(usage_obj, key, None)
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
        return {"data": data, "usage": usage}


__all__ = ["EmbeddingClient", "_retry_after_seconds"]
