"""RankingProfileAdapter: reads the version string from a JSON profile file."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dext_recommend.core.ranking_profile import RankingProfile
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

    async def read_profile(self, path: Path) -> RankingProfile:
        p = Path(path)

        def _read() -> RankingProfile:
            try:
                raw = p.read_text(encoding="utf-8")
            except OSError as exc:
                raise ReadinessSourceError("ranking", f"profile read failed: {exc}") from exc
            except UnicodeError as exc:
                raise ReadinessSourceError("ranking", "profile not valid UTF-8") from exc
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ReadinessSourceError("ranking", "profile is not valid JSON") from exc
            if not isinstance(data, dict):
                raise ReadinessSourceError("ranking", "profile root must be an object")
            try:
                return RankingProfile.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                raise ReadinessSourceError("ranking", f"invalid profile: {exc}") from exc

        return await asyncio.to_thread(_read)


__all__ = ["RankingProfileAdapter"]
