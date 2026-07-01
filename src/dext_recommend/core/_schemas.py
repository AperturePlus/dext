# src/dext_recommend/core/_schemas.py
"""Fixed JSON schemas for constrained LLM generation."""
from __future__ import annotations

QUERY_UNDERSTANDING_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "research_interests", "preferred_universities", "preferred_cities",
        "preferred_org_units", "degree_goal",
        "mentor_eligibility_requirement", "missing_information",
        "needs_clarification", "confidence",
    ],
    "properties": {
        "research_interests": {"type": "array", "items": {"type": "string"}},
        "preferred_universities": {"type": "array", "items": {"type": "string"}},
        "preferred_cities": {"type": "array", "items": {"type": "string"}},
        "preferred_org_units": {"type": "array", "items": {"type": "string"}},
        "degree_goal": {"type": ["string", "null"]},
        "mentor_eligibility_requirement": {"type": ["string", "null"]},
        "missing_information": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
}

__all__ = ["QUERY_UNDERSTANDING_SCHEMA"]
