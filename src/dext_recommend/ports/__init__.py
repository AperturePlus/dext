"""Port protocols for validated snapshots and published artifact readback."""
from dext_recommend.core.generation_profile import (
    OperationConfig, RecommendGenerationProfile,
)
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.ports.active_snapshot import ActiveSnapshotProvider
from dext_recommend.ports.embedding import EmbeddingResult, QueryEmbeddingPort
from dext_recommend.ports.generation import FakeLLMGenerationPort, LLMGenerationPort
from dext_recommend.ports.generation_profile import RecommendGenerationProfilePort
from dext_recommend.ports.conversation_store import ConversationStorePort, TurnSnapshot
from dext_recommend.ports.professor_facts import (
    ProfessorDetail,
    ProfessorFact,
    ProfessorFactNotFound,
    ProfessorFactPort,
    ViewerPermissions,
)
from dext_recommend.ports.release_readback import (
    CatalogReleaseObservation,
    CatalogReleasePort,
    GraphReleaseObservation,
    GraphReleasePort,
    PayloadCoverageObservation,
    ProfessorReleaseSample,
    RankingProfilePort,
    ReadinessSourceError,
    VectorReleaseObservation,
    VectorReleasePort,
)
from dext_recommend.ports.vector_search import AliasReadback, VectorHit, VectorSearchPort
from dext_recommend.ports._fakes import (
    FakeActiveSnapshotProvider,
    FakeCatalogReleasePort,
    FakeConversationStorePort,
    FakeGraphReleasePort,
    FakeProfessorFactPort,
    FakeQueryEmbeddingPort,
    FakeRankingProfilePort,
    FakeRecommendGenerationProfilePort,
    FakeVectorReleasePort,
    FakeVectorSearchPort,
)

__all__ = [
    "ActiveSnapshotProvider",
    "AliasReadback",
    "CatalogReleaseObservation",
    "CatalogReleasePort",
    "ConversationStorePort",
    "EmbeddingResult",
    "FakeActiveSnapshotProvider",
    "FakeCatalogReleasePort",
    "FakeConversationStorePort",
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
    "QueryEmbeddingPort",
    "RankingProfile",
    "RankingProfilePort",
    "ReadinessSourceError",
    "RecommendGenerationProfile",
    "RecommendGenerationProfilePort",
    "TurnSnapshot",
    "VectorHit",
    "VectorReleaseObservation",
    "VectorReleasePort",
    "VectorSearchPort",
    "ViewerPermissions",
]
