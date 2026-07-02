"""ActiveBuildSnapshot + ReadinessService (overview §9, readiness spec).

The snapshot is the immutable per-request binding to an ACTIVE build. One
recommendation / detail / match / outreach / compare request uses exactly
one snapshot; refresh never merges old + new. Real build/refresh logic
lands in R2; this module fixes the data shape now so ports can take it as
an explicit parameter (foundations §5).
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from dext_recommend._readback_call import ReadbackCall, gather_safe
from dext_recommend.config import RecommendSettings
from dext_recommend.errors import (
    ErrorSeverity,
    RecommendationError,
    RecommendationErrorCode,
)
from dext_recommend._immutable import freeze_mapping
from dext_recommend.ports.release_readback import (
    CatalogReleasePort,
    GraphReleasePort,
    RankingProfilePort,
    VectorReleasePort,
)


@dataclass(frozen=True, slots=True)
class ActiveBuildSnapshot:
    build_id: str
    catalog_schema_version: int
    neo4j_active_build_id: str
    qdrant_alias_target: str
    qdrant_payload_schema_version: int
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    embedding_fingerprint: str
    taxonomy_version: str | None
    ranking_profile_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    ready: bool
    snapshot: ActiveBuildSnapshot | None
    errors: tuple[RecommendationError, ...]
    payload_coverage: Mapping[str, "CoverageStat"]

    def __post_init__(self) -> None:
        object.__setattr__(self, "errors", tuple(self.errors or ()))
        object.__setattr__(self, "payload_coverage", freeze_mapping(self.payload_coverage))


@dataclass(frozen=True, slots=True)
class CoverageStat:
    field: str
    covered: float          # 0..1 fraction
    sample_size: int
    passes: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.covered <= 1.0:
            raise ValueError(
                f"CoverageStat.covered must be in [0.0, 1.0], got {self.covered}"
            )
        if self.sample_size < 0:
            raise ValueError(
                f"CoverageStat.sample_size must be >= 0, got {self.sample_size}"
            )


@dataclass(frozen=True, slots=True)
class ReadinessDeps:
    catalog_port: CatalogReleasePort
    vector_port: VectorReleasePort
    graph_port: GraphReleasePort
    ranking_port: RankingProfilePort


def _coverage_passes(obs_vector, field, threshold):
    if obs_vector is None or not obs_vector.coverage:
        return True, 1.0
    for cov in obs_vector.coverage:
        if cov.field == field:
            return cov.covered >= threshold, cov.covered
    return True, 1.0


def _reconcile_samples(catalog, vector, graph) -> list[RecommendationError]:
    """Compare profile_hash + org_unit_ids per entity_id across sources.

    An entity_id present in only one source contributes no error (a sample
    may legitimately not have landed in Qdrant/Neo4j yet). Only entities
    present in >=2 sources are compared, and only conflicting values fail.
    None vs non-None on the same field is a mismatch; None vs None is fine.
    """
    by_source = {
        "catalog": {s.entity_id: s for s in (catalog or ())},
        "vector": {s.entity_id: s for s in (vector or ())},
        "graph": {s.entity_id: s for s in (graph or ())},
    }
    all_ids = set()
    for src in by_source.values():
        all_ids.update(src)
    errors: list[RecommendationError] = []
    for eid in sorted(all_ids):
        present = {name: by_source[name][eid] for name in by_source if eid in by_source[name]}
        if len(present) < 2:
            continue
        for field in ("profile_hash", "org_unit_ids"):
            values = {name: getattr(s, field) for name, s in present.items()}
            distinct = {v for v in values.values()}
            # None vs None collapses to one value; None vs value does not
            if len(distinct) > 1:
                names = sorted(values)
                detail = ", ".join(f"{n}={values[n]!r}" for n in names)
                errors.append(RecommendationError(
                    code=RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT,
                    severity=ErrorSeverity.ERROR,
                    message=f"sample {eid} {field} mismatch: {detail}",
                ))
    return errors


class ReadinessService:
    """Two-phase ACTIVE-build construction + consistency checks (R2 readiness).

    Phase 1 gathers catalog.read_active + ranking.read_version (independent);
    phase 2 gathers catalog.read_samples + vector.read_current + graph.read_active,
    seeded by phase-1 sample_entity_ids. An ``asyncio.Lock`` serializes concurrent
    checks so a late-finishing older check cannot overwrite a newer snapshot. On any
    ERROR-severity failure (or a missing snapshot) ``self._snapshot`` is preserved;
    it is replaced only on a fully-ready check.
    """

    _reconcile_samples = staticmethod(_reconcile_samples)

    def __init__(self, deps: ReadinessDeps, settings: RecommendSettings) -> None:
        self._deps = deps
        self._settings = settings
        self._snapshot: ActiveBuildSnapshot | None = None
        self._lock = asyncio.Lock()

    async def check(self) -> ReadinessReport:
        async with self._lock:
            return await self._check_locked()

    async def _check_locked(self) -> ReadinessReport:
        s = self._settings
        phase1 = await gather_safe(
            ReadbackCall(
                "catalog", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                self._deps.catalog_port.read_active(),
            ),
            ReadbackCall(
                "ranking", RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE,
                self._deps.ranking_port.read_version(s.ranking_profile_path),
            ),
            timeout=s.readiness_readback_timeout,
        )
        catalog_obs, ranking_version = phase1
        errors: list[RecommendationError] = [
            e for e in phase1 if isinstance(e, RecommendationError)
        ]
        catalog_ok = (
            not isinstance(catalog_obs, RecommendationError)
            and catalog_obs is not None
        )
        phase2_results: tuple = ()
        if catalog_ok:
            sample_ids = catalog_obs.sample_entity_ids
            phase2_results = await gather_safe(
                ReadbackCall(
                    "catalog-samples",
                    RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                    self._deps.catalog_port.read_samples(
                        catalog_obs.build_id, sample_ids,
                    ),
                ),
                ReadbackCall(
                    "vector", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                    self._deps.vector_port.read_current(s.qdrant_alias, sample_ids),
                ),
                ReadbackCall(
                    "graph", RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                    self._deps.graph_port.read_active(sample_ids),
                ),
                timeout=s.readiness_readback_timeout,
            )
            errors.extend(
                e for e in phase2_results if isinstance(e, RecommendationError)
            )

        ranking_ver = (
            ranking_version
            if not isinstance(ranking_version, RecommendationError)
            else None
        )
        new_snapshot, validation_errors, coverage = self._assemble(
            catalog_obs if catalog_ok else None,
            ranking_ver,
            phase2_results,
        )
        errors.extend(validation_errors)

        has_error = any(e.severity is ErrorSeverity.ERROR for e in errors)
        ready = not has_error and new_snapshot is not None
        if ready:
            self._snapshot = new_snapshot
        return ReadinessReport(
            ready=ready,
            snapshot=self._snapshot,
            errors=tuple(errors),
            payload_coverage=coverage,
        )

    def get_snapshot(self) -> ActiveBuildSnapshot | None:
        return self._snapshot

    def _assemble(
        self, catalog_obs, ranking_version, phase2_results,
    ) -> tuple[
        ActiveBuildSnapshot | None,
        list[RecommendationError],
        Mapping[str, CoverageStat],
    ]:
        errors: list[RecommendationError] = []
        coverage: dict[str, CoverageStat] = {}

        def _err(code, message, *, severity=ErrorSeverity.ERROR, retryable=False):
            errors.append(RecommendationError(
                code=code, severity=severity, message=message, retryable=retryable,
            ))

        if catalog_obs is None:
            _err(RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE, "no ACTIVE build")
            return None, errors, coverage

        catalog_samples, vector_obs, graph_obs = self._unpack_phase2(phase2_results)

        # A RecommendationError value for vector/graph means gather_safe
        # already collected the real timeout/exception detail into `errors`
        # (via _check_locked's errors.extend). Emit the generic
        # ACTIVE_BUILD_UNAVAILABLE ONLY for the genuine None (pointer missing)
        # case, then normalize both error and None to None for the build_ids
        # block so a RecommendationError can never leak into build_ids.add(...).
        if vector_obs is None:
            _err(
                RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                "vector alias/collection unavailable",
            )
            vector_obs = None
        elif isinstance(vector_obs, RecommendationError):
            vector_obs = None
        if graph_obs is None:
            _err(
                RecommendationErrorCode.ACTIVE_BUILD_UNAVAILABLE,
                "graph active pointer unavailable",
            )
            graph_obs = None
        elif isinstance(graph_obs, RecommendationError):
            graph_obs = None

        build_ids = {catalog_obs.build_id}
        if vector_obs is not None:
            build_ids.add(vector_obs.build_id)
        if graph_obs is not None:
            build_ids.add(graph_obs.build_id)
        if len(build_ids) > 1:
            _err(
                RecommendationErrorCode.ACTIVE_BUILD_INCONSISTENT,
                f"three-way build id mismatch: {sorted(build_ids)}",
            )

        if len(build_ids) == 1:
            errors.extend(self._reconcile_samples(
                catalog_samples,
                vector_obs.samples if vector_obs is not None else (),
                graph_obs.samples if graph_obs is not None else (),
            ))

        if vector_obs is not None:
            if vector_obs.embedding_dimension != catalog_obs.embedding_dimension:
                _err(
                    RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
                    "embedding dimension mismatch",
                )
            if vector_obs.embedding_fingerprint != catalog_obs.embedding_fingerprint:
                _err(
                    RecommendationErrorCode.EMBEDDING_FINGERPRINT_MISMATCH,
                    "embedding fingerprint mismatch",
                )

        if vector_obs is not None:
            s = self._settings
            for field, threshold, code in (
                ("org_unit_ids", s.coverage_threshold_org_unit_ids,
                 RecommendationErrorCode.ORG_UNIT_IDS_COVERAGE_INSUFFICIENT),
                ("profile_hash", s.coverage_threshold_profile_hash,
                 RecommendationErrorCode.PROFILE_HASH_COVERAGE_INSUFFICIENT),
                ("role_status", s.coverage_threshold_role_status,
                 RecommendationErrorCode.ROLE_STATUS_COVERAGE_INSUFFICIENT),
                ("eligibility", s.coverage_threshold_eligibility,
                 RecommendationErrorCode.ELIGIBILITY_COVERAGE_INSUFFICIENT),
            ):
                passes, covered = _coverage_passes(vector_obs, field, threshold)
                coverage[field] = CoverageStat(
                    field=field, covered=covered,
                    sample_size=vector_obs.point_count, passes=passes,
                )
                if not passes:
                    # org_unit_ids coverage failure is non-blocking: readiness
                    # stays ready and only flags the org_unit hard-filter
                    # capability as unavailable (original readiness spec §4 —
                    # the refuse-vs-degrade decision is deferred to R3 recommend
                    # core, not readiness). The other three coverage codes are
                    # ERROR-severity and do block ready.
                    severity = (
                        ErrorSeverity.WARNING
                        if field == "org_unit_ids"
                        else ErrorSeverity.ERROR
                    )
                    _err(code, f"{field} coverage {covered:.2f} < {threshold}",
                         severity=severity)
                    if field == "org_unit_ids":
                        _err(
                            RecommendationErrorCode.ORG_UNIT_FILTER_UNAVAILABLE,
                            "org_unit hard filter unavailable",
                            severity=ErrorSeverity.WARNING,
                        )

        if ranking_version is None or isinstance(ranking_version, RecommendationError):
            _err(
                RecommendationErrorCode.RANKING_PROFILE_UNAVAILABLE,
                "ranking profile unavailable",
            )
            ranking_ver = "unknown"
        else:
            ranking_ver = ranking_version

        has_error = any(e.severity is ErrorSeverity.ERROR for e in errors)
        if has_error:
            return None, errors, coverage

        snapshot = ActiveBuildSnapshot(
            build_id=catalog_obs.build_id,
            catalog_schema_version=catalog_obs.catalog_schema_version,
            neo4j_active_build_id=graph_obs.build_id if graph_obs else "",
            qdrant_alias_target=vector_obs.target_collection if vector_obs else "",
            qdrant_payload_schema_version=catalog_obs.qdrant_payload_schema_version,
            embedding_provider=catalog_obs.embedding_provider,
            embedding_model=catalog_obs.embedding_model,
            embedding_dimension=catalog_obs.embedding_dimension,
            embedding_fingerprint=catalog_obs.embedding_fingerprint,
            taxonomy_version=catalog_obs.taxonomy_version,
            ranking_profile_version=ranking_ver,
            created_at=catalog_obs.created_at,
        )
        return snapshot, errors, coverage

    @staticmethod
    def _unpack_phase2(phase2_results):
        # phase2 order: catalog_samples, vector, graph
        if not phase2_results:
            return (), None, None
        catalog_samples = phase2_results[0]
        vector_obs = phase2_results[1] if len(phase2_results) > 1 else None
        graph_obs = phase2_results[2] if len(phase2_results) > 2 else None
        if isinstance(catalog_samples, RecommendationError):
            catalog_samples = ()
        # Keep vector_obs / graph_obs error objects intact; _assemble checks
        # isinstance(..., RecommendationError) to distinguish "source raised /
        # timed out" (already collected into `errors` by _check_locked's
        # errors.extend) from "pointer genuinely missing" (None, needs a fresh
        # ACTIVE_BUILD_UNAVAILABLE). This avoids a duplicate _err() call when
        # gather_safe already recorded the real timeout/exception detail.
        return catalog_samples, vector_obs, graph_obs


__all__ = [
    "ActiveBuildSnapshot",
    "CoverageStat",
    "ReadinessDeps",
    "ReadinessReport",
    "ReadinessService",
]
