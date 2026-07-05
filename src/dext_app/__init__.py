"""Application-level composition for Dext HTTP services."""
from __future__ import annotations

from dext_app.api import create_app, create_live_competition_http_deps

__all__ = ["create_app", "create_live_competition_http_deps"]
