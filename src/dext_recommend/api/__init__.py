"""aiohttp API surface for the recommendation system."""

from dext_recommend.api.app import create_recommendation_app
from dext_recommend.api.settings import AppSettings

__all__ = ["AppSettings", "create_recommendation_app"]
