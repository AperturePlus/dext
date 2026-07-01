"""Shared fixtures for live-LLM tests (overview §9, CLAUDE.md real-LLM policy).

No mocks/fakes are ever permitted for LLM behavior. Live LLM tests are opt-in
because they are slow and network-dependent; setting DEEPSEEK_API_KEY alone is
not enough to include them in the default test run.
"""

import os

import pytest

from dext.config import get_settings


@pytest.fixture(scope="session")
def live_settings():
    if os.getenv("DEXT_RUN_LIVE_LLM") != "1":
        pytest.skip("live LLM tests are opt-in; set DEXT_RUN_LIVE_LLM=1 to run them.")
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.deepseek_api_key:
        pytest.skip("DEEPSEEK_API_KEY not set; live LLM tests require a real key (no mocks allowed).")
    return settings


@pytest.fixture
def llm_client(live_settings):
    from dext.llm.client import LLMClient

    return LLMClient(live_settings)
