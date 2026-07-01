"""dext_recommend — explainable mentor recommendation query layer.

Peer package to dext / dext_graph / dext_monitor. Read-only consumer of
published ACTIVE build artifacts (catalog SQLite, Qdrant current alias,
Neo4j active pointer). Never writes back, never triggers build/promote.

Imports the shared dext_grounded contract for StudentContext/SourceRef/
LLMGenerationPort, but never imports dext/dext_graph/dext_monitor/
dext_competition internals.
"""

import dext_grounded  # noqa: F401 — shared contract; asserted by import-boundary test
from dext_grounded import SourceRef, StudentContext

from dext_recommend.config import RecommendSettings
from dext_recommend.core import RecommendDeps, RecommendationCore
from dext_recommend.errors import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)
from dext_recommend.models import (
    ConversationContext, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendResponse, RecommendationFilters, RecommendationWarning,
    RecommendedProfessor,
)
from dext_recommend.ports import (
    ActiveSnapshotProvider, AliasReadback, CatalogReleaseObservation,
    CatalogReleasePort, EmbeddingResult, FakeActiveSnapshotProvider,
    FakeCatalogReleasePort, FakeGraphReleasePort, FakeLLMGenerationPort,
    FakeProfessorFactPort, FakeQueryEmbeddingPort, FakeRankingProfilePort,
    FakeVectorReleasePort, FakeVectorSearchPort, GraphReleaseObservation,
    GraphReleasePort, LLMGenerationPort, PayloadCoverageObservation,
    ProfessorDetail, ProfessorFact, ProfessorFactPort, ProfessorReleaseSample,
    QueryEmbeddingPort, RankingProfile, RankingProfilePort, ReadinessSourceError,
    VectorHit, VectorReleaseObservation, VectorReleasePort, VectorSearchPort,
    ViewerPermissions,
)
from dext_recommend.readiness import (
    ActiveBuildSnapshot, CoverageStat, ReadinessReport, ReadinessService,
)

__version__ = "0.1.0"

__all__: list[str] = [
    "ActiveBuildSnapshot",
    "ActiveSnapshotProvider",
    "AliasReadback",
    "CatalogReleaseObservation",
    "CatalogReleasePort",
    "ConversationContext",
    "CoverageStat",
    "EmbeddingResult",
    "ErrorSeverity",
    "FakeActiveSnapshotProvider",
    "FakeCatalogReleasePort",
    "FakeGraphReleasePort",
    "FakeLLMGenerationPort",
    "FakeProfessorFactPort",
    "FakeQueryEmbeddingPort",
    "FakeRankingProfilePort",
    "FakeVectorReleasePort",
    "FakeVectorSearchPort",
    "GraphReleaseObservation",
    "GraphReleasePort",
    "LLMGenerationPort",
    "PayloadCoverageObservation",
    "ProfessorDetail",
    "ProfessorFact",
    "ProfessorFactPort",
    "ProfessorReleaseSample",
    "QueryDiagnostics",
    "QueryEmbeddingPort",
    "QueryUnderstanding",
    "RankingProfile",
    "RankingProfilePort",
    "ReadinessReport",
    "ReadinessService",
    "ReadinessSourceError",
    "RecommendDeps",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendSettings",
    "RecommendationCore",
    "RecommendationError",
    "RecommendationErrorCode",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
    "SourceRef",
    "StudentContext",
    "VectorHit",
    "VectorReleaseObservation",
    "VectorReleasePort",
    "VectorSearchPort",
    "ViewerPermissions",
]
