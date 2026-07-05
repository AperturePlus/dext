"""C6 preparation-plan assistant.

The assistant suggests validated change cards only. It does not persist user
state and never applies the cards to a plan snapshot.
"""
from dext_competition.assistant.change_cards import cards_from_generation_output
from dext_competition.assistant.profile import (
    ASSISTANT_GENERATION_PROFILE_VERSION,
    DEFAULT_ASSISTANT_PROFILE_PATH,
    AssistantGenerationProfile,
    load_assistant_generation_profile,
)
from dext_competition.assistant.service import (
    PlanAssistantDeps,
    suggest_plan_changes,
)
from dext_competition.assistant.validation import (
    reject_card,
    validate_change_card,
    validate_change_cards,
)

__all__ = [
    "ASSISTANT_GENERATION_PROFILE_VERSION",
    "AssistantGenerationProfile",
    "DEFAULT_ASSISTANT_PROFILE_PATH",
    "PlanAssistantDeps",
    "cards_from_generation_output",
    "load_assistant_generation_profile",
    "reject_card",
    "suggest_plan_changes",
    "validate_change_card",
    "validate_change_cards",
]
