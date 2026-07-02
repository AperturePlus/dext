import pytest

from dext_recommend.adapters._vector_reader import QdrantReader, parse_build_id_from_collection
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
    def __init__(self, *, alias_target=None, count=0, points=None, raise_on_aliases=False,
                 dimension=1536):
        self._alias_target = alias_target
        self._count = count
        self._points = points or []
        self._raise_on_aliases = raise_on_aliases
        self._dimension = dimension

    async def get_aliases(self):
        if self._raise_on_aliases:
            raise RuntimeError("connection refused")
        if self._alias_target is None:
            return type("R", (), {"aliases": []})()
        return type("R", (), {"aliases": [FakeAlias("dext_professors_current", self._alias_target)]})()

    async def count(self, collection_name, *, exact=True):
        return FakeCountResult(self._count)

    async def get_collection(self, *, collection_name):
        dense = type("Dense", (), {"size": self._dimension})()
        params = type("Params", (), {"vectors": {"dense": dense}})()
        config = type("Config", (), {"params": params})()
        return type("Collection", (), {"config": config})()

    async def retrieve(self, *, collection_name, ids, with_payload=True, with_vectors=False):
        wanted = set(ids)
        return [
            FakePoint(p["entity_id"], p)
            for p in self._points if p["entity_id"] in wanted
        ]

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
    reader = QdrantReader(client)
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
    reader = QdrantReader(client)
    adapter = VectorReleaseAdapter(reader)
    assert await adapter.read_current("dext_professors_current", ("e1",)) is None


async def test_read_current_raises_readiness_source_error_on_connection_failure():
    client = FakeAsyncQdrantClient(raise_on_aliases=True)
    reader = QdrantReader(client)
    adapter = VectorReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_current("dext_professors_current", ("e1",))


def test_parse_build_id_from_collection_suffix():
    assert parse_build_id_from_collection("dext_professors__b1") == "b1"


def test_parse_build_id_from_collection_multi_segment():
    # build ids may contain underscores; only the dext_professors__ prefix is stripped
    assert parse_build_id_from_collection("dext_professors__2026_07_01_b1") == "2026_07_01_b1"


def test_parse_build_id_unparseable_collection_raises():
    with pytest.raises(ReadinessSourceError) as raised:
        parse_build_id_from_collection("dext_professors_current")
    assert raised.value.source == "qdrant"


def test_parse_build_id_missing_prefix_raises():
    with pytest.raises(ReadinessSourceError):
        parse_build_id_from_collection("random_name")


def test_parse_build_id_empty_suffix_raises():
    with pytest.raises(ReadinessSourceError):
        parse_build_id_from_collection("dext_professors__")


async def test_read_current_build_id_from_collection_not_samples():
    # samples carry a DIFFERENT build_id in the payload; the collection name wins
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__real_build",
        count=1,
        points=[{
            "entity_id": "e1", "build_id": "STALE_PAYLOAD_BUILD",
            "profile_hash": "h1", "master_eligibility": "confirmed",
            "embedding_fingerprint": "fp-1", "org_unit_ids": ["org-a"],
        }],
    )
    reader = QdrantReader(client)
    adapter = VectorReleaseAdapter(reader)
    obs = await adapter.read_current("dext_professors_current", ("e1",))
    assert obs.build_id == "real_build"


async def test_read_current_build_id_when_samples_empty():
    # An empty physical collection cannot prove its embedding fingerprint.
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=0, points=[],
    )
    reader = QdrantReader(client)
    adapter = VectorReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_current("dext_professors_current", ("e-missing",))


async def test_read_current_raises_when_collection_unparseable():
    # alias resolves to a collection whose name does not encode build_id
    client = FakeAsyncQdrantClient(alias_target="legacy_collection", count=1, points=[])
    reader = QdrantReader(client)
    adapter = VectorReleaseAdapter(reader)
    with pytest.raises(ReadinessSourceError):
        await adapter.read_current("dext_professors_current", ("e1",))


def _coverage_field(rows, field):
    for r in rows:
        if r["field"] == field:
            return r
    return None


async def test_coverage_rows_include_eligibility_from_master_eligibility():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=2,
        points=[
            {"entity_id": "e1", "master_eligibility": "confirmed",
             "embedding_fingerprint": "fp-1"},
            {"entity_id": "e2", "master_eligibility": None,
             "embedding_fingerprint": "fp-1"},
        ],
    )
    reader = QdrantReader(client)
    raw = await reader.read_current("dext_professors_current", ("e1", "e2"))
    elig = _coverage_field(raw["coverage"], "eligibility")
    assert elig is not None
    assert elig["covered"] == 0.5
    assert elig["sample_size"] == 2


async def test_coverage_rows_eligibility_full_when_all_master_eligible():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=1,
        points=[{"entity_id": "e1", "master_eligibility": "confirmed",
                 "embedding_fingerprint": "fp-1"}],
    )
    reader = QdrantReader(client)
    raw = await reader.read_current("dext_professors_current", ("e1",))
    elig = _coverage_field(raw["coverage"], "eligibility")
    assert elig["covered"] == 1.0


async def test_read_current_reads_dimension_and_fingerprint_from_qdrant():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=1, dimension=768,
        points=[{"entity_id": "e1", "embedding_fingerprint": "live-fp"}],
    )
    raw = await QdrantReader(client).read_current(
        "dext_professors_current", ("e1",),
    )
    assert raw["embedding_dimension"] == 768
    assert raw["embedding_fingerprint"] == "live-fp"


async def test_read_current_rejects_missing_payload_fingerprint():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=1,
        points=[{"entity_id": "e1"}],
    )
    with pytest.raises(ReadinessSourceError):
        await QdrantReader(client).read_current(
            "dext_professors_current", ("e1",),
        )


async def test_read_current_reads_one_arbitrary_payload_when_sample_ids_empty():
    client = FakeAsyncQdrantClient(
        alias_target="dext_professors__b1", count=1,
        points=[{"entity_id": "e1", "embedding_fingerprint": "fp-1"}],
    )
    raw = await QdrantReader(client).read_current(
        "dext_professors_current", (),
    )
    assert raw["embedding_fingerprint"] == "fp-1"
    assert raw["samples"] == []
