"""Port protocols for validated snapshots and published artifact readback."""
from dext_recommend.core.ranking_profile import RankingProfile
from dext_recommend.ports.active_snapshot import ActiveSnapshotProvider
from dext_recommend.ports.embedding import EmbeddingResult, QueryEmbeddingPort
from dext_recommend.ports.generation import FakeLLMGenerationPort, LLMGenerationPort
from dext_recommend.ports.professor_facts import (
    ProfessorDetail,
    ProfessorFact,
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
    FakeGraphReleasePort,
    FakeProfessorFactPort,
    FakeQueryEmbeddingPort,
    FakeRankingProfilePort,
    FakeVectorReleasePort,
    FakeVectorSearchPort,
)

__all__ = [
    "ActiveSnapshotProvider",
    "AliasReadback",
    "CatalogReleaseObservation",
    "CatalogReleasePort",
    "EmbeddingResult",
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
    "QueryEmbeddingPort",
    "RankingProfile",
    "RankingProfilePort",
    "ReadinessSourceError",
    "VectorHit",
    "VectorReleaseObservation",
    "VectorReleasePort",
    "VectorSearchPort",
    "ViewerPermissions",
]
