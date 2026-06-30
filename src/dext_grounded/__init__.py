"""Shared constrained-generation + fact-citation contract.

Peer-to-peer contract layer consumed by ``dext_recommend`` and (later)
``dext_competition``. Defines immutable data contracts and three pure-Python
protocols (LLMGenerationPort, CitationValidator, SafetyGuard) from the
grounded-generation spec. No I/O, no LLM client, no DB.

Both consumer modules import this package but never import each other.
"""

__version__ = "0.1.0"

from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.content import ContentClass
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext

__all__ = [
    "Claim",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "GenerationResult",
    "GenerationWarning",
    "QUOTE_MAX_LEN",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
]
