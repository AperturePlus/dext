"""dext_recommend — explainable mentor recommendation query layer.

Peer package to dext / dext_graph / dext_monitor. Read-only consumer of
published ACTIVE build artifacts (catalog SQLite, Qdrant current alias,
Neo4j active pointer). Never writes back, never triggers build/promote.

Imports the shared dext_grounded contract for StudentContext/SourceRef/
LLMGenerationPort, but never imports dext/dext_graph/dext_monitor/
dext_competition internals.
"""

import dext_grounded  # noqa: F401 — shared contract; asserted by import-boundary test

from dext_recommend.config import RecommendSettings
from dext_recommend.errors import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)
from dext_recommend.models import (
    ConversationContext, QueryDiagnostics, QueryUnderstanding, RecommendRequest,
    RecommendResponse, RecommendationFilters, RecommendationWarning,
    RecommendedProfessor,
)
from dext_recommend.ports import (
    AliasReadback, BuildSnapshotPort, EmbeddingResult, FakeBuildSnapshotPort,
    FakeLLMGenerationPort, FakeProfessorFactPort, FakeQueryEmbeddingPort,
    FakeVectorSearchPort, LLMGenerationPort, ProfessorDetail, ProfessorFact,
    ProfessorFactPort, QueryEmbeddingPort, VectorHit, VectorSearchPort,
    ViewerPermissions,
)
from dext_recommend.readiness import (
    ActiveBuildSnapshot, CoverageStat, ReadinessReport, ReadinessService,
)

__version__ = "0.1.0"

__all__: list[str] = [
    "ActiveBuildSnapshot",
    "AliasReadback",
    "BuildSnapshotPort",
    "ConversationContext",
    "CoverageStat",
    "EmbeddingResult",
    "ErrorSeverity",
    "FakeBuildSnapshotPort",
    "FakeLLMGenerationPort",
    "FakeProfessorFactPort",
    "FakeQueryEmbeddingPort",
    "FakeVectorSearchPort",
    "LLMGenerationPort",
    "ProfessorDetail",
    "ProfessorFact",
    "ProfessorFactPort",
    "QueryDiagnostics",
    "QueryEmbeddingPort",
    "QueryUnderstanding",
    "ReadinessReport",
    "ReadinessService",
    "RecommendRequest",
    "RecommendResponse",
    "RecommendSettings",
    "RecommendationError",
    "RecommendationErrorCode",
    "RecommendationFilters",
    "RecommendationWarning",
    "RecommendedProfessor",
    "VectorHit",
    "VectorSearchPort",
    "ViewerPermissions",
]
