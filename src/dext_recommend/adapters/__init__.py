"""Concrete read-only adapter implementations for published ACTIVE build artifacts.

Each adapter composes a dialect-seam reader (SQLite/Qdrant/Neo4j) with the
dialect-agnostic mapping layer. Adapters never import dext_graph.
"""
from dext_recommend.adapters.catalog_release import CatalogReleaseAdapter
from dext_recommend.adapters.graph_release import GraphReleaseAdapter
from dext_recommend.adapters.ranking_profile import RankingProfileAdapter
from dext_recommend.adapters.vector_release import VectorReleaseAdapter

__all__ = [
    "CatalogReleaseAdapter",
    "GraphReleaseAdapter",
    "RankingProfileAdapter",
    "VectorReleaseAdapter",
]
