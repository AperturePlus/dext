"""Persistent catalog foundation for graph builds."""

from dext_graph.catalog.models import CATALOG_SCHEMA_VERSION
from dext_graph.catalog.curation import curate_build
from dext_graph.catalog.gold import evaluate_curation_gold
from dext_graph.catalog.graph_gold import evaluate_graph_gold, generate_graph_gold
from dext_graph.catalog.overrides import merge_entities, record_override
from dext_graph.catalog.vector_workflow import vector_build

__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "curate_build",
    "evaluate_curation_gold",
    "evaluate_graph_gold",
    "generate_graph_gold",
    "merge_entities",
    "record_override",
    "vector_build",
]
