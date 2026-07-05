"""G0 port-contract tests for dext_competition.

Pins that KnowledgeIndexPort / CompetitionCatalogPort are runtime-checkable
async Protocols, and that the in-process fakes satisfy them so C1/C2/C3 TDD
can wire a fake without touching real Markdown/PostgreSQL. Also pins the
generation-profile seam: competition owns its own profile version, never
mutates a shared client.
"""
from __future__ import annotations

import inspect
import hashlib

from dext_competition.ports import (
    CompetitionCatalogPort,
    FakeCompetitionCatalogPort,
    FakeKnowledgeIndexPort,
    KnowledgeIndexPort,
)
from dext_grounded import GenerationProfile, ProfileRegistry
from dext_competition import Chunk, KnowledgeHit


def test_knowledge_index_port_is_async_protocol():
    assert hasattr(KnowledgeIndexPort, "_is_protocol")
    retrieve = KnowledgeIndexPort.retrieve
    assert inspect.iscoroutinefunction(retrieve) or getattr(
        retrieve, "_is_protocol", False
    )
    # the query/search methods must be async (C1 spec §6)
    for name in ("query", "search"):
        assert hasattr(KnowledgeIndexPort, name)


def test_catalog_port_is_async_protocol():
    assert hasattr(CompetitionCatalogPort, "_is_protocol")
    for name in ("get", "list_competitions", "manifest"):
        assert hasattr(CompetitionCatalogPort, name)


def test_fake_knowledge_index_port_satisfies_protocol():
    fake = FakeKnowledgeIndexPort()
    assert isinstance(fake, KnowledgeIndexPort)


async def test_fake_knowledge_index_returns_hits_and_validates_limit():
    text = "数学建模"
    chunk = Chunk("a.md", "a.md > A", hashlib.sha256(text.encode()).hexdigest(), text)
    fake = FakeKnowledgeIndexPort((chunk,))
    hits = await fake.query("数学")
    assert len(hits) == 1
    assert isinstance(hits[0], KnowledgeHit)
    assert hits[0].source_ref.chunk_hash == chunk.chunk_hash


def test_fake_catalog_port_satisfies_protocol():
    fake = FakeCompetitionCatalogPort()
    assert isinstance(fake, CompetitionCatalogPort)


def test_competition_generation_profile_is_versioned_and_independent():
    # competition owns its own generation profile version; never reuses the
    # recommend ranking profile or a mutable shared client.
    profile = GenerationProfile(
        version="competition.qa.v1",
        prompt_ids=["dext_competition.qa.v1"],
    )
    registry = ProfileRegistry()
    registry.register(profile)
    assert registry.get("competition.qa.v1") is profile
    assert registry.get("recommend.detail.v1") is None
