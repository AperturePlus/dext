from __future__ import annotations

import asyncio

import pytest

from dext_recommend import RecommendSettings, RecommendationRuntimeError
from dext_recommend.adapters.query_embedding import (
    LiveQueryEmbeddingAdapter, sparse_bm25_query_vector,
)
from tests.dext_recommend._recfixtures import snapshot


class FakeEmbeddings:
    def __init__(self, owner):
        self.owner = owner

    async def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        outcome = self.owner.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return type("Response", (), {
            "data": [type("Row", (), {"embedding": outcome})()],
        })()


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = False
        self.embeddings = FakeEmbeddings(self)

    async def close(self):
        self.closed = True


def settings(**overrides):
    return RecommendSettings(
        embedding_provider="sf",
        embedding_model="bge-m3",
        embedding_query_prefix="query: ",
        **overrides,
    )


async def test_embed_returns_pinned_dense_and_sparse_vectors():
    client = FakeClient([0.1] * 1024)
    adapter = LiveQueryEmbeddingAdapter(client=client, settings=settings())
    result = await adapter.embed(snapshot(), "图学习 图学习")
    assert result.vector == tuple([0.1] * 1024)
    assert result.embedding_fingerprint == "fp-x"
    assert result.sparse_vector["indices"]
    assert client.calls[0]["input"] == ["query: 图学习 图学习"]


def test_sparse_query_vector_is_deterministic_and_versioned():
    first = sparse_bm25_query_vector("图学习 图学习", tokenizer_version="v1")
    assert first == sparse_bm25_query_vector("图学习 图学习", tokenizer_version="v1")
    assert first != sparse_bm25_query_vector("图学习 图学习", tokenizer_version="v2")
    assert first["indices"] == sorted(first["indices"])


@pytest.mark.parametrize(
    ("setting_overrides", "code"),
    [
        ({"embedding_provider": "other"}, "embedding_provider_mismatch"),
        ({"embedding_model": "other"}, "embedding_model_mismatch"),
    ],
)
async def test_embed_rejects_snapshot_pin_mismatch(setting_overrides, code):
    base = {
        "embedding_provider": "sf",
        "embedding_model": "bge-m3",
    }
    base.update(setting_overrides)
    adapter = LiveQueryEmbeddingAdapter(
        client=FakeClient([0.1] * 1024), settings=RecommendSettings(**base),
    )
    with pytest.raises(RecommendationRuntimeError) as raised:
        await adapter.embed(snapshot(), "safe query")
    assert raised.value.code == code


async def test_embed_rejects_dimension_mismatch():
    adapter = LiveQueryEmbeddingAdapter(
        client=FakeClient([0.1, 0.2]), settings=settings(),
    )
    with pytest.raises(RecommendationRuntimeError) as raised:
        await adapter.embed(snapshot(), "safe query")
    assert raised.value.code == "embedding_dimension_mismatch"
    assert raised.value.retryable is False


async def test_embed_retries_timeout_without_logging_query_or_key(caplog):
    client = FakeClient(asyncio.TimeoutError(), [0.1] * 1024)
    adapter = LiveQueryEmbeddingAdapter(
        client=client,
        settings=settings(embedding_api_key="do-not-log", embedding_max_retries=1),
    )
    await adapter.embed(snapshot(), "private-query")
    assert len(client.calls) == 2
    assert "private-query" not in caplog.text
    assert "do-not-log" not in caplog.text


async def test_embed_timeout_is_retryable_and_close_closes_client():
    client = FakeClient(asyncio.TimeoutError())
    adapter = LiveQueryEmbeddingAdapter(
        client=client, settings=settings(embedding_max_retries=0),
    )
    with pytest.raises(RecommendationRuntimeError) as raised:
        await adapter.embed(snapshot(), "safe query")
    assert raised.value.code == "embedding_unavailable"
    assert raised.value.retryable is True
    await adapter.aclose()
    assert client.closed is True
