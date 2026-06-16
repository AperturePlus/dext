import dataclasses

from dext.llm.prompts import (
    PROMPT_HASHES,
    SAVE_PROFESSORS_TOOL,
    build_decider_messages,
    build_extractor_messages,
    prompt_hash,
    truncate_to_budget,
)
from dext.page.links import LinkSignal, PageSnapshot
from dext.types import ProfessorPayload


def _snap(text="正文", url="https://x.edu.cn/p"):
    return PageSnapshot(url=url, final_url=url, title="标题", text_snapshot=text,
                        links=[], link_signals=[], content_hash="h")


def _sig(url, text="张三"):
    return LinkSignal(href=url, url=url, anchor_text=text, heading=None,
                      parent_class=None, path_segments=["teacher"], same_site=True)


def test_prompt_hash_is_deterministic_and_distinct():
    assert prompt_hash("decider") == prompt_hash("decider")
    assert prompt_hash("decider") != prompt_hash("extractor")
    assert len(prompt_hash("extractor")) == 16


def test_prompt_hashes_table_has_all_three():
    assert set(PROMPT_HASHES) == {"decider", "extractor", "extractor_retry"}


def test_decider_prompt_mentions_json_for_json_object_mode():
    msgs = build_decider_messages(
        _snap(), [_sig("https://x.edu.cn/teacher/1")],
        node=type("N", (), {"type": "faculty_list_url", "url": "u", "depth": 1, "org_unit_name": "数学"})(),
        context=type("C", (), {"university_name": "X大", "visited_summary": "", "faculty_list_url": "u"})(),
        max_tokens=1000,
    )
    assert msgs[0]["role"] == "system"
    assert "json" in msgs[0]["content"].lower()
    assert "https://x.edu.cn/teacher/1" in msgs[1]["content"]


def test_prompts_include_conservative_exclusion_rules():
    ctx = type("O", (), {"org_unit_id": 1, "org_unit_name": "数学", "faculty_list_url": ""})()
    decider = build_decider_messages(
        _snap(), [_sig("https://x.edu.cn/teacher/1")],
        node=type("N", (), {"type": "faculty_list_url", "url": "u", "depth": 1, "org_unit_name": "数学"})(),
        context=type("C", (), {"university_name": "X大", "visited_summary": "", "faculty_list_url": "u"})(),
        max_tokens=1000,
    )[0]["content"]
    extractor = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=False)[0]["content"]
    retry = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=True)[0]["content"]
    for prompt in (decider, extractor, retry):
        for term in (
            "中外合办",
            "联合办学",
            "艺术学院",
            "体育学院",
            "博士后",
            "离退休",
            "成人教育",
            "继续教育",
            "人事工作",
            "基教中心",
            "基础教学中心",
            "实验中心",
            "教辅岗",
            "行政岗",
            "专职行政",
            "行政人员",
            "行政团队",
            "学院（筹）",
            "筹建",
            "筹备",
            "卓越工程师学院",
            "书院",
            "吴玉章书院",
            "吴健雄书院",
            "行政法",
            "行政管理",
        ):
            assert term in prompt
        assert "宁可漏排除，不要错排除" in prompt
        assert "国际关系学院" in prompt
    assert "SCUPI" in decider
    assert "ltxjs" in decider
    assert "bshldz" in decider
    assert "行政团队等人员子类拆分" not in decider
    assert "行政团队必须标为 `noise`" in decider
    assert "“工程”“工程师”“国际”等泛词" in decider
    assert "正常招生学院" in decider
    assert "空的 professors 数组" in extractor


def test_extractor_strict_prompt_differs_from_default():
    ctx = type("O", (), {"org_unit_id": 1, "org_unit_name": "数学", "faculty_list_url": ""})()
    normal = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=False)
    strict = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=True)
    assert normal[0]["content"] != strict[0]["content"]


def test_extractor_prompt_separates_title_and_enrollment_pref():
    ctx = type("O", (), {"org_unit_id": 1, "org_unit_name": "数学", "faculty_list_url": ""})()
    for messages in (
        build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=False),
        build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=True),
    ):
        content = messages[0]["content"]
        assert "title" in content
        assert "enrollment_pref" in content
        assert "博导" in content
        assert "硕导" in content
        assert "博士生导师" in content
        assert "硕士生导师" in content
        assert "应填写到 `enrollment_pref`" in content


def test_extractor_prompt_describes_external_link_only():
    ctx = type("O", (), {"org_unit_id": 1, "org_unit_name": "数学", "faculty_list_url": ""})()
    for messages in (
        build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=False),
        build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=True),
    ):
        content = messages[0]["content"]
        assert "`external_link` 只填写外部/第三方主页" in content


def test_save_professors_tool_schema_excludes_system_homepage():
    fn = SAVE_PROFESSORS_TOOL["function"]
    assert fn["name"] == "save_professors"
    item = fn["parameters"]["properties"]["professors"]["items"]
    assert item["required"] == ["name"]
    payload_fields = {f.name for f in dataclasses.fields(ProfessorPayload)}
    assert "homepage" in payload_fields
    assert "homepage" not in item["properties"]
    assert set(item["properties"]) == payload_fields - {"homepage"}


def test_truncate_under_budget_unchanged_over_budget_shrinks():
    short = "hello world"
    assert truncate_to_budget(short, 1000) == short
    long_text = "word " * 5000
    out = truncate_to_budget(long_text, 50)
    assert 0 < len(out) < len(long_text)
    assert truncate_to_budget("", 50) == ""


def test_save_professors_tool_has_optional_exclusion_reason():
    props = SAVE_PROFESSORS_TOOL["function"]["parameters"]["properties"]
    assert "exclusion_reason" in props
    assert props["exclusion_reason"]["type"] == "string"
    # professors 仍是唯一必填项
    assert SAVE_PROFESSORS_TOOL["function"]["parameters"]["required"] == ["professors"]
