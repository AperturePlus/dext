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
    ConversationContext, ConversationDispatchResult, ConversationSummary,
    DetailFollowupResponse, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendResponse, RecommendationFilters, RecommendationWarning,
    RecommendedProfessor,
)
from dext_recommend.ports import (
    ActiveSnapshotProvider, AliasReadback, CatalogReleaseObservation,
    CatalogReleasePort, EmbeddingResult, FakeActiveSnapshotProvider,
    FakeCatalogReleasePort, FakeGraphReleasePort, FakeLLMGenerationPort,
    FakeProfessorFactPort, FakeQueryEmbeddingPort, FakeRankingProfilePort,
    FakeRecommendGenerationProfilePort, FakeVectorReleasePort, FakeVectorSearchPort,
    GraphReleaseObservation, GraphReleasePort, LLMGenerationPort, OperationConfig,
    PayloadCoverageObservation, ProfessorDetail, ProfessorFact, ProfessorFactNotFound,
    ProfessorFactPort, ProfessorReleaseSample, QueryEmbeddingPort, RankingProfile,
    RankingProfilePort, ReadinessSourceError, RecommendGenerationProfile,
    RecommendGenerationProfilePort, VectorHit, VectorReleaseObservation,
    VectorReleasePort, VectorSearchPort, ViewerPermissions,
)
from dext_recommend.readiness import (
    ActiveBuildSnapshot, CoverageStat, ReadinessReport, ReadinessService,
)
from dext_recommend.adapters import (
    CatalogProfessorFactAdapter, CatalogProfessorFactReader,
    CatalogSqliteFactReader,
)

__version__ = "0.1.0"

__all__: list[str] = [
    "ActiveBuildSnapshot",
    "ActiveSnapshotProvider",
    "AliasReadback",
    "CatalogProfessorFactAdapter",
    "CatalogProfessorFactReader",
    "CatalogReleaseObservation",
    "CatalogReleasePort",
    "CatalogSqliteFactReader",
    "ConversationContext",
    "ConversationDispatchResult",
    "ConversationSummary",
    "CoverageStat",
    "DetailFollowupResponse",
    "EmbeddingResult",
    "ErrorSeverity",
    "FakeActiveSnapshotProvider",
    "FakeCatalogReleasePort",
    "FakeGraphReleasePort",
    "FakeLLMGenerationPort",
    "FakeProfessorFactPort",
    "FakeQueryEmbeddingPort",
    "FakeRankingProfilePort",
    "FakeRecommendGenerationProfilePort",
    "FakeVectorReleasePort",
    "FakeVectorSearchPort",
    "GraphReleaseObservation",
    "GraphReleasePort",
    "LLMGenerationPort",
    "OperationConfig",
    "PayloadCoverageObservation",
    "ProfessorDetail",
    "ProfessorFact",
    "ProfessorFactNotFound",
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
    "RecommendGenerationProfile",
    "RecommendGenerationProfilePort",
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
