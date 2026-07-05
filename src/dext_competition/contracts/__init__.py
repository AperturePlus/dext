"""dext_competition contracts — immutable domain DTOs grouped by phase.

Phase mapping (competition assistant overview §9):
  knowledge  <- C1 knowledge index chunks + manifest (spec §4)
  catalog    <- C2 competition cards (spec §3)
  recommend  <- C3 recommend request/response/warnings (overview §3)
  qa         <- C4 detail / grounded answer / comparison (overview §3)
  plan       <- C5 preparation plan draft + level diagnosis (overview §6)
  assistant  <- C6 plan change card schemas (overview §6)

Every model is frozen + slot-based with tuple-typed collection fields, matching
the deep-immutability rule of the shared dext_grounded contract (spec §3).
``SourceRef`` is reused from dext_grounded — never redefined here.
"""
from __future__ import annotations

from dext_competition.contracts.assistant import (
    AssistantHistoryTurn,
    CardResult,
    PlanAssistantRequest,
    PlanAssistantResult,
    PlanAssistantServiceResult,
    PlanChangeCard,
    PlanChangeSet,
    PreparationNewTaskDraft,
    PreparationPhaseScheduleDraft,
    collapse_change_card_status,
)
from dext_competition.contracts.catalog import (
    CatalogEvidenceStatus,
    CompetitionCard,
    CompetitionCatalogManifest,
    CompetitionCategory,
    CompetitionFieldEvidence,
)
from dext_competition.contracts.knowledge import (
    Chunk,
    KnowledgeBaseManifest,
    KnowledgeHit,
)
from dext_competition.contracts.plan import (
    LevelDiagnosis,
    PlanGenerationResult,
    PlanPhase,
    PlanTask,
    PreparationPlanDraft,
)
from dext_competition.contracts.qa import (
    CompetitionComparison,
    CompetitionDetail,
    GroundedAnswer,
)
from dext_competition.contracts.recommend import (
    CompetitionPreferences,
    CompetitionQueryUnderstanding,
    CompetitionRecommendRequest,
    CompetitionRecommendResponse,
    CompetitionWarning,
    RecommendedCompetition,
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
    "CompetitionPreferences",
    "CompetitionQueryUnderstanding",
    "CompetitionRecommendRequest",
    "CompetitionRecommendResponse",
    "CompetitionWarning",
    "GroundedAnswer",
    "KnowledgeBaseManifest",
    "KnowledgeHit",
    "LevelDiagnosis",
    "AssistantHistoryTurn",
    "CardResult",
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
    "collapse_change_card_status",
]
