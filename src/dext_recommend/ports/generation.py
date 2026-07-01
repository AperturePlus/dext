"""Re-export the shared LLMGenerationPort (foundations §5, grounded §4)."""
from __future__ import annotations

from dext_grounded import FakeLLMGenerationPort, LLMGenerationPort

__all__ = ["FakeLLMGenerationPort", "LLMGenerationPort"]
