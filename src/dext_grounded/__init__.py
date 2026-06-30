"""Shared constrained-generation + fact-citation contract.

Peer-to-peer contract layer consumed by ``dext_recommend`` and (later)
``dext_competition``. Defines immutable data contracts and three pure-Python
protocols (LLMGenerationPort, CitationValidator, SafetyGuard) from the
grounded-generation spec. No I/O, no LLM client, no DB.

Both consumer modules import this package but never import each other.
"""

__version__ = "0.1.0"

from dext_grounded.content import ContentClass

__all__ = ["ContentClass"]
