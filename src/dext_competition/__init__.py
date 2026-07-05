"""dext_competition — competition assistant backend (overview spec).

Peer module to ``dext_recommend``. v1 read-only consumes the local Markdown
knowledge base at ``data/竞赛助手/``; it does NOT import dext / dext_graph /
dext_monitor / dext_recommend, and does NOT depend on the mentor ACTIVE build,
Neo4j or the Qdrant mentor collection.

The shared constrained-generation seam (raw LLMGenerationPort -> support-map ->
CitationValidator -> SafetyGuard) is owned by ``dext_grounded`` and is
re-exported from ``dext_competition.ports``; business layers MUST call
``ConstrainedGenerationPipeline``, never the raw port, and never re-run
citation/safety outside it.
"""
from __future__ import annotations

__version__ = "0.1.0"

from dext_competition.config import CompetitionSettings
from dext_competition.errors import (
    CompetitionError,
    CompetitionErrorCode,
    ErrorSeverity,
)
from dext_competition.recommend import (
    CompetitionRankingProfile,
    CompetitionRecommendDeps,
    CompetitionRecommendationService,
)
from dext_competition.assistant import PlanAssistantDeps, suggest_plan_changes
from dext_competition.contracts import (
    AssistantHistoryTurn,
    CatalogEvidenceStatus,
    CardResult,
    Chunk,
    CompetitionCard,
    CompetitionCatalogManifest,
    CompetitionCategory,
    CompetitionFieldEvidence,
    CompetitionComparison,
    CompetitionDetail,
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
    CompetitionRecommendRequest,
    CompetitionRecommendResponse,
    CompetitionWarning,
    GroundedAnswer,
    KnowledgeBaseManifest,
    KnowledgeHit,
    LevelDiagnosis,
    PlanAssistantRequest,
    PlanAssistantResult,
    PlanAssistantServiceResult,
    PlanChangeCard,
    PlanChangeSet,
    PlanGenerationResult,
    PlanPhase,
    PlanTask,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
    PreparationPlanDraft,
    RecommendedCompetition,
    collapse_change_card_status,
)
# Shared seam re-exported (single owner: dext_grounded). Business layers import
# these from dext_competition.ports — re-exported here for convenience.
from dext_grounded import (
    ConstrainedGenerationPipeline,
    FactBundle,
    FactItem,
    FakeLLMGenerationPort,
    LLMGenerationPort,
    SourceRef,
    StudentContext,
)

__all__ = [
    "CatalogEvidenceStatus",
    "Chunk",
    "CompetitionCard",
    "CompetitionCatalogManifest",
    "CompetitionCategory",
    "CompetitionFieldEvidence",
    "CompetitionComparison",
    "CompetitionDetail",
    "CompetitionError",
    "CompetitionErrorCode",
    "CompetitionPreferences",
    "CompetitionQueryUnderstanding",
    "CompetitionRecommendRequest",
    "CompetitionRecommendResponse",
    "CompetitionRecommendDeps",
    "CompetitionRecommendationService",
    "CompetitionRankingProfile",
    "CompetitionSettings",
    "CompetitionWarning",
    "ConstrainedGenerationPipeline",
    "ErrorSeverity",
    "FactBundle",
    "FactItem",
    "FakeLLMGenerationPort",
    "GroundedAnswer",
    "KnowledgeBaseManifest",
    "KnowledgeHit",
    "LevelDiagnosis",
    "LLMGenerationPort",
    "AssistantHistoryTurn",
    "CardResult",
    "PlanAssistantDeps",
    "PlanAssistantRequest",
    "PlanAssistantResult",
    "PlanAssistantServiceResult",
    "PlanChangeCard",
    "PlanChangeSet",
    "PlanGenerationResult",
    "PlanPhase",
    "PlanTask",
    "PreparationNewTaskDraft",
    "PreparationPhaseScheduleDraft",
    "PreparationPlanDraft",
    "RecommendedCompetition",
    "SourceRef",
    "StudentContext",
    "collapse_change_card_status",
    "suggest_plan_changes",
]
