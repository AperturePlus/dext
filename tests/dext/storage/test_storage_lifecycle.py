import pytest
from sqlalchemy import select

from dext.config import Settings
from dext.seed import UniversitySeed
from dext.storage.db import StorageError
from dext.storage.lifecycle import open_fresh, open_resume, resolve_db_path
from dext.storage.models import CrawlRun, GraphNode, NodeStatus, NodeType, UniversityMeta
from dext.storage.writer import NodeSpec


def _university():
    return UniversitySeed(
        name="北京航空航天大学", url="https://www.buaa.edu.cn/",
        location="北京", org_unit_listing_urls=["https://www.buaa.edu.cn/list"],
    )


def _settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "universities")


def test_resolve_db_path(tmp_path):
    s = _settings(tmp_path)
    assert resolve_db_path("buaa", s) == (tmp_path / "universities" / "buaa.db")


async def test_open_fresh_creates_db_and_meta_row(tmp_path):
    s = _settings(tmp_path)
    handle = await open_fresh(_university(), "buaa", s)
    try:
        assert handle.backup_path is None
        assert resolve_db_path("buaa", s).exists()
        async with handle.session() as sess:
            meta = (await sess.execute(select(UniversityMeta))).scalar_one()
            assert meta.name == "北京航空航天大学"
            assert meta.abbr == "buaa"
            assert meta.start_url == "https://www.buaa.edu.cn/"
            assert meta.schema_version == 1
    finally:
        await handle.close()


async def test_open_fresh_backs_up_existing_db_then_rebuilds(tmp_path):
    s = _settings(tmp_path)
    h1 = await open_fresh(_university(), "buaa", s)
    await h1.writer.upsert_node(NodeSpec(node_key="old", type=NodeType.detail_url, url="https://x/old"))
    await h1.close()

    h2 = await open_fresh(_university(), "buaa", s)
    try:
        backups = list((s.data_dir / "backup").glob("*-buaa/buaa.db"))
        assert len(backups) == 1  # old DB copied to backup/<ts>-buaa/
        assert h2.backup_path == backups[0].parent
        async with h2.session() as sess:
            assert (await sess.execute(select(GraphNode))).scalars().all() == []  # rebuilt empty
    finally:
        await h2.close()


async def test_open_resume_missing_db_raises(tmp_path):
    s = _settings(tmp_path)
    with pytest.raises(StorageError):
        await open_resume(_university(), "buaa", s)


async def test_open_resume_resets_in_progress_to_retry_keeps_done(tmp_path):
    s = _settings(tmp_path)
    h1 = await open_fresh(_university(), "buaa", s)
    inprog = await h1.writer.upsert_node(NodeSpec(node_key="ip", type=NodeType.detail_url, url="https://x/ip"))
    finished = await h1.writer.upsert_node(NodeSpec(node_key="dn", type=NodeType.detail_url, url="https://x/dn"))
    await h1.writer.mark_node(inprog, NodeStatus.in_progress)
    await h1.writer.mark_node(finished, NodeStatus.done)
    await h1.close()

    h2 = await open_resume(_university(), "buaa", s)
    try:
        assert h2.backup_path is None
        async with h2.session() as sess:
            ip = (await sess.execute(select(GraphNode).where(GraphNode.node_key == "ip"))).scalar_one()
            dn = (await sess.execute(select(GraphNode).where(GraphNode.node_key == "dn"))).scalar_one()
            assert ip.status == "retry"   # crash-residue reset
            assert dn.status == "done"    # terminal preserved
    finally:
        await h2.close()


async def test_start_run_records_backup_path_and_redacted_settings(tmp_path):
    s = _settings(tmp_path)
    h1 = await open_fresh(_university(), "buaa", s)
    await h1.close()

    h2 = await open_fresh(_university(), "buaa", s)
    try:
        run_id = await h2.writer.start_run(
            mode="fresh",
            settings={"llm_model": "x"},
            backup_path=h2.backup_path,
        )
        async with h2.session() as sess:
            run = (await sess.execute(select(CrawlRun).where(CrawlRun.id == run_id))).scalar_one()
            assert run.mode == "fresh"
            assert run.settings_json == {"llm_model": "x"}
            assert run.backup_path == str(h2.backup_path)
    finally:
        await h2.close()
