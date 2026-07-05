"""Port protocols + fakes for dext_competition.

Re-exports the shared constrained-generation seam from dext_grounded so the
business layers (C3-C6) import one path. No business logic here — only the
read-only knowledge/catalog protocols, their fakes, and the profile seam.
"""
from __future__ import annotations

from dext_competition.ports.catalog import CompetitionCatalogPort
from dext_competition.ports.generation_profile import (
    GenerationProfile,
    ProfileRegistry,
)
from dext_competition.ports.knowledge import KnowledgeIndexPort
from dext_competition.ports._fakes import (
    FakeCompetitionCatalogPort,
    FakeKnowledgeIndexPort,
)
from dext_grounded import (
    ConstrainedGenerationPipeline,
    FakeLLMGenerationPort,
    LLMGenerationPort,
)

__all__ = [
    "CompetitionCatalogPort",
    "ConstrainedGenerationPipeline",
    "FakeCompetitionCatalogPort",
    "FakeKnowledgeIndexPort",
    "FakeLLMGenerationPort",
    "GenerationProfile",
    "KnowledgeIndexPort",
    "LLMGenerationPort",
    "ProfileRegistry",
]
