import json

import pytest
from aiohttp import web

from dext_graph.config import GraphSettings
from dext_graph.embeddings import EmbeddingClient
from dext_graph.models import ValueValidationError


async def _serve(handler):
    app = web.Application()
    app.router.add_post("/v1/embeddings", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/v1"


def _settings(base_url, **overrides):
    values = {
        "embedding_base_url": base_url,
        "embedding_api_key": "top-secret-key",
        "embedding_model": "BAAI/bge-m3",
        "embedding_dimension": 3,
        "embedding_max_retries": 1,
    }
    values.update(overrides)
    return GraphSettings(**values)


async def test_embedding_http_contract_reorders_indexes_and_records_trace():
    received = {}

    async def handler(request):
        received["body"] = await request.json()
        received["authorization"] = request.headers["Authorization"]
        return web.json_response(
            {
                "data": [
                    {"index": 1, "embedding": [4, 5, 6]},
                    {"index": 0, "embedding": [1, 2, 3]},
                ],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            },
            headers={"x-siliconcloud-trace-id": "trace-123"},
        )

    runner, base_url = await _serve(handler)
    metrics = []
    try:
        async with EmbeddingClient(_settings(base_url), metrics.append) as client:
            result = await client.embed(["a", "b"], purpose="test")
    finally:
        await runner.cleanup()
    assert result.vectors == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert received["body"] == {
        "model": "BAAI/bge-m3",
        "input": ["a", "b"],
        "encoding_format": "float",
    }
    assert "dimensions" not in received["body"]
    assert received["authorization"] == "Bearer top-secret-key"
    assert metrics[0].trace_id == "trace-123"
    assert metrics[0].total_tokens == 7
    assert metrics[0].succeeded


@pytest.mark.parametrize("retry_status", [429, 503])
async def test_embedding_retries_rate_limit_and_5xx(retry_status):
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return web.json_response({}, status=retry_status, headers={"Retry-After": "0"})
        return web.json_response(
            {"data": [{"index": 0, "embedding": [1, 0, 0]}], "usage": {}}
        )

    runner, base_url = await _serve(handler)
    metrics = []
    sleeps = []

    async def sleep(value):
        sleeps.append(value)

    try:
        async with EmbeddingClient(
            _settings(base_url), metrics.append, sleep=sleep
        ) as client:
            result = await client.embed(["a"], purpose="retry")
    finally:
        await runner.cleanup()
    assert result.vectors == [[1.0, 0.0, 0.0]]
    assert calls == 2
    assert sleeps == [0.0]
    assert [metric.status_code for metric in metrics] == [retry_status, 200]


@pytest.mark.parametrize("bad_vector", [[1], [float("nan"), 0, 0]])
async def test_embedding_rejects_bad_vectors_without_leaking_key(bad_vector):
    async def handler(_request):
        return web.json_response({"data": [{"index": 0, "embedding": bad_vector}]})

    runner, base_url = await _serve(handler)
    metrics = []
    try:
        async with EmbeddingClient(_settings(base_url), metrics.append) as client:
            with pytest.raises(ValueValidationError) as captured:
                await client.embed(["a"], purpose="bad")
    finally:
        await runner.cleanup()
    assert "top-secret-key" not in str(captured.value)
    assert "top-secret-key" not in json.dumps([row.asdict() for row in metrics])
