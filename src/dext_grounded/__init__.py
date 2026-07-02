"""Shared constrained-generation + fact-citation contract.

Peer-to-peer contract layer consumed by ``dext_recommend`` and (later)
``dext_competition``. Defines immutable data contracts and three pure-Python
protocols (LLMGenerationPort, CitationValidator, SafetyGuard) from the
grounded-generation spec. No I/O, no LLM client, no DB.

Both consumer modules import this package but never import each other.
"""

__version__ = "0.1.0"

from dext_grounded.citation import CitationValidator
from dext_grounded.pipeline import ConstrainedGenerationPipeline
from dext_grounded.claims import Claim, GenerationResult, GenerationWarning
from dext_grounded.codes import GenerationWarningCode
from dext_grounded.content import ContentClass
from dext_grounded.eval import AcceptanceSample, describe_metrics, load_acceptance_samples
from dext_grounded.fact_bundle import FactBundle, FactItem
from dext_grounded.ports import FakeLLMGenerationPort, LLMGenerationPort
from dext_grounded.profile import GenerationProfile, ProfileRegistry
from dext_grounded.rules import GroundedRules, load_grounded_rules
from dext_grounded.safety import SafetyGuard
from dext_grounded.source_ref import QUOTE_MAX_LEN, SourceRef, UserContextRef
from dext_grounded.student_context import StudentContext
from dext_grounded.trim import trim

__all__ = [
    "AcceptanceSample",
    "CitationValidator",
    "Claim",
    "ConstrainedGenerationPipeline",
    "ContentClass",
    "FactBundle",
    "FactItem",
    "FakeLLMGenerationPort",
    "GenerationProfile",
    "GenerationResult",
    "GenerationWarning",
    "GenerationWarningCode",
    "GroundedRules",
    "LLMGenerationPort",
    "ProfileRegistry",
    "QUOTE_MAX_LEN",
    "SafetyGuard",
    "SourceRef",
    "StudentContext",
    "UserContextRef",
    "describe_metrics",
    "load_acceptance_samples",
    "load_grounded_rules",
    "trim",
]
