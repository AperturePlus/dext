# tests/dext_recommend/_recfixtures.py
"""Explicit factory module for R3 recommend-core tests.

Every function returns a fresh object so tests never share mutable state.
No pytest fixtures (no conftest sharing) — call these directly in tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from dext_grounded import FactBundle, FakeLLMGenerationPort, GenerationResult, SourceRef
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactItem

from dext_recommend import (
    ActiveBuildSnapshot, ProfessorDetail, ProfessorFact, VectorHit,
)
from dext_recommend.core.generation_profile import RecommendGenerationProfile


def generation_profile() -> RecommendGenerationProfile:
    return RecommendGenerationProfile.from_file(
        Path("data/recommend/generation-profile.json")
    )


def _empty_fact_bundle(*, build_id: str, entity_id: str) -> FactBundle:
    return FactBundle(
        build_id=build_id, subject_id=entity_id,
        facts=(FactItem(field="display_name", value=entity_id,
                        content_class=ContentClass.UNCERTAIN),),
        source_refs=(),
    )


def snapshot(build_id: str = "b-1", fingerprint: str = "fp-x",
             ranking_profile_version: str = "r1") -> ActiveBuildSnapshot:
    return ActiveBuildSnapshot(
        build_id=build_id, catalog_schema_version=6,
        neo4j_active_build_id=build_id, qdrant_alias_target="phys-1",
        qdrant_payload_schema_version=2, embedding_provider="sf",
        embedding_model="bge-m3", embedding_dimension=1024,
        embedding_fingerprint=fingerprint, taxonomy_version="t1",
        ranking_profile_version=ranking_profile_version,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )


def ranking_profile_dict(
    *, version: str = "r1",
    weights: dict | None = None,
    rrf_k: int = 60,
    oversample_steps: tuple[int, ...] = (200, 400, 800, 1000),
    detail_rerank_window: int = 50,
    detail_fetch_concurrency: int = 8,
    detail_rerank_window_max: int = 100,
    match_level_thresholds: dict | None = None,
    tie_break: tuple[str, ...] = ("score", "semantic_score", "evidence_count", "entity_id"),
    same_field_boost_per_topic: float = 0.05,
    same_field_boost_max: float = 0.15,
) -> dict:
    return {
        "version": version,
        "weights": weights or {
            "semantic_score": 0.50,
            "topic_statement_score": 0.18,
            "student_fit_score": 0.12,
            "eligibility_score": 0.08,
            "provenance_score": 0.08,
            "completeness_score": 0.04,
        },
        "rrf_k": rrf_k,
        "oversample_steps": oversample_steps,
        "detail_rerank_window": detail_rerank_window,
        "detail_fetch_concurrency": detail_fetch_concurrency,
        "detail_rerank_window_max": detail_rerank_window_max,
        "match_level_thresholds": match_level_thresholds or {
            "excellent": 0.75, "strong": 0.55, "possible": 0.35,
        },
        "tie_break": tie_break,
        "same_field_boost_per_topic": same_field_boost_per_topic,
        "same_field_boost_max": same_field_boost_max,
    }


def vector_hits_case(name: str) -> tuple[VectorHit, ...]:
    """Return hits for a named topology. payload and fact stay consistent
    except `e_other_org` where payload pretends to match but fact does not."""
    if name == "happy":
        return (
            VectorHit("e_cv_strong", 0.95, {
                "university_id": "u_demo", "city_name": "北京",
                "org_unit_ids": ["ou_cs"], "title_family": "professor",
                "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
                "role_status": "included", "topic_ids": ["topic_cv", "topic_ml"],
            }),
            VectorHit("e_cv_excluded", 0.90, {
                "university_id": "u_demo", "city_name": "北京",
                "org_unit_ids": ["ou_cs"], "title_family": "professor",
                "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
                "role_status": "excluded", "topic_ids": [],
            }),
        )
    if name == "other_org":
        # payload claims ou_cs but hydrated fact has ou_math
        return (
            VectorHit("e_other_org", 0.80, {
                "university_id": "u_demo", "city_name": "北京",
                "org_unit_ids": ["ou_cs"], "title_family": "professor",
                "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
                "role_status": "included", "topic_ids": [],
            }),
        )
    if name == "review":
        return (
            VectorHit("e_cv_review", 0.85, {
                "university_id": "u_demo", "city_name": "北京",
                "org_unit_ids": ["ou_cs"], "title_family": "professor",
                "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
                "role_status": "review", "topic_ids": ["topic_cv"],
            }),
        )
    if name == "anchor":
        return (
            VectorHit("e_nlp_anchor", 0.92, {
                "university_id": "u_demo", "city_name": "北京",
                "org_unit_ids": ["ou_cs"], "title_family": "professor",
                "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
                "role_status": "included", "topic_ids": ["topic_nlp"],
            }),
        )
    if name == "no_statement":
        return (
            VectorHit("e_no_statement", 0.70, {
                "university_id": "u_demo", "city_name": "北京",
                "org_unit_ids": ["ou_cs"], "title_family": "professor",
                "master_eligibility": "confirmed", "phd_eligibility": "confirmed",
                "role_status": "included", "topic_ids": [],
            }),
        )
    raise ValueError(f"unknown vector_hits_case: {name}")


def professor_facts_case(name: str) -> dict[str, ProfessorFact]:
    """Return hydrated facts. authority fields used for hard filters;
    display fields for cards. e_other_org's fact overrides payload."""
    def _fact(eid: str, *, role: str = "included",
              org_unit_ids: tuple[str, ...] = ("ou_cs",),
              university_id: str = "u_demo", city_name: str = "北京",
              title_family: str = "professor", master: str = "confirmed",
              phd: str = "confirmed", research_summary: str | None = "summary",
              topic_ids: tuple[str, ...] = ("topic_cv",)) -> ProfessorFact:
        return ProfessorFact(
            entity_id=eid, display_name=eid.replace("_", " ").title(),
            university="示例大学", org_units=("计算机学院",), title="Prof",
            title_family=title_family, master_eligibility=master,
            phd_eligibility=phd, role_status=role, profile_url=None,
            profile_hash=None, research_summary=research_summary,
            university_id=university_id, city_name=city_name,
            org_unit_ids=org_unit_ids, topic_ids=topic_ids,
        )
    if name == "happy":
        return {
            "e_cv_strong": _fact("e_cv_strong"),
            "e_cv_excluded": _fact("e_cv_excluded", role="excluded"),
        }
    if name == "other_org":
        return {"e_other_org": _fact("e_other_org", org_unit_ids=("ou_math",))}
    if name == "review":
        return {"e_cv_review": _fact("e_cv_review", role="review")}
    if name == "anchor":
        return {"e_nlp_anchor": _fact("e_nlp_anchor", topic_ids=("topic_nlp",))}
    if name == "no_statement":
        return {"e_no_statement": _fact("e_no_statement", research_summary=None,
                                        topic_ids=())}
    raise ValueError(f"unknown professor_facts_case: {name}")


def professor_details_case(name: str) -> dict[str, ProfessorDetail]:
    def _detail(eid: str, *, topics: tuple[str, ...] = ("topic_cv",),
                statements: tuple[str, ...] = ("NLP research",),
                pubs: tuple[str, ...] = ("paper A",),
                source_urls: tuple[str, ...] = ("http://example/p",),
                risk: tuple[str, ...] = ()) -> ProfessorDetail:
        return ProfessorDetail(
            build_id="b-1", profile_hash=None, entity_id=eid,
            display_name=eid.replace("_", " ").title(), university="示例大学",
            org_units=("计算机学院",), title="Prof", title_family="professor",
            master_eligibility="confirmed", phd_eligibility="confirmed",
            role_status="included", profile_url=None,
            research_statements=statements, approved_topics=topics,
            selected_publication_mentions=pubs, bio_snippets=(),
            source_urls=source_urls, provenance_refs=(), quality_findings=(),
            risk_flags=risk,
            fact_bundle=_empty_fact_bundle(build_id="b-1", entity_id=eid),
        )
    if name == "happy":
        return {"e_cv_strong": _detail("e_cv_strong")}
    if name == "anchor":
        return {"e_nlp_anchor": _detail("e_nlp_anchor", topics=("topic_nlp",))}
    if name == "no_statement":
        return {"e_no_statement": _detail(
            "e_no_statement", topics=(), statements=(), pubs=(), source_urls=())}
    raise ValueError(f"unknown professor_details_case: {name}")


def coverage_flags_case(build_id: str = "b-1", **flags) -> dict[str, dict[str, bool]]:
    defaults = {"org_unit_ids": True}
    defaults.update(flags)
    return {build_id: defaults}


def fake_llm_for_understanding(output: dict) -> FakeLLMGenerationPort:
    return FakeLLMGenerationPort(preset=GenerationResult(output=output))
