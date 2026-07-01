from __future__ import annotations

import pytest

from dext_recommend import (
    CoverageStat,
    EmbeddingResult,
    FakeQueryEmbeddingPort,
    FakeVectorSearchPort,
    ProfessorFact,
    QueryUnderstanding,
    ReadinessReport,
    RecommendedProfessor,
    VectorHit,
)


def test_query_understanding_collections_are_immutable():
    query = QueryUnderstanding([], [], [], [], None, None, [], False, 1.0)
    with pytest.raises((AttributeError, TypeError)):
        query.research_interests.append("NLP")


def test_recommended_professor_score_components_are_deeply_immutable():
    professor = RecommendedProfessor(
        entity_id="e", display_name="P", university="U", org_units=[],
        title="T", title_family="professor", master_eligibility="confirmed",
        phd_eligibility="confirmed", role_status="included", profile_url=None,
        research_summary=None, match_level="strong", short_reasons=[], score=1.0,
        score_components={"nested": {"parts": [1.0]}}, matched_topics=[],
        matched_statements=[], matched_publications=[], evidence_refs=[],
        risk_flags=[], available_actions=[],
    )
    with pytest.raises(TypeError):
        professor.score_components["new"] = 1.0
    with pytest.raises((AttributeError, TypeError)):
        professor.score_components["nested"]["parts"].append(2.0)


def test_port_payload_dtos_are_deeply_immutable():
    hit = VectorHit("e", 1.0, {"nested": ["x"]})
    embedding = EmbeddingResult([0.1], "fp", {"indices": [1], "values": [0.5]})
    fact = ProfessorFact(
        "e", "P", "U", ["org"], "T", "professor", "confirmed",
        "confirmed", "included", None, None, None,
    )
    with pytest.raises((AttributeError, TypeError)):
        hit.payload["nested"].append("y")
    with pytest.raises((AttributeError, TypeError)):
        embedding.sparse_vector["indices"].append(2)
    with pytest.raises((AttributeError, TypeError)):
        fact.org_units.append("other")


def test_readiness_report_collections_are_immutable():
    report = ReadinessReport(
        ready=False,
        snapshot=None,
        errors=[],
        payload_coverage={"org_unit_ids": CoverageStat("org_unit_ids", 1.0, 1, True)},
    )
    with pytest.raises((AttributeError, TypeError)):
        report.errors.append("error")
    with pytest.raises(TypeError):
        report.payload_coverage["profile_hash"] = CoverageStat(
            "profile_hash", 1.0, 1, True,
        )


def test_fake_ports_defensively_copy_constructor_inputs():
    vector = [0.1]
    embedding_port = FakeQueryEmbeddingPort(vector, "fp")
    vector.append(0.2)
    assert embedding_port._vector == (0.1,)

    hits = [VectorHit("e1", 1.0, {})]
    search_port = FakeVectorSearchPort(hits=hits)
    hits.append(VectorHit("e2", 0.5, {}))
    assert len(search_port._hits) == 1
