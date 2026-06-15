from dext.llm.extractor import OrgUnitContext, extract_professors
from dext.page.links import build_snapshot

_DETAIL_HTML = """
<html><head><title>张三 - 数学学院</title></head><body>
<h1>张三 教授</h1>
<p>职称：教授，博士生导师</p>
<p>研究方向：人工智能、机器学习</p>
<p>邮箱：zhangsan@x.edu.cn</p>
<p>个人简介：张三，2005年博士毕业，主要从事……教育经历……科研项目……</p>
</body></html>
"""


async def test_extract_real_detail_page_yields_payload(llm_client):
    snap = build_snapshot(_DETAIL_HTML, "https://x.edu.cn/teacher/info/1001",
                          "https://x.edu.cn/teacher/info/1001", "张三 - 数学学院")
    ctx = OrgUnitContext(org_unit_id=1, org_unit_name="数学学院")
    result = await extract_professors(snap, ctx, client=llm_client)
    assert result.failure_type is None
    assert len(result.payloads) >= 1
    assert result.payloads[0].name  # non-empty


async def test_extract_sparse_page_classifies_no_structured_data(llm_client):
    snap = build_snapshot("<html><body><p>欢迎访问本站首页。</p></body></html>",
                          "https://x.edu.cn/index.htm", "https://x.edu.cn/index.htm", "首页")
    ctx = OrgUnitContext(org_unit_id=1, org_unit_name="数学学院")
    result = await extract_professors(snap, ctx, client=llm_client)
    assert result.payloads == []
    assert result.failure_type == "no_structured_data"
    assert result.recoverable is False


async def test_strict_retry_path_runs(llm_client):
    snap = build_snapshot(_DETAIL_HTML, "https://x.edu.cn/teacher/info/1001",
                          "https://x.edu.cn/teacher/info/1001", "张三")
    ctx = OrgUnitContext(org_unit_id=1, org_unit_name="数学学院")
    result = await extract_professors(snap, ctx, client=llm_client, attempt=1)
    assert result.failure_type in (None, "invalid_json", "no_structured_data")
