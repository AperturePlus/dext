"""Dialect-agnostic mapping from raw rows/dicts to release observations.

These pure functions consume Mapping / dict (never sqlite3.Row, Qdrant point
or Neo4j record), so a future CatalogPgReader reuses them unchanged.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation, GraphReleaseObservation, PayloadCoverageObservation,
    ProfessorReleaseSample, VectorReleaseObservation,
)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _opt_str(row: Mapping[str, object], key: str) -> str | None:
    """Return None if the key is missing/None, else str(value)."""
    val = row.get(key)
    return None if val is None else str(val)


def map_catalog_release(
    row: Mapping[str, object], sample_ids: tuple[str, ...],
) -> CatalogReleaseObservation:
    return CatalogReleaseObservation(
        build_id=str(row["build_id"]),
        catalog_schema_version=int(row["catalog_schema_version"]),
        qdrant_payload_schema_version=int(row["qdrant_payload_schema_version"]),
        embedding_provider=str(row["embedding_provider"]),
        embedding_model=str(row["embedding_model"]),
        embedding_dimension=int(row["embedding_dimension"]),
        embedding_fingerprint=str(row["embedding_fingerprint"]),
        taxonomy_version=_opt_str(row, "taxonomy_version"),
        expected_professor_count=int(row["expected_professor_count"]),
        sample_entity_ids=sample_ids,
        created_at=_parse_dt(str(row["created_at"])),
    )


def map_professor_sample(row: Mapping[str, object]) -> ProfessorReleaseSample:
    org_units = row.get("org_unit_ids") or ()
    return ProfessorReleaseSample(
        entity_id=str(row["entity_id"]),
        org_unit_ids=tuple(org_units),
        profile_hash=_opt_str(row, "profile_hash"),
        role_status=_opt_str(row, "role_status"),
        master_eligibility=_opt_str(row, "master_eligibility"),
        phd_eligibility=_opt_str(row, "phd_eligibility"),
        embedding_fingerprint=_opt_str(row, "embedding_fingerprint"),
    )


def map_vector_release(raw: Mapping[str, object]) -> VectorReleaseObservation:
    samples = tuple(map_professor_sample(s) for s in (raw.get("samples") or ()))
    coverage = tuple(
        PayloadCoverageObservation(
            field=str(c["field"]),
            covered=float(c["covered"]),
            sample_size=int(c["sample_size"]),
            invalid_count=int(c.get("invalid_count", 0)),
            mismatch_count=int(c.get("mismatch_count", 0)),
        )
        for c in (raw.get("coverage") or ())
    )
    return VectorReleaseObservation(
        alias=str(raw["alias"]),
        target_collection=str(raw["target_collection"]),
        build_id=str(raw["build_id"]),
        payload_schema_version=int(raw["payload_schema_version"]),
        embedding_fingerprint=str(raw["embedding_fingerprint"]),
        embedding_dimension=int(raw["embedding_dimension"]),
        point_count=int(raw["point_count"]),
        samples=samples,
        coverage=coverage,
    )


def map_graph_release(raw: Mapping[str, object]) -> GraphReleaseObservation:
    samples = tuple(map_professor_sample(s) for s in (raw.get("samples") or ()))
    return GraphReleaseObservation(
        build_id=str(raw["build_id"]),
        samples=samples,
    )


__all__ = [
    "map_catalog_release", "map_graph_release", "map_professor_sample",
    "map_vector_release",
]
