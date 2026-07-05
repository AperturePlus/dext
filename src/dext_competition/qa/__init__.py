"""C4 detail, grounded QA and competition comparison."""
from dext_competition.qa.answer import answer_competition_question, support_map_validator
from dext_competition.qa.compare import compare_competitions
from dext_competition.qa.detail import get_competition_detail
from dext_competition.qa.schemas import (
    ANSWER_JSON_SCHEMA,
    ANSWER_SYSTEM_PROMPT_ID,
    COMPARE_JSON_SCHEMA,
    COMPARE_SYSTEM_PROMPT_ID,
    FRESHNESS_NOTICE,
    QA_GENERATION_PROFILE_VERSION,
)

__all__ = [
    "ANSWER_JSON_SCHEMA",
    "ANSWER_SYSTEM_PROMPT_ID",
    "COMPARE_JSON_SCHEMA",
    "COMPARE_SYSTEM_PROMPT_ID",
    "FRESHNESS_NOTICE",
    "QA_GENERATION_PROFILE_VERSION",
    "answer_competition_question",
    "compare_competitions",
    "get_competition_detail",
    "support_map_validator",
]

