"""Shared fixtures for live-LLM tests (overview §9, CLAUDE.md real-LLM policy).

No mocks/fakes are ever permitted for LLM behavior. When DEEPSEEK_API_KEY is
absent the live tests SKIP (the environment is unfit) — never downgrade to a fake.
"""

import socket

import pytest

from dext.config import get_settings


@pytest.fixture(scope="session")
def live_settings():
    get_settings.cache_clear()
    settings = get_settings()
    if not settings.deepseek_api_key:
        pytest.skip("DEEPSEEK_API_KEY not set; live LLM tests require a real key (no mocks allowed).")
    return settings


@pytest.fixture
def llm_client(live_settings):
    from dext.llm.client import LLMClient

    return LLMClient(live_settings)


@pytest.fixture(scope="session")
def live_http():
    """Enable real-HTTP tests. Skips (never fakes) when the network is out.

    No mocks/fakes are permitted for HTTP behavior — when the host is unreachable
    the environment is unfit and the test is skipped.
    """
    try:
        socket.getaddrinfo("bs.nankai.edu.cn", 443, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        pytest.skip("network unavailable; live-HTTP tests require a real connection (no mocks allowed).")
    return True
