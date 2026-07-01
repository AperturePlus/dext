import hashlib
import sqlite3

import pytest

from dext_graph.catalog.db import initialize_catalog
from dext_graph.catalog.semantic import (
    CacheVectorCorruption,
    build_semantic_profile,
    dense_vector_from_blob,
    dense_vector_to_blob,
    load_embedding_cache,
    mark_embedding_job,
    sparse_bm25_vector,
    store_embedding_cache,
    vector_checksum,
)


class CharacterTokenizer:
    identity = "character-v1"

    def encode(self, text, *, add_special_tokens=False):
        values = [ord(char) for char in text]
        return ([1] + values + [2]) if add_special_tokens else values

    def decode(self, token_ids):
        return "".join(chr(value) for value in token_ids if value > 2)


def test_canonical_semantic_profile_excludes_sensitive_fields_and_skips_empty_topics():
    profile = build_semantic_profile(
        {
            "entity_id": "entity-1",
            "university": "测试大学",
            "org_units": ["计算机学院", "人工智能学院"],
            "title": "教授",
            "name": "不应进入向量的姓名",
            "email": "secret@example.edu.cn",
            "phone": "123456",
        },
        research_statements=["图神经网络", "药物发现"],
        publication_mentions=["代表论文 A. 2024"],
        approved_topics=[],
        bio="个人简介",
        template_name="baseline-v1",
        tokenizer=CharacterTokenizer(),
        max_tokens=400,
    )

    assert profile.entity_id == "entity-1"
    assert profile.normalized_profile.startswith("学校：测试大学")
    assert "学院：计算机学院、人工智能学院" in profile.normalized_profile
    assert "研究方向原文：图神经网络\n药物发现" in profile.normalized_profile
    assert "代表成果：代表论文 A. 2024" in profile.normalized_profile
    assert "规范主题" not in profile.normalized_profile
    assert "不应进入向量的姓名" not in profile.normalized_profile
    assert "secret@example.edu.cn" not in profile.normalized_profile
    assert "123456" not in profile.normalized_profile
    assert profile.profile_hash == hashlib.sha256(
        f"baseline-v1\n{profile.normalized_profile}".encode("utf-8")
    ).hexdigest()


def test_dense_blob_roundtrip_checksum_and_corruption_detection():
    vector = [1.0, -2.5, 3.25]
    blob = dense_vector_to_blob(vector)
    checksum = vector_checksum(vector, {"indices": [1], "values": [0.5]})
    assert dense_vector_from_blob(blob, dimension=3, checksum=hashlib.sha256(blob).hexdigest()) == pytest.approx(vector)
    with pytest.raises(CacheVectorCorruption):
        dense_vector_from_blob(blob[:-1], dimension=3, checksum=checksum)


def test_sparse_bm25_vector_is_deterministic_and_versioned():
    first = sparse_bm25_vector("图学习 图学习 药物发现", tokenizer_version="bm25-test-v1")
    second = sparse_bm25_vector("图学习 图学习 药物发现", tokenizer_version="bm25-test-v1")
    changed = sparse_bm25_vector("图学习 图学习 药物发现", tokenizer_version="bm25-test-v2")
    assert first == second
    assert first["indices"] == sorted(first["indices"])
    assert len(first["indices"]) == len(first["values"])
    assert first != changed


def test_vector_schema_tables_enforce_job_status(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO embedding_jobs(build_id, entity_id, profile_hash, status, updated_at) "
                "VALUES ('missing-build', 'entity', 'hash', 'skipped', 'now')"
            )


def _insert_build(connection: sqlite3.Connection, build_id: str = "build-1") -> None:
    connection.execute(
        """
        INSERT INTO graph_builds(
          id,status,curation_version,graph_schema_version,vector_schema_version,
          settings_json,summary_json
        ) VALUES (?, 'WRITING_VECTOR', 'curation-v1', 1, 1, '{}', '{}')
        """,
        (build_id,),
    )


def test_embedding_cache_roundtrip_and_corruption_detection(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    with sqlite3.connect(path) as connection:
        dense = [1.0, 0.5, -0.25]
        sparse = {"indices": [3, 9], "values": [0.75, 1.25]}
        store_embedding_cache(
            connection,
            profile_hash="profile-a",
            embedding_fingerprint="fingerprint-a",
            dense_vector=dense,
            sparse_vector=sparse,
        )
        cached = load_embedding_cache(
            connection,
            profile_hash="profile-a",
            embedding_fingerprint="fingerprint-a",
            dimension=3,
        )
        assert cached is not None
        assert cached.dense == pytest.approx(dense)
        assert cached.sparse == sparse

        connection.execute(
            "UPDATE embedding_cache SET dense_blob=? WHERE profile_hash='profile-a'",
            (b"broken",),
        )
        with pytest.raises(CacheVectorCorruption):
            load_embedding_cache(
                connection,
                profile_hash="profile-a",
                embedding_fingerprint="fingerprint-a",
                dimension=3,
            )


def test_embedding_job_status_transitions_are_idempotent(tmp_path):
    path = initialize_catalog(tmp_path / "catalog.db")
    with sqlite3.connect(path) as connection:
        _insert_build(connection)
        mark_embedding_job(
            connection,
            build_id="build-1",
            entity_id="entity-1",
            profile_hash="profile-a",
            status="pending",
        )
        mark_embedding_job(
            connection,
            build_id="build-1",
            entity_id="entity-1",
            profile_hash="profile-a",
            status="retry",
            increment_attempt=True,
            last_error="timeout",
        )
        row = connection.execute(
            "SELECT status, attempt_count, last_error FROM embedding_jobs "
            "WHERE build_id='build-1' AND entity_id='entity-1'"
        ).fetchone()
        assert row == ("retry", 1, "timeout")
        mark_embedding_job(
            connection,
            build_id="build-1",
            entity_id="entity-1",
            profile_hash="profile-a",
            status="succeeded",
            vector_checksum="checksum-a",
        )
        row = connection.execute(
            "SELECT status, attempt_count, vector_checksum, last_error FROM embedding_jobs "
            "WHERE build_id='build-1' AND entity_id='entity-1'"
        ).fetchone()
        assert row == ("succeeded", 1, "checksum-a", None)
