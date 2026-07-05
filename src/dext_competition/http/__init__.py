"""HTTP adapter package for C7 competition endpoints."""
from __future__ import annotations

from dext_competition.http.app import CompetitionHttpDeps, create_app, setup_routes
from dext_competition.http.routes import routes

__all__ = ["CompetitionHttpDeps", "create_app", "routes", "setup_routes"]
