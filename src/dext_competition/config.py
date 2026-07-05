"""Competition settings (mirrors dext_recommend.config surface).

G0 only fixes the knob surface; tuning happens per-phase as C1-C7 wire in.
All knobs have safe defaults so import-boundary tests run with zero env.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompetitionSettings:
    knowledge_source_root: str = "data/竞赛助手/"
    index_artifact_dir: str = "data/competition/index/"
    catalog_artifact_dir: str = "data/competition/catalog/"
    ranking_profile_version: str = "competition.ranking.v1"
    default_limit: int = 6
    bm25_top_k: int = 20
    fact_read_timeout: float = 5.0
    chunk_size: int = 1


__all__ = ["CompetitionSettings"]
