"""Cross-subproject shared value objects (overview §5).

Enums are DEFINED in dext.storage.models and re-exported here so other layers
(SP3–SP7) import them without depending on the ORM module directly.
ProfessorPayload is the contract SP5's extractor/sanitizer fills and SP2's
save_professors consumes; SP5 may add fields but must not rename these.
"""

from __future__ import annotations

from dataclasses import dataclass

from dext.storage.models import EdgeType, NodeStatus, NodeType

__all__ = ["NodeType", "EdgeType", "NodeStatus", "ProfessorPayload"]


@dataclass
class ProfessorPayload:
    """One extracted professor record (pre-dedup). All fields except name optional."""

    name: str
    title: str | None = None
    research_areas: str | None = None  # multi-value joined by "；"
    email: str | None = None
    phone: str | None = None
    homepage: str | None = None  # on-campus profile URL
    external_link: str | None = None  # external/3rd-party homepage
    bio: str | None = None
    enrollment_pref: str | None = None  # 博导/硕导 etc.
    publications: str | None = None
