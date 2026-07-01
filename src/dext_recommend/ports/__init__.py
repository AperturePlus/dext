"""Port protocols (interfaces to published build artifacts)."""
from dext_recommend.ports.build_snapshot import BuildSnapshotPort
from dext_recommend.ports.embedding import EmbeddingResult, QueryEmbeddingPort
from dext_recommend.ports.generation import FakeLLMGenerationPort, LLMGenerationPort
from dext_recommend.ports.professor_facts import (
    ProfessorDetail, ProfessorFact, ProfessorFactPort, ViewerPermissions,
)
from dext_recommend.ports.vector_search import AliasReadback, VectorHit, VectorSearchPort

__all__ = [
    "AliasReadback",
    "BuildSnapshotPort",
    "EmbeddingResult",
    "FakeLLMGenerationPort",
    "LLMGenerationPort",
    "ProfessorDetail",
    "ProfessorFact",
    "ProfessorFactPort",
    "QueryEmbeddingPort",
    "VectorHit",
    "VectorSearchPort",
    "ViewerPermissions",
]
