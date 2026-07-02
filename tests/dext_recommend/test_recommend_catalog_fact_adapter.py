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


import pytest

from dext_grounded import FactBundle, SourceRef

from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFactNotFound, ViewerPermissions,
)

from tests.dext_recommend._factfixtures import dumps


def _full_entity_db(tmp_path, *, role_status="included", profile_hash="h1",
                    email="e@x", phone="123"):
    profs = [
        ("e1", "b1", "A", "Prof.", "professor", role_status, "[]",
         "confirmed", "unknown", "areas", "bio1", email, phone,
         "https://x/p", "https://x/e", 1, 0.9),
    ]
    payload = dumps({
        "university_id": "u1", "org_unit_ids": ["ou_cs"], "city": "Beijing",
        "topic_ids": [], "profile_hash": profile_hash,
        "provenance_ref": "catalog:entity:e1:build:b1",
    })
    profiles = [
        ("b1", "e1", profile_hash or "", "tv", "ti", "np", 10, payload,
         "2026-01-01T00:00:00+00:00"),
    ]
    observations = [
        ("obs1", "u1", "snap1", "https://x/p?utm_source=foo", "single_profile",
         dumps({"affiliations": [{"org_unit_name": "Dept CS"}]}),
         "rh1", "direct", "b1", "b1", 1),
    ]
    entity_observations = [("e1", "obs1", "strong", "b1")]
    statements = [
        ("s1", "b1", "e1", "obs1", "raw", "works on NLP", "en", "sh1"),
    ]
    mentions = [
        ("m1", "b1", "e1", "obs1", "raw", "Paper A", 2024, 0.9, 0),
    ]
    topics = [("tax-v1", "t1", "NLP", "nlp", "method", "active", "llm")]
    topic_links = [
        ("b1", "s1", "tax-v1", "t1", "PRIMARY_TOPIC", "span", "llm", 0.95,
         "approved", "catalog:research-statement:b1:s1"),
    ]
    findings = [
        ("f1", "b1", "warning", "incomplete_profile", "e1", None,
         dumps({"note": "x"}), 0),
    ]
    source_docs = [
        ("doc1", "u1", "https://x/p", "ch1", "2026-01-01T00:00:00+00:00", "b1", "b1"),
    ]
    source_tasks = [
        ("b1", "u1", "Tsinghua", "tsinghua", "/src", 0, "COMPLETED",
         "2026-01-01T00:00:00+00:00"),
    ]
    return build_catalog_db(
        tmp_path, schema_version=6, graph_builds=[_active_build()],
        canonical_professors=profs, professor_profiles=profiles,
        professor_observations=observations, entity_observations=entity_observations,
        research_statements=statements, publication_mentions=mentions,
        topics=topics, statement_topic_links=topic_links,
        quality_findings=findings, source_documents=source_docs,
        build_source_tasks=source_tasks,
        entities=[("e1", "professor", "active", None,
                   "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00")],
    )


def test_get_detail_assembles_full_professor_detail(tmp_path):
    path = _full_entity_db(tmp_path)
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    detail = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(),
    ))
    assert detail.build_id == "b1"
    assert detail.entity_id == "e1"
    assert detail.display_name == "A"
    assert detail.university == "Tsinghua"
    assert detail.org_units == ("Dept CS",)  # from observation affiliations
    assert detail.research_statements == ("works on NLP",)
    assert detail.approved_topics == ("NLP",)
    assert detail.selected_publication_mentions == ("Paper A",)
    # source URL canonicalized (utm stripped)
    assert detail.source_urls == ("https://x/p",)
    assert detail.quality_findings == ("incomplete_profile",)
    # contacts gated off
    assert dict(detail.contacts) == {}


def test_get_detail_fact_bundle_invariants(tmp_path):
    path = _full_entity_db(tmp_path)
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    detail = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(),
    ))
    assert isinstance(detail.fact_bundle, FactBundle)
    assert detail.fact_bundle.build_id == detail.build_id == "b1"
    assert detail.fact_bundle.subject_id == detail.entity_id == "e1"
    assert tuple(detail.fact_bundle.source_refs) == tuple(detail.provenance_refs)
    # every FactItem has a source_ref or is UNCERTAIN
    from dext_grounded.content import ContentClass
    for item in detail.fact_bundle.facts:
        if not item.source_refs:
            assert item.content_class is ContentClass.UNCERTAIN


def test_get_detail_contacts_double_gated(tmp_path):
    path = _full_entity_db(tmp_path, email="prof@x", phone="555")
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    # both flags True -> contacts present
    d1 = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=True,
        viewer_permissions=ViewerPermissions(include_contacts=True),
    ))
    assert d1.contacts["email"] == "prof@x"
    assert d1.contacts["phone"] == "555"
    # request asks but viewer lacks permission -> empty
    d2 = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=True,
        viewer_permissions=ViewerPermissions(include_contacts=False),
    ))
    assert dict(d2.contacts) == {}
    # viewer permits but request doesn't ask -> empty
    d3 = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(include_contacts=True),
    ))
    assert dict(d3.contacts) == {}
    # contacts never in fact_bundle
    for d in (d1, d2, d3):
        for item in d.fact_bundle.facts:
            assert item.field not in ("email", "phone")
        for ref in d.fact_bundle.source_refs:
            assert "@" not in ref.quote_or_summary


def test_get_detail_missing_raises_professor_fact_not_found(tmp_path):
    path = build_catalog_db(tmp_path, schema_version=6, graph_builds=[_active_build()])
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    with pytest.raises(ProfessorFactNotFound):
        asyncio.run(adapter.get_detail(
            _snapshot(), "nope", include_contacts=False,
            viewer_permissions=ViewerPermissions(),
        ))


def test_get_detail_excluded_raises_not_found(tmp_path):
    path = _full_entity_db(tmp_path, role_status="excluded")
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    with pytest.raises(ProfessorFactNotFound):
        asyncio.run(adapter.get_detail(
            _snapshot(), "e1", include_contacts=False,
            viewer_permissions=ViewerPermissions(),
        ))


def test_get_detail_review_gated_by_viewer_permissions(tmp_path):
    path = _full_entity_db(tmp_path, role_status="review")
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    # viewer cannot see review -> not found
    with pytest.raises(ProfessorFactNotFound):
        asyncio.run(adapter.get_detail(
            _snapshot(), "e1", include_contacts=False,
            viewer_permissions=ViewerPermissions(can_view_review=False),
        ))
    # viewer can see review -> returned
    d = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(can_view_review=True),
    ))
    assert d.role_status == "review"


def test_get_detail_profile_hash_missing_adds_risk(tmp_path):
    path = _full_entity_db(tmp_path, profile_hash=None)
    adapter = CatalogProfessorFactAdapter(
        CatalogSqliteFactReader(path), settings=RecommendSettings(),
    )
    d = asyncio.run(adapter.get_detail(
        _snapshot(), "e1", include_contacts=False,
        viewer_permissions=ViewerPermissions(),
    ))
    assert d.profile_hash is None
    assert "profile_hash_missing" in d.risk_flags
