"""C3 competition recommend core."""
from dext_competition.recommend.explanation import (
    GroundedRecommendationReason,
    assemble_recommended_competition,
    build_grounded_reasons,
)
from dext_competition.recommend.generation_profile import (
    QueryUnderstandingProfile,
    load_query_understanding_profile,
)
from dext_competition.recommend.filters import apply_filters
from dext_competition.recommend.profile import (
    CompetitionRankingProfile,
    load_ranking_profile,
)
from dext_competition.recommend.query_understanding import (
    QUERY_UNDERSTANDING_SCHEMA,
    heuristic_understanding,
    understand_query,
)
from dext_competition.recommend.ranking import RankedCompetition, rank_candidates
from dext_competition.recommend.recall import CompetitionCandidate, recall_candidates
from dext_competition.recommend.service import (
    CompetitionRecommendDeps,
    CompetitionRecommendationService,
    validate_request,
)

__all__ = [
    "CompetitionCandidate",
    "CompetitionRankingProfile",
    "CompetitionRecommendDeps",
    "CompetitionRecommendationService",
    "GroundedRecommendationReason",
    "QUERY_UNDERSTANDING_SCHEMA",
    "RankedCompetition",
    "apply_filters",
    "assemble_recommended_competition",
    "build_grounded_reasons",
    "heuristic_understanding",
    "load_ranking_profile",
    "load_query_understanding_profile",
    "rank_candidates",
    "recall_candidates",
    "understand_query",
    "validate_request",
]
