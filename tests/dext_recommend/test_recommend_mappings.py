from dext_recommend.adapters._mappings import (
    map_catalog_release, map_graph_release, map_professor_sample,
    map_vector_release,
)


def _catalog_row():
    return {
        "build_id": "b1",
        "catalog_schema_version": 6,
        "qdrant_payload_schema_version": 2,
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 1536,
        "embedding_fingerprint": "fp-1",
        "taxonomy_version": "tax-v1",
        "expected_professor_count": 11801,
        "created_at": "2026-07-01T00:00:00+00:00",
    }


def test_map_catalog_release_passes_sample_ids_through():
    obs = map_catalog_release(_catalog_row(), ("e1", "e2"))
    assert obs.build_id == "b1"
    assert obs.catalog_schema_version == 6
    assert obs.embedding_fingerprint == "fp-1"
    assert obs.expected_professor_count == 11801
    assert obs.sample_entity_ids == ("e1", "e2")


def test_map_professor_sample_normalizes_org_unit_ids():
    sample = map_professor_sample({
        "entity_id": "e1",
        "org_unit_ids": ["org-a", "org-b"],
        "profile_hash": "h1",
        "role_status": "included",
        "master_eligibility": "confirmed",
        "phd_eligibility": "unknown",
        "embedding_fingerprint": "fp-1",
    })
    assert sample.entity_id == "e1"
    assert sample.org_unit_ids == ("org-a", "org-b")
    assert sample.profile_hash == "h1"


def _vector_raw():
    return {
        "alias": "dext_professors_current",
        "target_collection": "dext_professors__b1",
        "build_id": "b1",
        "payload_schema_version": 2,
        "embedding_fingerprint": "fp-1",
        "embedding_dimension": 1536,
        "point_count": 11801,
        "samples": [{
            "entity_id": "e1",
            "org_unit_ids": ["org-a"],
            "profile_hash": "h1",
            "role_status": "included",
            "master_eligibility": "confirmed",
            "phd_eligibility": "unknown",
            "embedding_fingerprint": "fp-1",
        }],
        "coverage": [
            {"field": "org_unit_ids", "covered": 1.0, "sample_size": 50,
             "invalid_count": 0, "mismatch_count": 0},
        ],
    }


def test_map_vector_release_builds_full_observation():
    obs = map_vector_release(_vector_raw())
    assert obs.alias == "dext_professors_current"
    assert obs.target_collection == "dext_professors__b1"
    assert obs.build_id == "b1"
    assert obs.payload_schema_version == 2
    assert obs.embedding_fingerprint == "fp-1"
    assert obs.embedding_dimension == 1536
    assert obs.point_count == 11801
    assert len(obs.samples) == 1
    assert obs.samples[0].entity_id == "e1"
    assert len(obs.coverage) == 1
    assert obs.coverage[0].field == "org_unit_ids"


def test_map_graph_release():
    raw = {
        "build_id": "b1",
        "samples": [{
            "entity_id": "e1",
            "org_unit_ids": ["org-a"],
            "profile_hash": "h1",
            "role_status": "included",
            "master_eligibility": "confirmed",
            "phd_eligibility": "unknown",
            "embedding_fingerprint": "fp-1",
        }],
    }
    obs = map_graph_release(raw)
    assert obs.build_id == "b1"
    assert obs.samples[0].entity_id == "e1"


def test_map_catalog_release_taxonomy_none_passthrough():
    row = _catalog_row()
    row["taxonomy_version"] = None
    obs = map_catalog_release(row, ())
    assert obs.taxonomy_version is None
