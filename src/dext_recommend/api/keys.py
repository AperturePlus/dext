"""Typed aiohttp application keys."""
from __future__ import annotations

from typing import Any

from aiohttp import web

from dext_recommend.api.settings import AppSettings
from dext_recommend.app_state.repositories import AppStateRepository
from dext_recommend.app_state.conversation_store import PostgresConversationStore

SETTINGS_KEY = web.AppKey("recommend_settings", AppSettings)
REQUEST_ID_KEY = web.RequestKey("recommend_request_id", str)
REPOSITORY_KEY = web.AppKey("recommend_repository", AppStateRepository)
CONVERSATION_STORE_KEY = web.AppKey("recommend_conversation_store", PostgresConversationStore)
RUNTIME_KEY = web.AppKey("recommend_runtime", Any)
ATTEMPT_REGISTRY_KEY = web.AppKey("recommend_attempt_registry", Any)
APPLICATION_SERVICES_KEY = web.AppKey("recommend_application_services", Any)

__all__ = [
    "ATTEMPT_REGISTRY_KEY",
    "APPLICATION_SERVICES_KEY",
    "CONVERSATION_STORE_KEY",
    "REPOSITORY_KEY",
    "REQUEST_ID_KEY",
    "RUNTIME_KEY",
    "SETTINGS_KEY",
]
