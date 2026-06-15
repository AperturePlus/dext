"""dext LLM subsystem — DeepSeek V4 client, decider, extractor, sanitizer, retry.

Layer purity (CLAUDE.md): imports only dext.types and dext.page; no DB/bridge,
no network beyond the openai SDK. Public interface (spec §9) re-exported below.
"""

from dext.llm.client import LLMClient, LLMResponse
from dext.llm.decider import (
    DecidedLink,
    Decision,
    DeciderContext,
    DeciderNode,
    decide_links,
)
from dext.llm.extractor import ExtractionResult, OrgUnitContext, extract_professors
from dext.llm.prompts import (
    PROMPT_HASHES,
    SAVE_PROFESSORS_TOOL,
    build_decider_messages,
    build_extractor_messages,
    prompt_hash,
    truncate_to_budget,
)
from dext.llm.retry import NoDataVerdict, assess_no_data
from dext.llm.sanitizer import sanitize

__all__ = [
    "LLMClient",
    "LLMResponse",
    "decide_links",
    "Decision",
    "DecidedLink",
    "DeciderNode",
    "DeciderContext",
    "extract_professors",
    "ExtractionResult",
    "OrgUnitContext",
    "sanitize",
    "assess_no_data",
    "NoDataVerdict",
    "build_decider_messages",
    "build_extractor_messages",
    "SAVE_PROFESSORS_TOOL",
    "prompt_hash",
    "PROMPT_HASHES",
    "truncate_to_budget",
]
