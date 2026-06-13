from sqlalchemy import select

from dext.storage.db import create_all, create_engine_for_path, make_session_factory
from dext.storage.models import Academician, ExtractionFailure, Professor, ProfessorAffiliation
from dext.storage.writer import DBWriter, OrgUnitSpec
from dext.types import ProfessorPayload


async def _setup(tmp_path):
    import asyncio

    eng = create_engine_for_path(tmp_path / "p.db")
    await create_all(eng)
    sf = make_session_factory(eng)
    w = DBWriter(sf)
    task = asyncio.create_task(w.run())
    math = await w.upsert_org_unit(OrgUnitSpec(name="数学学院", url="https://x/math"))
    phys = await w.upsert_org_unit(OrgUnitSpec(name="物理学院", url="https://x/phys"))
    return eng, sf, w, task, math, phys


async def _close(eng, w, task):
    await w.stop()
    await task
    await eng.dispose()


async def test_same_college_same_name_key_merges_and_fills(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    r1 = await w.save_professors([ProfessorPayload(name="张三", title="教授")], org_unit_id=math, org_unit_name="数学学院")
    r2 = await w.save_professors([ProfessorPayload(name="张三", email="z@x.edu")], org_unit_id=math, org_unit_name="数学学院")
    assert r1.inserted == 1
    assert r2.inserted == 0 and r2.updated == 1
    async with sf() as s:
        profs = (await s.execute(select(Professor))).scalars().all()
        assert len(profs) == 1
        assert profs[0].title == "教授" and profs[0].email == "z@x.edu"  # fields merged
    await _close(eng, w, task)


async def test_cross_college_email_match_adds_affiliation(tmp_path):
    eng, sf, w, task, math, phys = await _setup(tmp_path)
    await w.save_professors([ProfessorPayload(name="李四", email="l@x.edu")], org_unit_id=math, org_unit_name="数学学院")
    r = await w.save_professors([ProfessorPayload(name="李四", email="l@x.edu")], org_unit_id=phys, org_unit_name="物理学院")
    assert r.inserted == 0 and r.affiliations_added == 1
    async with sf() as s:
        assert len((await s.execute(select(Professor))).scalars().all()) == 1
        assert len((await s.execute(select(ProfessorAffiliation))).scalars().all()) == 2
    await _close(eng, w, task)


async def test_cross_college_homepage_match(tmp_path):
    eng, sf, w, task, math, phys = await _setup(tmp_path)
    await w.save_professors([ProfessorPayload(name="王五", homepage="https://x/wang")], org_unit_id=math, org_unit_name="数学学院")
    r = await w.save_professors([ProfessorPayload(name="王五", homepage="https://x/wang")], org_unit_id=phys, org_unit_name="物理学院")
    assert r.affiliations_added == 1
    async with sf() as s:
        assert len((await s.execute(select(Professor))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_distinct_people_create_separate_rows(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    r = await w.save_professors(
        [ProfessorPayload(name="赵六", email="a@x.edu"), ProfessorPayload(name="钱七", email="b@x.edu")],
        org_unit_id=math, org_unit_name="数学学院",
    )
    assert r.inserted == 2
    async with sf() as s:
        assert len((await s.execute(select(Professor))).scalars().all()) == 2
    await _close(eng, w, task)


async def test_academician_routed_to_academicians_table(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    r = await w.save_professors(
        [ProfessorPayload(name="孙院士", title="中国科学院院士")], org_unit_id=math, org_unit_name="数学学院"
    )
    assert r.academicians == 1 and r.inserted == 0
    async with sf() as s:
        assert len((await s.execute(select(Academician))).scalars().all()) == 1
        assert len((await s.execute(select(Professor))).scalars().all()) == 0
    await _close(eng, w, task)


async def test_academician_idempotent_on_repeat(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    await w.save_professors([ProfessorPayload(name="周院士", title="院士")], org_unit_id=math, org_unit_name="数学学院")
    r = await w.save_professors([ProfessorPayload(name="周院士", title="院士")], org_unit_id=math, org_unit_name="数学学院")
    assert r.academicians == 0  # (org_unit_id, name_key) already present
    async with sf() as s:
        assert len((await s.execute(select(Academician))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_repeated_save_is_idempotent_no_duplicate_affiliations(tmp_path):
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    p = ProfessorPayload(name="吴九", email="w@x.edu")
    await w.save_professors([p], org_unit_id=math, org_unit_name="数学学院")
    await w.save_professors([p], org_unit_id=math, org_unit_name="数学学院")
    async with sf() as s:
        assert len((await s.execute(select(ProfessorAffiliation))).scalars().all()) == 1
    await _close(eng, w, task)


async def test_save_error_isolation_records_failure_and_continues_batch(tmp_path):
    # Requirement 4 (per-record SAVEPOINT isolation): a bad payload rolls back ITS
    # savepoint, is recorded as a save_error, and the rest of the batch still persists.
    eng, sf, w, task, math, _ = await _setup(tmp_path)
    payloads = [
        ProfessorPayload(name="甲", email="jia@x.edu"),
        ProfessorPayload(name=None),  # name_key(None) raises inside its SAVEPOINT
        ProfessorPayload(name="乙", email="yi@x.edu"),
    ]
    r = await w.save_professors(payloads, org_unit_id=math, org_unit_name="数学学院")
    assert r.save_errors == 1
    assert r.inserted == 2  # the two good records survived the bad one
    async with sf() as s:
        profs = (await s.execute(select(Professor))).scalars().all()
        assert {p.name for p in profs} == {"甲", "乙"}
        fails = (await s.execute(select(ExtractionFailure))).scalars().all()
        assert len(fails) == 1 and fails[0].failure_type == "save_error"
    await _close(eng, w, task)
