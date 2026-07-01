"""RankingProfileAdapter: reads the version string from a JSON profile file."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dext_recommend.ports.release_readback import ReadinessSourceError


class RankingProfileAdapter:
    async def read_version(self, path: Path) -> str:
        p = Path(path)
        def _read() -> str:
            if not p.is_file():
                raise ReadinessSourceError("ranking", f"profile not found: {p}")
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ReadinessSourceError("ranking", "profile is not valid JSON") from exc
            version = data.get("version")
            if not isinstance(version, str) or not version:
                raise ReadinessSourceError("ranking", "profile missing version string")
            return version
        return await asyncio.to_thread(_read)


__all__ = ["RankingProfileAdapter"]
