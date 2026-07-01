import pytest

from dext_recommend.adapters._vector_reader import QdrantReader
from dext_recommend.adapters.vector_release import VectorReleaseAdapter
from dext_recommend.ports.release_readback import ReadinessSourceError


class FakeAlias:
    def __init__(self, alias_name, collection_name):
        self.alias_name = alias_name
        self.collection_name = collection_name


class FakeCountResult:
    def __init__(self, count): self.count = count


class FakePoint:
    def __init__(self, pid, payload): self.id = pid; self.payload = payload


class FakeAsyncQdrantClient:
    def __init__(self, *, alias_target=None, count=0, points=None, raise_on_aliases=False):
        self._alias_target = alias_target
        self._count = count
        self._points = points or []
        self._raise_on_aliases = raise_on_aliases

    async def get_aliases(self):
        if self._raise_on_aliases:
            raise RuntimeError("connection refused")
        if self._alias_target is None:
            return type("R", (), {"aliases": []})()
        return type("R", (), {"aliases": [FakeAlias("dext_professors_current", self._alias_target)]})()

    async def count(self, collection_name, *, exact=True):
        return FakeCountResult(self._count)

    async def scroll(self, collection_name, *, limit, offset=None, with_payload=True, with_vectors=False):
        pts = self._points[:limit]
        points = [FakePoint(p["entity_id"], p) for p in pts]
        return points, None


async def test_read_current_returns_observation():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=11801,
        points=[{
            "entity_id": "e1", "build_id": "b1", "profile_hash": "h1",
            "role_status": "included", "master_eligibility": "confirmed",
            "phd_eligibility": "unknown", "embedding_fingerprint": "fp-1",
            "org_unit_ids": ["org-a"],
        }],
    )
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    obs = await adapter.read_current("dext_professors_current", ("e1",))
    assert obs is not None
    assert obs.alias == "dext_professors_current"
    assert obs.target_collection == "dext_professors__b1"
    assert obs.build_id == "b1"
    assert obs.point_count == 11801
    assert len(obs.samples) == 1


async def test_read_current_returns_none_when_alias_missing():
    client = FakeAsyncQdrantClient(alias_target=None)
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    assert await adapter.read_current("dext_professors_current", ("e1",)) is None


async def test_read_current_raises_readiness_source_error_on_connection_failure():
    client = FakeAsyncQdrantClient(raise_on_aliases=True)
    reader = QdrantReader(client, embedding_dimension=1536, embedding_fingerprint="fp-1")
    adapter = VectorReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_current("dext_professors_current", ("e1",))
