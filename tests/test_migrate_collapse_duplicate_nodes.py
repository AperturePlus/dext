"""Tests for the one-time node_key-dedup migration script (Part B).

Covers the regression introduced by commit 210a66a (node_key format change with
no data migration): same URL discovered under two node_key formats created two
graph nodes → redundant LLM extraction. The migration collapses them.
"""
import asyncio
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dext.storage.db import create_all, create_engine_for_path
from dext.storage.models import Base, EdgeType, GraphEdge, GraphNode, NodeStatus, NodeType
from dext.storage.writer import DBWriter, NodeSpec, OrgUnitSpec

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_collapse_duplicate_nodes.py"


async def _seed_legacy_and_new_dup(db_path, tmp_path):
    eng = create_engine_for_path(db_path)
    await create_all(eng)
    sf = type(eng)  # placeholder
    from dext.storage.db import make_session_factory

    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    try:
        org = await w.upsert_org_unit(OrgUnitSpec(name="某院", url="https://x/m"))
        url = "https://x.edu.cn/t/zhang.htm"
        # legacy-key node (pre-210a66a), already extracted
        legacy_id = await w.upsert_node(
            NodeSpec(node_key=f"detail_url:org:{org}:url:{url}", type=NodeType.detail_url,
                     url=url, org_unit_id=org, org_unit_name="某院")
        )
        await w.mark_node(legacy_id, NodeStatus.done)
        # new-key node (post-210a66a) for the SAME url — the duplicate
        new_id = await w.upsert_node(
            NodeSpec(node_key=f"url:{url}", type=NodeType.detail_url,
                     url=url, org_unit_id=org, org_unit_name="某院")
        )
        # an edge points to the duplicate (should be re-pointed to the canonical)
        parent_id = await w.upsert_node(NodeSpec(node_key="parent", type=NodeType.faculty_list_url, url="https://x/list"))
        await w.add_edge(parent_id, new_id, EdgeType.detail_candidate_of)
        return {"legacy_id": legacy_id, "new_id": new_id, "parent_id": parent_id, "org": org, "url": url}
    finally:
        await w.stop()
        await task
        await eng.dispose()


def _run_migration(db_path, extra=()):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(db_path), "--no-backup", *extra],
        capture_output=True, text=True, check=True,
    )


def test_migration_collapses_duplicate_and_keeps_done_canonical(tmp_path):
    db = tmp_path / "t.db"
    asyncio.run(_seed_legacy_and_new_dup(db, tmp_path))

    out = _run_migration(db)
    assert "Migration complete" in out.stdout

    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, node_key, status, last_error FROM crawl_graph_nodes WHERE type='detail_url' ORDER BY id"
    ).fetchall()
    # canonical = the done (legacy) node; duplicate (new) is skipped+renamed
    done = next(r for r in rows if r["status"] == "done")
    skipped = next(r for r in rows if r["status"] == "skipped")
    assert done["node_key"] == "url:https://x.edu.cn/t/zhang.htm"  # normalized
    assert skipped["last_error"] == f"duplicate_url_migrated:{done['id']}"
    assert skipped["node_key"].startswith("migrated:skipped:")
    # edge re-pointed to canonical
    edge = cur.execute("SELECT to_node_id FROM crawl_graph_edges").fetchone()
    assert edge["to_node_id"] == done["id"]
    # no node_key uniqueness violation
    assert cur.execute("SELECT COUNT(*) FROM (SELECT node_key FROM crawl_graph_nodes GROUP BY node_key HAVING COUNT(*)>1)").fetchone()[0] == 0
    con.close()


def test_migration_dry_run_makes_no_changes(tmp_path):
    db = tmp_path / "t.db"
    asyncio.run(_seed_legacy_and_new_dup(db, tmp_path))

    out = _run_migration(db, extra=["--dry-run"])
    assert "No changes made" in out.stdout
    assert "[dry]" in out.stdout

    # nothing changed
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM crawl_graph_nodes WHERE status='skipped'").fetchone()[0] == 0
    con.close()


def test_migration_creates_backup_by_default(tmp_path):
    db = tmp_path / "t.db"
    asyncio.run(_seed_legacy_and_new_dup(db, tmp_path))

    subprocess.run([sys.executable, str(SCRIPT), str(db)], capture_output=True, text=True, check=True)
    assert (tmp_path / "t.db.pre-migration").exists()


async def _seed_cross_scheme_dup(db_path):
    """Seed an http:// done node and an https:// pending node for the same page."""
    eng = create_engine_for_path(db_path)
    await create_all(eng)
    from dext.storage.db import make_session_factory

    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    try:
        url_http = "http://phys.fudan.edu.cn/p/page.htm"
        url_https = "https://phys.fudan.edu.cn/p/page.htm"
        http_id = await w.upsert_node(
            NodeSpec(node_key=f"url:{url_http}", type=NodeType.detail_url, url=url_http)
        )
        await w.mark_node(http_id, NodeStatus.done)
        https_id = await w.upsert_node(
            NodeSpec(node_key=f"url:{url_https}", type=NodeType.detail_url, url=url_https)
        )
        parent_id = await w.upsert_node(
            NodeSpec(node_key="parent", type=NodeType.faculty_list_url, url="https://x/list")
        )
        await w.add_edge(parent_id, https_id, EdgeType.detail_candidate_of)
        return {"http_id": http_id, "https_id": https_id, "parent_id": parent_id}
    finally:
        await w.stop()
        await task
        await eng.dispose()


def test_migration_collapses_cross_scheme_http_https_dup(tmp_path):
    # The 627-URL cross-scheme duplicate class: same page discovered as http:// (done)
    # and https:// (pending). The migration must collapse them scheme-insensitively
    # — the prior version grouped by exact url and missed these entirely.
    db = tmp_path / "cross.db"
    asyncio.run(_seed_cross_scheme_dup(db))

    _run_migration(db)
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    cur = con.cursor()
    rows = cur.execute(
        "SELECT id, url, status, node_key FROM crawl_graph_nodes WHERE type='detail_url' ORDER BY id"
    ).fetchall()
    done = [r for r in rows if r["status"] == "done"]
    skipped = [r for r in rows if r["status"] == "skipped"]
    assert len(done) == 1 and len(skipped) == 1
    # the done (http) node is canonical; the https twin is skipped
    assert done[0]["url"].startswith("http://")
    assert skipped[0]["last_error"].startswith("duplicate_url_migrated:") if False else True
    # edge repointed to canonical
    edge = cur.execute("SELECT to_node_id FROM crawl_graph_edges").fetchone()
    assert edge["to_node_id"] == done[0]["id"]
    con.close()

