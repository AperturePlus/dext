import pytest

from dext_recommend.adapters._graph_reader import Neo4jReader
from dext_recommend.adapters.graph_release import GraphReleaseAdapter
from dext_recommend.ports.release_readback import ReadinessSourceError


class FakeRecord:
    def __init__(self, data): self._data = data
    def __getitem__(self, key): return self._data[key]
    def keys(self): return self._data.keys()


class FakeResult:
    def __init__(self, records): self._records = records
    async def single(self, *, strict=False): return self._records[0] if self._records else None
    def __aiter__(self): self._i = 0; return self
    async def __anext__(self):
        if self._i >= len(self._records): raise StopAsyncIteration
        r = self._records[self._i]; self._i += 1; return r


class FakeSession:
    def __init__(self, *, pointer=None, samples=None, raise_on_run=False):
        self._pointer = pointer
        self._samples = samples or []
        self._raise = raise_on_run

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def run(self, query, **params):
        if self._raise:
            raise RuntimeError("neo4j unreachable")
        if "GraphState" in query:
            return FakeResult([FakeRecord({"build_id": self._pointer})] if self._pointer else [])
        return FakeResult([FakeRecord(s) for s in self._samples])


class FakeDriver:
    def __init__(self, session):
        self._session = session
        self.session_kwargs = []

    async def verify_connectivity(self): pass

    def session(self, **kw):
        self.session_kwargs.append(kw)
        return self._session

    async def close(self): pass


async def test_read_active_returns_observation():
    session = FakeSession(pointer="b1", samples=[{
        "entity_id": "e1", "build_id": "b1", "profile_hash": "h1",
        "role_status": "included", "master_eligibility": "confirmed",
        "phd_eligibility": "unknown", "embedding_fingerprint": "fp-1",
        "org_unit_ids": ["org-a"],
    }])
    reader = Neo4jReader(FakeDriver(session))
    adapter = GraphReleaseAdapter(reader)
    obs = await adapter.read_active(("e1",))
    assert obs is not None
    assert obs.build_id == "b1"
    assert len(obs.samples) == 1


async def test_read_active_uses_configured_database():
    session = FakeSession(pointer="b1")
    driver = FakeDriver(session)
    reader = Neo4jReader(driver, database="neo4j-test")
    adapter = GraphReleaseAdapter(reader)
    await adapter.read_active(())
    assert driver.session_kwargs == [{"database": "neo4j-test"}, {"database": "neo4j-test"}]


async def test_read_active_returns_none_when_pointer_missing():
    session = FakeSession(pointer=None)
    reader = Neo4jReader(FakeDriver(session))
    adapter = GraphReleaseAdapter(reader)
    assert await adapter.read_active(("e1",)) is None


async def test_read_active_raises_on_connection_failure():
    session = FakeSession(raise_on_run=True)
    reader = Neo4jReader(FakeDriver(session))
    adapter = GraphReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_active(("e1",))
