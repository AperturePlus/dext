"""Snapshot-pinned OpenAI-compatible query embedding adapter."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from time import monotonic
from typing import Any

import httpx

from dext_recommend.config import RecommendSettings
from dext_recommend.errors import RecommendationRuntimeError
from dext_recommend.ports.embedding import EmbeddingResult
from dext_recommend.readiness import ActiveBuildSnapshot

logger = logging.getLogger(__name__)
_TOKEN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9_]+")


def _normalize_sparse_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for raw_line in value.split("\n"):
        line = re.sub(r"[\t\f\v ]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def sparse_bm25_query_vector(
    text: str, *, tokenizer_version: str,
) -> dict[str, list[int] | list[float]]:
    """Equivalent to the build-side deterministic sparse hashing primitive."""
    counts: dict[str, int] = {}
    normalized = _normalize_sparse_text(text)
    for match in _TOKEN.finditer(normalized):
        token = match.group(0).casefold()
        counts[token] = counts.get(token, 0) + 1
    rows: list[tuple[int, float]] = []
    for token, count in counts.items():
        digest = hashlib.sha256(f"{tokenizer_version}\0{token}".encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
        rows.append((index, float((count * 2.2) / (count + 1.2))))
    rows.sort(key=lambda item: item[0])
    return {
        "indices": [index for index, _ in rows],
        "values": [value for _, value in rows],
    }


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_connection_error(exc: BaseException) -> bool:
    try:
        from openai import APIConnectionError, APITimeoutError
        return isinstance(exc, (APIConnectionError, APITimeoutError))
    except ImportError:  # pragma: no cover - openai is a project dependency
        return False


class LiveQueryEmbeddingAdapter:
    def __init__(self, *, client: Any, settings: RecommendSettings) -> None:
        self._client = client
        self._settings = settings
        self._closed = False

    async def embed(
        self, snapshot: ActiveBuildSnapshot, query_text: str,
    ) -> EmbeddingResult:
        s = self._settings
        if not s.embedding_provider or s.embedding_provider != snapshot.embedding_provider:
            raise RecommendationRuntimeError(
                code="embedding_provider_mismatch",
                message="embedding provider does not match ACTIVE build",
            )
        if not s.embedding_model or s.embedding_model != snapshot.embedding_model:
            raise RecommendationRuntimeError(
                code="embedding_model_mismatch",
                message="embedding model does not match ACTIVE build",
            )

        provider_input = f"{s.embedding_query_prefix}{query_text}"
        started = monotonic()
        response = None
        for attempt in range(s.embedding_max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self._client.embeddings.create(
                        model=s.embedding_model,
                        input=[provider_input],
                        encoding_format="float",
                    ),
                    timeout=s.embedding_timeout,
                )
                break
            except Exception as exc:
                connection_error = (
                    isinstance(exc, (asyncio.TimeoutError, httpx.RequestError))
                    or _is_connection_error(exc)
                )
                if connection_error and attempt < s.embedding_max_retries:
                    continue
                status = _status_code(exc)
                retryable = connection_error or status in {429, 503, 504}
                logger.warning(
                    "embedding failed model=%s code=embedding_unavailable error_type=%s",
                    s.embedding_model, type(exc).__name__,
                )
                raise RecommendationRuntimeError(
                    code="embedding_unavailable",
                    message="embedding provider request failed",
                    retryable=retryable,
                ) from exc

        try:
            vector = tuple(float(value) for value in response.data[0].embedding)
            sparse = sparse_bm25_query_vector(
                query_text, tokenizer_version=s.bm25_tokenizer_version,
            )
            if not sparse["indices"]:
                raise ValueError("query produced no sparse tokens")
        except Exception as exc:
            raise RecommendationRuntimeError(
                code="embedding_unavailable",
                message="embedding provider returned an unusable result",
            ) from exc
        if len(vector) != snapshot.embedding_dimension:
            raise RecommendationRuntimeError(
                code="embedding_dimension_mismatch",
                message="embedding dimension does not match ACTIVE build",
            )
        logger.info(
            "embedding completed model=%s fingerprint=%s dimension=%d latency_ms=%.1f",
            s.embedding_model,
            snapshot.embedding_fingerprint,
            len(vector),
            (monotonic() - started) * 1000,
        )
        return EmbeddingResult(
            vector=vector,
            embedding_fingerprint=snapshot.embedding_fingerprint,
            sparse_vector=sparse,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


__all__ = ["LiveQueryEmbeddingAdapter", "sparse_bm25_query_vector"]
