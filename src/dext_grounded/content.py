"""Output content classification (grounded-generation spec §2.3).

Every assertion (Claim) is independently classified; mixed fact/advice/uncertain
output is NOT collapsed into a single global content_class.
"""
from __future__ import annotations

from enum import Enum


class ContentClass(str, Enum):
    FACT = "fact"          # from the fact bundle or an official link
    ADVICE = "advice"      # process/methodology + user-context advice
    UNCERTAIN = "uncertain"  # needs current-year notice or school-file re-check


__all__ = ["ContentClass"]
