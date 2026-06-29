import json
import sqlite3

import pytest

from dext_graph.catalog.gold import evaluate_curation_gold
from dext_graph.catalog.curation import _assign_observation
from dext_graph.catalog.normalization import (
    normalize_email,
    normalize_name,
    normalize_text,
    normalize_url,
    split_multivalue,
)
from dext_graph.catalog.overrides import merge_entities, record_override
from dext_graph.catalog.rules import classify_role, load_curation_rules
from dext_graph.catalog.workflow import create_build
from test_catalog_workflow import _patch_runtime, _settings, _source_db


def _rows(path, query, params=()):
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, params)]


def test_normalization_is_versioned_and_conservative():
    assert normalize_text("  张  三\n") == "张 三"
    assert normalize_name("张·三") == "张三"
    assert normalize_email("User@EXAMPLE.EDU.CN") == "User@example.edu.cn"
    assert normalize_email("shared office") is None
    assert normalize_url("HTTPS://EXAMPLE.EDU:443/p/?b=2&a=1#x") == "https://example.edu/p?a=1&b=2"
    assert split_multivalue("图学习；机器学习、图学习,药物发现") == (
        "图学习",
        "机器学习",
        "图学习,药物发现",
    )


def test_role_rules_cover_supervisor_exclusion_and_review():
    rules = load_curation_rules()
    lecturer = classify_role("讲师", ["硕士生导师"], [], rules)
    assert lecturer.role_status == "included"
    assert lecturer.master_eligibility == "confirmed"
    excluded = classify_role("工程师", [], ["实验技术队伍"], rules)
    assert excluded.role_status == "excluded"
    technical = classify_role("实验师", [], [], rules)
    assert technical.role_status == "review"
    conflict = classify_role("讲师", ["博导", "非博导"], [], rules)
    assert conflict.phd_eligibility == "conflict"
    negative_only = classify_role("讲师", ["非博导"], [], rules)
    assert negative_only.phd_eligibility == "unknown"


@pytest.mark.asyncio
async def test_build_curates_canonical_professors_and_status(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    _source_db(settings.source_data_dir / "test.db", count=3)
    result = await create_build(["测试大学"], settings)
    assert result["build"]["status"] == "EMBEDDING"
    assert result["curation"]["status"] == "COMPLETED"
    canonical = _rows(
        settings.catalog_path,
        "SELECT * FROM canonical_professors WHERE build_id=? ORDER BY name",
        (result["build"]["id"],),
    )
    assert len(canonical) == 3
    assert {row["role_status"] for row in canonical} == {"included"}
    assert {row["phd_eligibility"] for row in canonical} == {"confirmed"}
    assert {row["completeness"] for row in canonical} == {1.0}
    assert all(row["active"] == 1 for row in canonical)


@pytest.mark.asyncio
async def test_same_org_name_with_different_urls_creates_review(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE professors SET name='同名教师' WHERE id IN (1,2)")
    result = await create_build(["测试大学"], settings)
    assignments = _rows(
        settings.catalog_path,
        "SELECT DISTINCT entity_id FROM entity_observations WHERE build_id=?",
        (result["build"]["id"],),
    )
    assert len(assignments) == 2
    findings = _rows(
        settings.catalog_path,
        "SELECT code FROM quality_findings WHERE build_id=? AND code='identity_conflict'",
        (result["build"]["id"],),
    )
    assert len(findings) == 1
    canonical = _rows(
        settings.catalog_path,
        "SELECT role_status, role_reason_codes FROM canonical_professors WHERE build_id=?",
        (result["build"]["id"],),
    )
    assert any(row["role_status"] == "review" for row in canonical)


@pytest.mark.asyncio
async def test_orcid_is_strong_identity_but_email_is_not(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE professors SET external_link='https://orcid.org/0000-0002-1825-0097', "
            "email=CASE id WHEN 1 THEN 'a@example.edu' ELSE 'b@example.edu' END"
        )
    result = await create_build(["测试大学"], settings)
    entities = _rows(
        settings.catalog_path,
        "SELECT DISTINCT entity_id FROM entity_observations WHERE build_id=?",
        (result["build"]["id"],),
    )
    assert len(entities) == 1


@pytest.mark.asyncio
async def test_shared_email_alone_does_not_merge_entities(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute("INSERT INTO org_units VALUES (2, '数学学院', 'completed')")
        connection.execute("UPDATE professors SET email='office@example.edu.cn'")
        connection.execute("UPDATE professors SET org_unit_name='数学学院' WHERE id=2")
        connection.execute("UPDATE professor_affiliations SET org_unit_id=2 WHERE professor_id=2")
    result = await create_build(["测试大学"], settings)
    entities = _rows(
        settings.catalog_path,
        "SELECT DISTINCT entity_id FROM entity_observations WHERE build_id=?",
        (result["build"]["id"],),
    )
    assert len(entities) == 2


@pytest.mark.asyncio
async def test_strong_claim_collision_creates_review_entity(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=2)
    result = await create_build(["测试大学"], settings)
    build_id = result["build"]["id"]
    rules = load_curation_rules()
    with sqlite3.connect(settings.catalog_path) as connection:
        connection.row_factory = sqlite3.Row
        assigned = list(
            connection.execute(
                "SELECT entity_id, observation_id FROM entity_observations "
                "WHERE build_id=? ORDER BY observation_id",
                (build_id,),
            )
        )
        snapshot_id = connection.execute(
            "SELECT source_snapshot_id FROM professor_observations LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO identity_claims(entity_id,claim_type,claim_value,strength,observation_id,active,created_at) "
            "VALUES (?, 'profile_name_url', 'https://collision.example.edu/p|collision', "
            "'strong', ?, 1, '2026-01-01T00:00:00+00:00')",
            (assigned[0]["entity_id"], assigned[0]["observation_id"]),
        )
        connection.execute(
            "INSERT INTO identity_claims(entity_id,claim_type,claim_value,strength,observation_id,active,created_at) "
            "VALUES (?, 'external_identity', 'orcid:0000-0002-1825-0097', "
            "'strong', ?, 1, '2026-01-01T00:00:00+00:00')",
            (assigned[1]["entity_id"], assigned[1]["observation_id"]),
        )
        payload = json.dumps(
            {"name": "Collision", "external_link": "https://orcid.org/0000-0002-1825-0097"}
        )
        connection.execute(
            """
            INSERT INTO professor_observations(
              id,university_id,source_snapshot_id,source_professor_id,source_url,
              source_page_kind,extraction_batch_size,source_content_hash,source_document_id,
              org_unit_source_id,name_raw,name_key,payload_json,row_hash,provenance_grade,
              first_seen_build,last_seen_build,active
            ) VALUES ('collision-observation','univ:test',?,999,'https://collision.example.edu/p',
              'single_profile',1,NULL,NULL,1,'Collision','collision',?,'collision-row',
              'legacy_merged',?,?,1)
            """,
            (snapshot_id, payload, build_id, build_id),
        )
        row = dict(
            connection.execute(
                "SELECT * FROM professor_observations WHERE id='collision-observation'"
            ).fetchone()
        )
        assert _assign_observation(connection, build_id, row, rules) is True
        review = connection.execute(
            "SELECT e.status FROM entity_observations eo JOIN entities e ON e.id=eo.entity_id "
            "WHERE eo.build_id=? AND eo.observation_id='collision-observation'",
            (build_id,),
        ).fetchone()
        assert review[0] == "review"
        assert connection.execute(
            "SELECT COUNT(*) FROM quality_findings WHERE build_id=? AND code='strong_claim_collision'",
            (build_id,),
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_changed_observation_reuses_persistent_identity(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=1)
    first = await create_build(["测试大学"], settings)
    first_entity = _rows(
        settings.catalog_path,
        "SELECT entity_id FROM entity_observations WHERE build_id=?",
        (first["build"]["id"],),
    )[0]["entity_id"]
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE professors SET title='特聘教授' WHERE id=1")
    second = await create_build(["测试大学"], settings)
    second_entity = _rows(
        settings.catalog_path,
        "SELECT entity_id FROM entity_observations WHERE build_id=?",
        (second["build"]["id"],),
    )[0]["entity_id"]
    assert second_entity == first_entity
    assert _rows(
        settings.catalog_path,
        "SELECT title_raw FROM canonical_professors WHERE build_id=? AND active=1",
        (second["build"]["id"],),
    )[0]["title_raw"] == "特聘教授"


@pytest.mark.asyncio
async def test_field_override_and_manual_merge_apply_on_next_build(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path, batch=1)
    source = settings.source_data_dir / "test.db"
    _source_db(source, count=2)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE professors SET name='同名教师' WHERE id IN (1,2)")
    first = await create_build(["测试大学"], settings)
    entities = [
        row["entity_id"]
        for row in _rows(
            settings.catalog_path,
            "SELECT DISTINCT entity_id FROM entity_observations WHERE build_id=? ORDER BY entity_id",
            (first["build"]["id"],),
        )
    ]
    target, source_entity = entities
    record_override(
        settings.catalog_path,
        entity_id=target,
        kind="field",
        field_name="title",
        value="首席教授",
        actor="tester",
        reason="gold correction",
    )
    merge_entities(
        settings.catalog_path,
        source_entity_id=source_entity,
        target_entity_id=target,
        actor="tester",
        reason="same person confirmed",
    )
    second = await create_build(["测试大学"], settings)
    canonical = _rows(
        settings.catalog_path,
        "SELECT entity_id, title_raw, active FROM canonical_professors WHERE build_id=? ORDER BY active DESC",
        (second["build"]["id"],),
    )
    assert sum(row["active"] for row in canonical) == 1
    active = next(row for row in canonical if row["active"])
    assert active["entity_id"] == target
    assert active["title_raw"] == "首席教授"
    assert any(not row["active"] for row in canonical)


@pytest.mark.asyncio
async def test_gold_evaluator_uses_observation_grouping(tmp_path, monkeypatch):
    _patch_runtime(monkeypatch)
    settings = _settings(tmp_path)
    _source_db(settings.source_data_dir / "test.db", count=2)
    result = await create_build(["测试大学"], settings)
    observations = _rows(
        settings.catalog_path,
        "SELECT id FROM professor_observations WHERE active=1 ORDER BY id",
    )
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        "".join(
            json.dumps(
                {"observation_id": row["id"], "person_key": f"p{index}", "role_status": "included"},
                ensure_ascii=False,
            )
            + "\n"
            for index, row in enumerate(observations)
        ),
        encoding="utf-8",
    )
    metrics = evaluate_curation_gold(settings.catalog_path, result["build"]["id"], gold)
    assert metrics["status"] == "evaluated"
    assert metrics["auto_merge_pairwise_precision"] == 1.0
    assert metrics["excluded_precision"] == 1.0
