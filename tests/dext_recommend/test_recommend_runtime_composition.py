from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from dext_recommend import (
    LiveClients, RecommendSettings, RecommendationRuntimeError,
    build_live_recommendation_runtime,
)


SCHEMA = """
CREATE TABLE graph_builds (
    id TEXT PRIMARY KEY, status TEXT NOT NULL,
    curation_version TEXT NOT NULL, taxonomy_version TEXT,
    graph_schema_version INTEGER NOT NULL, vector_schema_version INTEGER NOT NULL,
    embedding_provider TEXT, embedding_base_url TEXT, embedding_model TEXT,
    embedding_fingerprint TEXT, embedding_dimension INTEGER,
    settings_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT, finished_at TEXT, last_error TEXT
);
CREATE TABLE canonical_professors (
    entity_id TEXT, build_id TEXT, name TEXT, title_raw TEXT,
    title_family TEXT, role_status TEXT, role_reason_codes TEXT,
    master_eligibility TEXT, phd_eligibility TEXT, research_areas_text TEXT,
    bio TEXT, email TEXT, phone TEXT, profile_url TEXT, external_url TEXT,
    active INTEGER, completeness REAL, PRIMARY KEY (entity_id, build_id)
);
CREATE TABLE professor_profiles (
    build_id TEXT, entity_id TEXT, profile_hash TEXT, template_version TEXT,
    tokenizer_identity TEXT, normalized_profile TEXT, token_count INTEGER,
    payload_json TEXT, created_at TEXT, PRIMARY KEY (build_id, entity_id)
);
"""


def build_catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO graph_builds(id,status,curation_version,taxonomy_version,"
            "graph_schema_version,vector_schema_version,embedding_provider,"
            "embedding_model,embedding_fingerprint,embedding_dimension,settings_json,"
            "finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("b1", "ACTIVE", "c1", "t1", 6, 2, "sf", "bge-m3", "fp-1", 3,
             "{}", "2026-07-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO canonical_professors(entity_id,build_id,name,title_family,"
            "role_status,role_reason_codes,master_eligibility,phd_eligibility,active,"
            "completeness) VALUES(?,?,?,?,?,?,?,?,1,1.0)",
            ("e1", "b1", "Name", "professor", "included", "[]", "confirmed", "unknown"),
        )
        conn.execute(
            "INSERT INTO professor_profiles(build_id,entity_id,profile_hash,template_version,"
            "tokenizer_identity,normalized_profile,token_count,payload_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("b1", "e1", "h1", "tv", "ti", "profile", 1, "{}", "2026-07-01"),
        )
        conn.commit()
    return path


class Closable:
    def __init__(self, name, events):
        self.name = name
        self.events = events

    async def close(self):
        self.events.append(self.name)


class Alias:
    alias_name = "dext_professors_current"
    collection_name = "dext_professors__b1"


class Point:
    id = "e1"
    payload = {
        "build_id": "b1",
        "profile_hash": "h1",
        "role_status": "included",
        "master_eligibility": "confirmed",
        "phd_eligibility": "unknown",
        "embedding_fingerprint": "fp-1",
        "org_unit_ids": [],
    }


class FakeQdrant:
    def __init__(self, events):
        self.events = events
        self.network_calls = 0

    async def get_aliases(self):
        self.network_calls += 1
        return type("Aliases", (), {"aliases": [Alias()]})()

    async def get_collection(self, *, collection_name):
        self.network_calls += 1
        dense = type("Dense", (), {"size": 3})()
        params = type("Params", (), {"vectors": {"dense": dense}})()
        config = type("Config", (), {"params": params})()
        return type("Collection", (), {"config": config})()

    async def count(self, *, collection_name, exact=True):
        self.network_calls += 1
        return type("Count", (), {"count": 1})()

    async def retrieve(self, **kwargs):
        self.network_calls += 1
        return [Point()]

    def close(self):
        self.events.append("qdrant")


class AsyncRows:
    def __init__(self, rows):
        self.rows = iter(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.rows)
        except StopIteration:
            raise StopAsyncIteration


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def run(self, query, **kwargs):
        if "GraphState" in query:
            return AsyncRows([{"build_id": "b1"}])
        return AsyncRows([{
            "entity_id": "e1", "profile_hash": "h1", "role_status": "included",
            "master_eligibility": "confirmed", "phd_eligibility": "unknown",
            "embedding_fingerprint": "fp-1", "org_unit_ids": [],
        }])


class FakeNeo4j:
    def __init__(self, events):
        self.events = events
        self.network_calls = 0

    async def verify_connectivity(self):
        self.network_calls += 1

    def session(self):
        self.network_calls += 1
        return Session()

    async def close(self):
        self.events.append("neo4j")


def clients(events):
    return LiveClients(
        openai_embedding=Closable("embedding", events),
        qdrant_client=FakeQdrant(events),
        neo4j_driver=FakeNeo4j(events),
        openai_llm_core=Closable("llm_core", events),
        openai_llm_aux=Closable("llm_aux", events),
    )


def settings(catalog_path: Path, **overrides):
    values = {
        "catalog_path": catalog_path,
        "embedding_provider": "sf",
        "embedding_model": "bge-m3",
        "runtime_refresh_interval": 3600,
        "readiness_sample_size": 1,
    }
    values.update(overrides)
    return RecommendSettings(**values)


async def test_live_runtime_composes_all_services_and_closes_in_reverse_order(tmp_path):
    events = []
    injected = clients(events)
    runtime = await build_live_recommendation_runtime(
        settings(build_catalog(tmp_path)), clients=injected,
    )
    assert runtime.core.deps.snapshot_port.get_snapshot().build_id == "b1"
    assert runtime.conversation.core is runtime.core
    assert runtime.auxiliary_generation._core is runtime.core
    assert runtime.generation_profile.version
    assert runtime.readiness._refresh_task is not None
    await runtime.aclose()
    await runtime.aclose()
    assert events == ["llm_aux", "llm_core", "embedding", "qdrant", "neo4j"]


async def test_readiness_failure_closes_all_injected_clients(tmp_path):
    events = []
    injected = clients(events)
    with pytest.raises(RecommendationRuntimeError) as raised:
        await build_live_recommendation_runtime(
            settings(tmp_path / "missing.db"), clients=injected,
        )
    assert raised.value.code == "readiness_failed"
    assert raised.value.retryable is True
    assert events == ["llm_aux", "llm_core", "embedding", "qdrant", "neo4j"]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"embedding_provider": "other"}, "embedding_provider_mismatch"),
        ({"embedding_model": "other"}, "embedding_model_mismatch"),
    ],
)
async def test_runtime_rejects_embedding_config_mismatch_and_closes(
    tmp_path, overrides, code,
):
    events = []
    with pytest.raises(RecommendationRuntimeError) as raised:
        await build_live_recommendation_runtime(
            settings(build_catalog(tmp_path), **overrides),
            clients=clients(events),
        )
    assert raised.value.code == code
    assert raised.value.retryable is False
    assert events == ["llm_aux", "llm_core", "embedding", "qdrant", "neo4j"]


async def test_manifest_mismatch_fails_before_any_client_or_network_use(tmp_path):
    raw = json.loads(Path("data/recommend/generation-profile.json").read_text("utf-8"))
    raw["grounded_rules_manifest_hash"] = "stale"
    profile_path = tmp_path / "generation.json"
    profile_path.write_text(json.dumps(raw), encoding="utf-8")
    events = []
    injected = clients(events)
    with pytest.raises(RecommendationRuntimeError) as raised:
        await build_live_recommendation_runtime(
            settings(
                tmp_path / "missing.db", generation_profile_path=profile_path,
            ),
            clients=injected,
        )
    assert raised.value.code == "generation_profile_manifest_mismatch"
    assert injected.qdrant_client.network_calls == 0
    assert injected.neo4j_driver.network_calls == 0
    assert events == []


async def test_missing_generation_profile_has_structured_nonretryable_error(tmp_path):
    with pytest.raises(RecommendationRuntimeError) as raised:
        await build_live_recommendation_runtime(
            settings(
                tmp_path / "missing.db",
                generation_profile_path=tmp_path / "missing-profile.json",
            ),
            clients=clients([]),
        )
    assert raised.value.code == "generation_profile_unavailable"
    assert raised.value.retryable is False
