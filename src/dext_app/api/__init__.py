"""Combined aiohttp API app for the Flutter-facing service."""
from __future__ import annotations

from dext_app.api.app import create_app, create_live_competition_http_deps

__all__ = ["create_app", "create_live_competition_http_deps"]
