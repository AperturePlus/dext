"""Generation-profile adapters."""
from __future__ import annotations

import asyncio
from pathlib import Path

from dext_recommend.core.generation_profile import RecommendGenerationProfile


class LiveGenerationProfileAdapter:
    async def read_profile(self, path: Path) -> RecommendGenerationProfile:
        return await asyncio.to_thread(RecommendGenerationProfile.from_file, Path(path))


class StaticGenerationProfileAdapter:
    """Return the already-validated runtime generation profile."""

    def __init__(self, profile: RecommendGenerationProfile) -> None:
        self._profile = profile

    async def read_profile(self, path: Path) -> RecommendGenerationProfile:
        return self._profile


__all__ = ["LiveGenerationProfileAdapter", "StaticGenerationProfileAdapter"]
