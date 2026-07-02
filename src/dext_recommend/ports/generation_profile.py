"""RecommendGenerationProfilePort — loads the checked-in generation profile."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from dext_recommend.core.generation_profile import RecommendGenerationProfile


@runtime_checkable
class RecommendGenerationProfilePort(Protocol):
    async def read_profile(self, path: Path) -> RecommendGenerationProfile: ...


__all__ = ["RecommendGenerationProfilePort"]
