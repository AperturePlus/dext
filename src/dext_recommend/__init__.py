"""dext_recommend — explainable mentor recommendation query layer.

Peer package to dext / dext_graph / dext_monitor. Read-only consumer of
published ACTIVE build artifacts (catalog SQLite, Qdrant current alias,
Neo4j active pointer). Never writes back, never triggers build/promote.

Imports the shared dext_grounded contract for StudentContext/SourceRef/
LLMGenerationPort, but never imports dext/dext_graph/dext_monitor/
dext_competition internals.
"""

import dext_grounded  # noqa: F401 — shared contract; asserted by import-boundary test

from dext_recommend.config import RecommendSettings
from dext_recommend.errors import (
    ErrorSeverity, RecommendationError, RecommendationErrorCode,
)

__version__ = "0.1.0"

__all__: list[str] = [
    "ErrorSeverity",
    "RecommendSettings",
    "RecommendationError",
    "RecommendationErrorCode",
]
