"""Live generation-profile port backed by the checked-in JSON artifact."""
from __future__ import annotations

import asyncio
from pathlib import Path

from dext_recommend.core.generation_profile import RecommendGenerationProfile


class LiveGenerationProfileAdapter:
    async def read_profile(self, path: Path) -> RecommendGenerationProfile:
        return await asyncio.to_thread(RecommendGenerationProfile.from_file, Path(path))


__all__ = ["LiveGenerationProfileAdapter"]
