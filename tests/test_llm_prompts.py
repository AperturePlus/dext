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


def test_extractor_strict_prompt_differs_from_default():
    ctx = type("O", (), {"org_unit_id": 1, "org_unit_name": "数学", "faculty_list_url": ""})()
    normal = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=False)
    strict = build_extractor_messages(_snap(), ctx, max_tokens=1000, strict=True)
    assert normal[0]["content"] != strict[0]["content"]


def test_save_professors_tool_schema_aligns_with_payload():
    fn = SAVE_PROFESSORS_TOOL["function"]
    assert fn["name"] == "save_professors"
    item = fn["parameters"]["properties"]["professors"]["items"]
    assert item["required"] == ["name"]
    payload_fields = {f.name for f in dataclasses.fields(ProfessorPayload)}
    assert set(item["properties"]) == payload_fields


def test_truncate_under_budget_unchanged_over_budget_shrinks():
    short = "hello world"
    assert truncate_to_budget(short, 1000) == short
    long_text = "word " * 5000
    out = truncate_to_budget(long_text, 50)
    assert 0 < len(out) < len(long_text)
    assert truncate_to_budget("", 50) == ""
