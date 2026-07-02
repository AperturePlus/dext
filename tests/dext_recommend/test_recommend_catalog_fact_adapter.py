import asyncio

from dext_recommend.adapters._catalog_fact_reader import CatalogSqliteFactReader
from dext_recommend.adapters.catalog_professor_facts import CatalogProfessorFactAdapter
from dext_recommend.config import RecommendSettings
from dext_recommend.readiness import ActiveBuildSnapshot
from datetime import datetime, timezone

from tests.dext_recommend._factfixtures import build_catalog_db, dumps


def _active_build():
    return ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}")


def _snapshot(build_id="b1"):
    return ActiveBuildSnapshot(
        build_id=build_id, catalog_schema_version=6, neo4j_active_build_id="b1",
        qdrant_alias_target="phys-1", qdrant_payload_schema_version=6,
        embedding_provider="openai", embedding_model="m", embedding_dimension=1536,
        embedding_fingerprint="fp", taxonomy_version="tax-v1",
        ranking_profile_version="ranking-v1", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _profile(entity_id, **kw):
    base = {"university_id": "u1", "org_unit_ids": ["ou_cs"], "city": None,
            "topic_ids": [], "profile_hash": f"h_{entity_id}"}
    base.update(kw)
    return dumps(base)


def test_hydrate_returns_facts_with_authority_fields(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, "https://x/e1", None, 1, 0.9),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10,
         _profile("e1", university_id="u_tsinghua", org_unit_ids=["ou_cs", "ou_ai"],
                  city="Beijing", topic_ids=["t1", "t2"]),
         "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot(), ["e1", "e_missing"]))
    assert set(out) == {"e1"}
    f = out["e1"]
    assert f.entity_id == "e1"
    assert f.display_name == "A"
    assert f.university_id == "u_tsinghua"
    assert f.city_name == "Beijing"
    assert f.org_unit_ids == ("ou_cs", "ou_ai")
    assert f.topic_ids == ("t1", "t2")
    assert f.master_eligibility == "confirmed"
    assert f.role_status == "included"
    assert f.profile_hash == "h1"


def test_hydrate_silently_drops_excluded_and_inactive(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ("e2", "b1", "B", "Prof.", "professor", "excluded", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
        ("e3", "b1", "C", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 0, 0.5),
    ]
    profiles = [
        ("b1", eid, f"h_{eid}", "tv", "ti", "np", 10, _profile(eid),
         "2026-01-01T00:00:00+00:00")
        for eid in ("e1", "e2", "e3")
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot(), ["e1", "e2", "e3", "e_missing"]))
    assert set(out) == {"e1"}


def test_hydrate_dedups_ids(tmp_path):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    profiles = [
        ("b1", "e1", "h1", "tv", "ti", "np", 10, _profile("e1"), "2026-01-01T00:00:00+00:00"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot(), ["e1", "e1", "e1"]))
    assert set(out) == {"e1"}


def test_hydrate_empty_ids_returns_empty(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    assert asyncio.run(adapter.hydrate(_snapshot(), [])) == {}


def test_hydrate_pins_caller_build_id(tmp_path):
    # build b2 with a different professor; hydrate(b1) must not see b2's rows
    profs_b2 = [
        ("e1", "b2", "A2", "Prof.", "professor", "included", "[]",
         "confirmed", "unknown", None, None, None, None, None, None, 1, 0.5),
    ]
    profiles_b2 = [
        ("b2", "e1", "h_b2", "tv", "ti", "np", 10, _profile("e1"), "2026-01-01T00:00:00+00:00"),
    ]
    builds = [
        ("b1", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}"),
        ("b2", "ACTIVE", "cur-v1", "tax-v1", 6, 6, "{}"),
    ]
    path = build_catalog_db(
        tmp_path, schema_version=6, graph_builds=builds,
        canonical_professors=profs_b2, professor_profiles=profiles_b2,
    )
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    out = asyncio.run(adapter.hydrate(_snapshot("b1"), ["e1"]))
    assert out == {}  # e1 only exists under b2
